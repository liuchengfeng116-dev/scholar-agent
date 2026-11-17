"""模块四核心服务：论文检索下钻、引用含金量筛选、Sickle arXiv 动态追踪与个性化推荐。"""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import arxiv
import httpx
from sickle import Sickle
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ArxivPaper, Paper, Recommendation, Reference, User
from app.llm import client as llm

log = logging.getLogger(__name__)

_GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.I)
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")

_TOP_VENUES = (
    "neurips", "nips", "icml", "iclr", "acl", "emnlp", "naacl", "cvpr",
    "iccv", "eccv", "aaai", "ijcai", "nature", "science", "ieee", "acm",
    "jmlr", "tpami", "transactions",
)


def _headers_s2() -> dict[str, str]:
    h = {"User-Agent": "ScholarAgent/0.1"}
    if settings.semantic_scholar_api_key:
        h["x-api-key"] = settings.semantic_scholar_api_key
    return h


def _headers_cr() -> dict[str, str]:
    ua = "ScholarAgent/0.1"
    if settings.crossref_mailto:
        ua += f" (mailto:{settings.crossref_mailto})"
    return {"User-Agent": ua}


# ----------------------------------------------------------------------------
# 1. 论文查找与 PDF 自动下钻抓取
# ----------------------------------------------------------------------------
def search_external_papers(query: str, limit: int = 8) -> list[dict]:
    """联合 Semantic Scholar + OpenAlex + arXiv 搜索真实论文及开放 PDF 链接。"""
    results: list[dict] = []
    seen_titles: set[str] = set()

    # 1) Semantic Scholar
    try:
        r = httpx.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query,
                "limit": limit,
                "fields": "paperId,title,authors,year,venue,citationCount,influentialCitationCount,externalIds,openAccessPdf,abstract",
            },
            headers=_headers_s2(),
            timeout=15,
        )
        if r.status_code == 200:
            for item in r.json().get("data") or []:
                t = (item.get("title") or "").strip()
                if not t or t.lower() in seen_titles:
                    continue
                seen_titles.add(t.lower())
                ext = item.get("externalIds") or {}
                oa_pdf = (item.get("openAccessPdf") or {}).get("url") or ""
                aid = ext.get("ArXiv") or ""
                if not oa_pdf and aid:
                    oa_pdf = f"https://arxiv.org/pdf/{aid}.pdf"
                results.append(
                    {
                        "title": t,
                        "authors": ", ".join(a.get("name", "") for a in (item.get("authors") or [])[:6]),
                        "year": item.get("year"),
                        "venue": item.get("venue") or "",
                        "doi": ext.get("DOI") or "",
                        "arxiv_id": aid,
                        "citations": item.get("citationCount") or 0,
                        "influential_citations": item.get("influentialCitationCount") or 0,
                        "pdf_url": oa_pdf,
                        "abstract": item.get("abstract") or "",
                        "source": "SemanticScholar",
                    }
                )
    except Exception:  # noqa: BLE001
        log.debug("Semantic Scholar 搜索超时或限流")

    # 2) OpenAlex（免费无严苛限流，补充覆盖）
    if len(results) < limit:
        try:
            r = httpx.get(
                "https://api.openalex.org/works",
                params={"search": query, "per-page": limit},
                timeout=15,
            )
            if r.status_code == 200:
                for w in r.json().get("results") or []:
                    t = (w.get("title") or "").strip()
                    if not t or t.lower() in seen_titles:
                        continue
                    seen_titles.add(t.lower())
                    oa_url = (w.get("open_access") or {}).get("oa_url") or ""
                    loc = w.get("primary_location") or {}
                    pdf_url = loc.get("pdf_url") or (oa_url if ".pdf" in oa_url else "")
                    doi = (w.get("doi") or "").replace("https://doi.org/", "")
                    aid_m = _ARXIV_ID_RE.search(str(w.get("ids") or "") + " " + pdf_url)
                    aid = aid_m.group(1) if aid_m else ""
                    if not pdf_url and aid:
                        pdf_url = f"https://arxiv.org/pdf/{aid}.pdf"
                    results.append(
                        {
                            "title": t,
                            "authors": ", ".join(
                                (a.get("author") or {}).get("display_name", "")
                                for a in (w.get("authorships") or [])[:6]
                            ),
                            "year": w.get("publication_year"),
                            "venue": ((loc.get("source") or {}).get("display_name")) or "",
                            "doi": doi,
                            "arxiv_id": aid,
                            "citations": w.get("cited_by_count") or 0,
                            "influential_citations": 0,
                            "pdf_url": pdf_url,
                            "abstract": "",
                            "source": "OpenAlex",
                        }
                    )
        except Exception:  # noqa: BLE001
            log.debug("OpenAlex 搜索异常")

    # 3) arXiv API（兜底确保预印本与 PDF 可下载）
    if len(results) < max(3, limit // 2):
        try:
            search = arxiv.Search(query=query, max_results=min(6, limit))
            client = arxiv.Client(page_size=6, delay_seconds=1.0, num_retries=2)
            for res in client.results(search):
                t = res.title.strip().replace("\n", " ")
                if t.lower() in seen_titles:
                    continue
                seen_titles.add(t.lower())
                aid_m = _ARXIV_ID_RE.search(res.entry_id)
                aid = aid_m.group(1) if aid_m else ""
                results.append(
                    {
                        "title": t,
                        "authors": ", ".join(a.name for a in res.authors[:6]),
                        "year": res.published.year if res.published else None,
                        "venue": "arXiv",
                        "doi": res.doi or "",
                        "arxiv_id": aid,
                        "citations": 0,
                        "influential_citations": 0,
                        "pdf_url": res.pdf_url or (f"https://arxiv.org/pdf/{aid}.pdf" if aid else ""),
                        "abstract": res.summary.replace("\n", " "),
                        "source": "arXiv",
                    }
                )
        except Exception:  # noqa: BLE001
            log.debug("arXiv 搜索异常")

    return results[:limit]


def download_pdf_bytes(pdf_url: str, arxiv_id: str = "") -> tuple[bytes, str] | None:
    """下载 PDF 二进制内容，校验是否为真实 PDF（%PDF 头）。"""
    urls = []
    if pdf_url:
        urls.append(pdf_url)
    if arxiv_id:
        urls.append(f"https://arxiv.org/pdf/{arxiv_id}.pdf")
    for u in urls:
        try:
            r = httpx.get(u, follow_redirects=True, timeout=40, headers={"User-Agent": "ScholarAgent/0.1"})
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                fname = (arxiv_id or u.rstrip("/").split("/")[-1] or "downloaded").replace(".pdf", "") + ".pdf"
                return r.content, fname
        except Exception:  # noqa: BLE001
            continue
    return None


# ----------------------------------------------------------------------------
# 2. 「引用含金量」筛选机制（自动剔除应付式引用）
# ----------------------------------------------------------------------------
def _heuristic_ref_quality(ref: Reference, ext_meta: dict | None) -> tuple[float, str, str]:
    """结合正文引用语境、频次、会议影响力与被引次数计算引用含金量（0-100）。"""
    ctxs = ref.cite_contexts or []
    cite_count = len(ctxs)

    # 检测是否只在 [1, 2, 3, 4] 大段凑数罗列中出现
    perfunctory_hits = 0
    substantive_hits = 0
    for c in ctxs:
        snip = (c.get("snippet") or "").lower()
        if re.search(r"\[\d+(?:\s*,\s*\d+){2,}\]|\[\d+\s*[-–]\s*\d+\]", snip):
            perfunctory_hits += 1
        if any(
            kw in snip
            for kw in (
                "follow", "based on", "similar to", "unlike", "compare", "outperform",
                "table", "baseline", "architecture", "optimizer", "proposed by", "extend",
            )
        ):
            substantive_hits += 1

    score = 35.0
    score += min(25.0, cite_count * 7.0)
    score += min(20.0, substantive_hits * 10.0)
    score -= min(18.0, perfunctory_hits * 6.0)

    venue_low = (ref.venue or (ext_meta or {}).get("venue") or "").lower()
    if any(v in venue_low for v in _TOP_VENUES):
        score += 15.0

    cites = (ext_meta or {}).get("citations") or 0
    if cites > 0:
        score += min(20.0, math.log10(cites + 1) * 5.0)
    inf_cites = (ext_meta or {}).get("influential_citations") or 0
    if inf_cites >= 20:
        score += 8.0

    score = round(max(0.0, min(100.0, score)), 1)
    if substantive_hits >= 1 or cite_count >= 2 or score >= 62:
        label = "core"
        reason = f"正文实质性/多次引用({cite_count}次)" + (f"，被引 {cites} 次" if cites else "")
    elif perfunctory_hits > 0 and substantive_hits == 0 and cite_count <= 1:
        label = "perfunctory"
        reason = "仅在背景罗列中被顺带打包引用，属低含金量应付式引用"
    else:
        label = "normal"
        reason = f"常规参考引用（含金量评分 {score}）"
    return score, label, reason


def evaluate_references_quality(db: Session, paper_id: int) -> list[dict]:
    """评估某篇论文全部参考文献的「引用含金量」，并落库保存结果。"""
    refs = db.query(Reference).filter(Reference.paper_id == paper_id).order_by(Reference.idx.asc()).all()
    if not refs:
        return []

    # 对前 25 条做轻量外部检索补充被引次数（并行加速）
    ext_by_idx: dict[int, dict] = {}

    def _lookup(r: Reference) -> tuple[int, dict | None]:
        if not r.title or len(r.title) < 10:
            return r.idx, None
        hits = search_external_papers(r.title[:140], limit=1)
        return r.idx, (hits[0] if hits else None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for idx, meta in pool.map(_lookup, refs[:25]):
            if meta:
                ext_by_idx[idx] = meta

    results = []
    for r in refs:
        meta = ext_by_idx.get(r.idx)
        score, label, reason = _heuristic_ref_quality(r, meta)
        qobj = {
            "score": score,
            "label": label,  # core | normal | perfunctory
            "reason": reason,
            "citations": (meta or {}).get("citations", 0),
            "pdf_url": (meta or {}).get("pdf_url", ""),
            "arxiv_id": r.arxiv_id or (meta or {}).get("arxiv_id", ""),
        }
        r.quality = qobj
        results.append(
            {
                "id": r.id,
                "idx": r.idx,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "raw_text": r.raw_text,
                **qobj,
            }
        )
    db.commit()
    return results


# ----------------------------------------------------------------------------
# 3. Sickle (OAI-PMH) arXiv 动态追踪与个性化推荐（含代码优先 + 多用户协同）
# ----------------------------------------------------------------------------
def _extract_github_url(text: str) -> str:
    m = _GITHUB_RE.search(text or "")
    return m.group(0).rstrip(".,);]") if m else ""


def track_arxiv_with_sickle(
    db: Session,
    *,
    categories: list[str] | None = None,
    days: int = 4,
    limit_per_cat: int = 25,
) -> int:
    """使用 Sickle (OAI-PMH) 定期抓取 arXiv 最新论文元数据，失败时用 arXiv Atom API 补充。"""
    cats = categories or [c.strip() for c in settings.arxiv_track_categories.split(",") if c.strip()]
    from_date = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    added = 0

    try:
        sickle = Sickle("https://export.arxiv.org/oai2", max_retries=2, timeout=25)
        for cat in cats:
            set_spec = "cs" if cat.startswith("cs.") else cat.split(".")[0]
            try:
                recs = sickle.ListRecords(
                    metadataPrefix="arXiv",
                    **{"from": from_date, "set": set_spec},
                    ignore_deleted=True,
                )
                count = 0
                for rec in recs:
                    meta = rec.metadata
                    aid = ((meta.get("id") or [""])[0]).strip()
                    if not aid:
                        continue
                    rec_cats = " ".join(x for x in (meta.get("categories") or []) if x)
                    if cat not in rec_cats and count > 5:
                        continue
                    title = " ".join(((meta.get("title") or [""])[0] or "").split())
                    abstract = " ".join(((meta.get("abstract") or [""])[0] or "").split())
                    comments = " ".join(((meta.get("comments") or [""])[0] or "").split())
                    authors = ", ".join(x for x in (meta.get("authors") or meta.get("author") or [])[:8] if x)
                    created = ((meta.get("created") or [""])[0] or "").strip()
                    gh = _extract_github_url(abstract + " " + comments)
                    if not db.get(ArxivPaper, aid):
                        db.add(
                            ArxivPaper(
                                arxiv_id=aid,
                                title=title[:500],
                                authors=authors,
                                abstract=abstract,
                                categories=rec_cats[:120],
                                comments=comments,
                                published=created,
                                github_url=gh,
                            )
                        )
                        added += 1
                    count += 1
                    if count >= limit_per_cat:
                        break
            except Exception as e:  # noqa: BLE001
                log.warning("Sickle 抓取分类 %s 跳过: %s", cat, e)
    except Exception as e:  # noqa: BLE001
        log.warning("Sickle OAI-PMH 初始化失败，改用 arXiv Atom 接口补充: %s", e)

    # 若 OAI-PMH 窗口内恰好无新数据或限流，用 arXiv Search 抓取最新提交作补充保障
    if added < 8:
        client = arxiv.Client(page_size=15, delay_seconds=1.0, num_retries=2)
        for cat in cats[:3]:
            try:
                s = arxiv.Search(
                    query=f"cat:{cat}",
                    max_results=12,
                    sort_by=arxiv.SortCriterion.SubmittedDate,
                )
                for res in client.results(s):
                    m = _ARXIV_ID_RE.search(res.entry_id)
                    if not m:
                        continue
                    aid = m.group(1)
                    if db.get(ArxivPaper, aid):
                        continue
                    comments = res.comment or ""
                    abstract = res.summary.replace("\n", " ")
                    gh = _extract_github_url(abstract + " " + comments)
                    db.add(
                        ArxivPaper(
                            arxiv_id=aid,
                            title=res.title.replace("\n", " ")[:500],
                            authors=", ".join(a.name for a in res.authors[:8]),
                            abstract=abstract,
                            categories=" ".join(res.categories)[:120],
                            comments=comments,
                            published=res.published.strftime("%Y-%m-%d") if res.published else "",
                            github_url=gh,
                        )
                    )
                    added += 1
            except Exception:  # noqa: BLE001
                continue

    db.commit()
    return added


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _collaborative_boost(db: Session, user_id: int) -> dict[str, float]:
    """多用户协同过滤拓展：找出与当前用户点赞交集高的相似学者，汇总其点赞论文加权。"""
    my_likes = {
        r.arxiv_id
        for r in db.query(Recommendation).filter(Recommendation.user_id == user_id, Recommendation.feedback == 1)
    }
    other_recs = (
        db.query(Recommendation)
        .filter(Recommendation.user_id != user_id, Recommendation.feedback == 1)
        .all()
    )
    by_user: dict[int, set[str]] = {}
    for r in other_recs:
        by_user.setdefault(r.user_id, set()).add(r.arxiv_id)

    boost: dict[str, float] = {}
    for _uid, their_likes in by_user.items():
        if not my_likes:
            sim = 0.25  # 冷启动期吸收群体热门高质量论文
        else:
            inter = len(my_likes & their_likes)
            union = len(my_likes | their_likes)
            sim = inter / union if union else 0.0
        if sim <= 0:
            continue
        for aid in their_likes - my_likes:
            boost[aid] = boost.get(aid, 0.0) + sim * 0.25
    return boost


def generate_recommendations(db: Session, user_id: int, top_n: int = 10) -> list[dict]:
    """结合本地文献库 + 兴趣关键词 + GitHub开源代码加权 + 点赞踩反馈 + 多用户协同 生成推荐列表。"""
    candidates = db.query(ArxivPaper).order_by(ArxivPaper.published.desc()).limit(60).all()
    if not candidates:
        track_arxiv_with_sickle(db)
        candidates = db.query(ArxivPaper).order_by(ArxivPaper.published.desc()).limit(60).all()
    if not candidates:
        return []

    user = db.get(User, user_id)
    my_papers = db.query(Paper).filter(Paper.owner_id == user_id, Paper.status == "ready").all()
    existing_recs = {
        r.arxiv_id: r for r in db.query(Recommendation).filter(Recommendation.user_id == user_id).all()
    }

    # 构建用户兴趣画像文本（本地库标题摘要 + 个人兴趣关键词 + 历史点赞论文）
    kws = ((user.interests or {}).get("keywords") or []) if user else []
    liked_titles = [r.title for r in existing_recs.values() if r.feedback == 1][:6]
    disliked_ids = {r.arxiv_id for r in existing_recs.values() if r.feedback == -1}
    profile_parts = (
        [f"Research keywords: {', '.join(kws)}"] if kws else []
    ) + [f"{p.title}. {(p.abstract or '')[:300]}" for p in my_papers[:8]] + liked_titles
    profile_text = "\n".join(profile_parts) or "Large Language Models, AI Agent, Retrieval-Augmented Generation, Transformer"

    cand_list = [c for c in candidates if c.arxiv_id not in disliked_ids][:35]
    if not cand_list:
        return []

    try:
        vecs = llm.embed([profile_text[:2500]] + [f"{c.title}. {c.abstract[:600]}" for c in cand_list])
        p_vec, c_vecs = vecs[0], vecs[1:]
        sims = [_cosine(p_vec, cv) for cv in c_vecs]
    except Exception:  # noqa: BLE001
        # 词重合回退
        ptoks = set(re.findall(r"[a-z]+", profile_text.lower()))
        sims = [
            len(ptoks & set(re.findall(r"[a-z]+", f"{c.title} {c.abstract}".lower()))) / max(1, len(ptoks))
            for c in cand_list
        ]

    cf_boost = _collaborative_boost(db, user_id)

    scored: list[tuple[ArxivPaper, float, dict]] = []
    for c, sim in zip(cand_list, sims, strict=False):
        code_bonus = 0.18 if c.github_url else 0.0
        cf = cf_boost.get(c.arxiv_id, 0.0)
        total = round(sim + code_bonus + cf, 4)
        detail = {
            "semantic_sim": round(sim, 3),
            "code_bonus": code_bonus,
            "collaborative_bonus": round(cf, 3),
        }
        scored.append((c, total, detail))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_items = scored[:top_n]

    # 为尚未生成推荐理由的 Top 论文批量生成理由
    need_reason = [c for c, _, _ in top_items if c.arxiv_id not in existing_recs or not existing_recs[c.arxiv_id].reason]
    reasons_map: dict[str, str] = {}
    if need_reason:
        brief = "\n".join(
            f"- {c.arxiv_id}: 《{c.title}》 (开源代码: {'有 ' + c.github_url if c.github_url else '暂无'}) | 摘要: {c.abstract[:220]}"
            for c in need_reason[:10]
        )
        try:
            res = llm.chat_json(
                f"用户研究兴趣：{profile_text[:600]}\n请为以下候选论文各写一句简明中文推荐理由（突出与用户库的相关性及是否含开源易复现代码）：\n{brief}\n"
                '返回 JSON：{"reasons": {"arxiv_id": "推荐理由"}}',
                model=settings.llm_model_fast,
                temperature=0.2,
            )
            reasons_map = res.get("reasons") or {}
        except Exception:  # noqa: BLE001
            pass

    out = []
    for c, score, detail in top_items:
        rec = existing_recs.get(c.arxiv_id)
        reason = reasons_map.get(c.arxiv_id) or (rec.reason if rec else "") or (
            ("附带开源 GitHub 代码（易复现），" if c.github_url else "") + f"与您的研究库语义相似度 {detail['semantic_sim']}"
        )
        if not rec:
            rec = Recommendation(
                user_id=user_id,
                arxiv_id=c.arxiv_id,
                title=c.title,
                authors=c.authors,
                abstract=c.abstract,
                categories=c.categories,
                published=c.published,
                github_url=c.github_url,
                score=score,
                score_detail=detail,
                reason=reason,
            )
            db.add(rec)
            db.flush()
        else:
            rec.score = score
            rec.score_detail = detail
            rec.reason = reason
        out.append(
            {
                "id": rec.id,
                "arxiv_id": rec.arxiv_id,
                "title": rec.title,
                "authors": rec.authors,
                "abstract": rec.abstract,
                "categories": rec.categories,
                "published": rec.published,
                "github_url": rec.github_url,
                "has_code": bool(rec.github_url),
                "score": rec.score,
                "score_detail": rec.score_detail,
                "reason": rec.reason,
                "feedback": rec.feedback,
            }
        )
    db.commit()
    return out
