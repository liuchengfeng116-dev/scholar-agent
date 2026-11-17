"""基于文本内容与向量语义的论文去重、版本比对与修订差异总结。"""

from __future__ import annotations

import difflib
import logging
import math
import re
from pathlib import Path

import pymupdf
from datasketch import MinHash
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Block, DuplicateRecord, Paper
from app.llm import client as llm

log = logging.getLogger(__name__)

_ARXIV_VER_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)
_FILE_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
_DATE_RE = re.compile(r"\b(\d{1,2}\s+[A-Z][a-z]{2}\s+20\d{2})\b")
_WORD_RE = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]+")

NUM_PERM = 128


def normalize_text(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def compute_minhash(text: str) -> list[int]:
    """用 5-gram 词片计算 MinHash 指纹，用于 O(1) 近似 Jaccard 初筛。"""
    tokens = _WORD_RE.findall((text or "").lower())
    m = MinHash(num_perm=NUM_PERM)
    if not tokens:
        return [int(x) for x in m.hashvalues]
    k = 5 if len(tokens) >= 5 else 1
    for i in range(max(1, len(tokens) - k + 1)):
        shingle = " ".join(tokens[i : i + k])
        m.update(shingle.encode("utf-8"))
    return [int(x) for x in m.hashvalues]


def minhash_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for a, b in zip(sig_a, sig_b, strict=False) if a == b)
    return round(matches / len(sig_a), 4)


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return round(dot / (na * nb), 4) if na and nb else 0.0


def extract_version_info(text: str, filename: str) -> dict:
    """从文本边距印记或文件名中提取 arXiv ID、版本号与提交日期。"""
    arxiv_id = ""
    version = ""
    pub_date = ""

    m = _ARXIV_VER_RE.search(text or "")
    if m:
        arxiv_id = m.group(1)
        version = m.group(2) or ""
        tail = (text or "")[m.end() : m.end() + 40]
        dm = _DATE_RE.search(tail)
        if dm:
            pub_date = dm.group(1)

    if not arxiv_id:
        fm = _FILE_ARXIV_RE.search(filename or "")
        if fm:
            arxiv_id = fm.group(1)
            version = fm.group(2) or ""

    ver_num = int(version[1:]) if version.startswith("v") and version[1:].isdigit() else 0
    return {"arxiv_id": arxiv_id, "version": version, "version_num": ver_num, "pub_date": pub_date}


def extract_quick_text(pdf_path: Path, max_pages: int = 5) -> str:
    """快速抽取用于去重比对的正文（扫描件自动降级用 VLM OCR 读第 1 页）。"""
    doc = pymupdf.open(pdf_path)
    try:
        n = len(doc)
        idxs = list(range(min(3, n)))
        if n > 4:
            idxs.extend(range(max(3, n - 2), n))
        text = "\n".join(doc[i].get_text("text") for i in idxs).strip()
        if len(text) < 80 and n > 0:
            # 无原生文本层的扫描 PDF，取第 1 页图像交由 OCR 模型识别，保证语义去重同样生效
            try:
                pix = doc[0].get_pixmap(dpi=130, alpha=False)
                text = llm.vision(
                    pix.tobytes("png"),
                    "请完整识别该学术页面中的标题、作者与正文文字，直接输出纯文本。",
                    model=settings.ocr_model,
                    max_tokens=2048,
                )
            except Exception:  # noqa: BLE001
                log.warning("扫描件去重 OCR 失败: %s", pdf_path.name)
        return text
    finally:
        doc.close()


def _full_pdf_text(pdf_path: Path, max_pages: int = 40) -> str:
    doc = pymupdf.open(pdf_path)
    try:
        return "\n".join(doc[i].get_text("text") for i in range(min(len(doc), max_pages)))
    finally:
        doc.close()


def _existing_paper_text(db: Session, paper: Paper) -> str:
    p = Path(paper.file_path)
    if p.exists():
        txt = _full_pdf_text(p)
        if len(txt.strip()) >= 80:
            return txt
    blocks = (
        db.query(Block)
        .filter(Block.paper_id == paper.id)
        .order_by(Block.page.asc(), Block.order.asc())
        .all()
    )
    return "\n".join(b.content for b in blocks if b.content)


def summarize_revision_diff(
    old_text: str,
    new_text: str,
    old_label: str,
    new_label: str,
) -> str:
    """提取两份文档之间的真实文本差异，并用 LLM 总结核心修订内容。"""
    lines_a = [ln.strip() for ln in old_text.splitlines() if ln.strip()]
    lines_b = [ln.strip() for ln in new_text.splitlines() if ln.strip()]
    added, removed = [], []
    for ln in difflib.unified_diff(lines_a, lines_b, n=0):
        if ln.startswith(("+++", "---", "@@")):
            continue
        if ln.startswith("+"):
            added.append(ln[1:])
        elif ln.startswith("-"):
            removed.append(ln[1:])

    if not added and not removed:
        return "两份文档正文内容完全一致，未发现实质性文本差异。"

    diff_snippet = (
        f"[{old_label} 中有而 {new_label} 中移除/改写的内容（节选）]:\n"
        + "\n".join(f"- {x}" for x in removed[:30])
        + f"\n\n[{new_label} 中新增/改写后的内容（节选）]:\n"
        + "\n".join(f"+ {x}" for x in added[:30])
    )
    prompt = (
        f"以下是同一篇论文两个版本（{old_label} 与 {new_label}）的文本行级差异。\n"
        "请用简洁中文分条总结两版之间的**主要实质性修订差异**"
        "（例如：实验指标/BLEU 分数更新、作者/机构脚注变化、版权声明、致谢或引用增删等），"
        "只总结差异中确实出现的内容，不要猜测：\n\n"
        f"{diff_snippet[:5000]}"
    )
    try:
        return llm.complete(prompt, model=settings.llm_model_fast, temperature=0.1)
    except Exception:  # noqa: BLE001
        return f"共检测到 {len(removed)} 处删改、{len(added)} 处新增（详见版本对比）。"


def detect_duplicate(
    db: Session,
    user_id: int,
    new_paper: Paper,
    pdf_path: Path,
) -> DuplicateRecord | None:
    """基于语义内容与指纹检测重复论文，并生成版本建议与修订差异总结。"""
    quick_text = extract_quick_text(pdf_path)
    if len(quick_text.strip()) < 40:
        return None

    new_sig = compute_minhash(quick_text)
    new_paper.fingerprint = new_sig
    ver_info = extract_version_info(quick_text, new_paper.filename)
    if ver_info["arxiv_id"] and not new_paper.arxiv_id:
        new_paper.arxiv_id = ver_info["arxiv_id"]
    if ver_info["version"] and not new_paper.version:
        new_paper.version = ver_info["version"]
    if ver_info["pub_date"] and not new_paper.pub_date:
        new_paper.pub_date = ver_info["pub_date"]

    # 第一行大概率为标题或声明，取前几行做标题模糊辅助匹配
    first_lines = " ".join(quick_text.splitlines()[:4])[:220]

    candidates = (
        db.query(Paper)
        .filter(Paper.owner_id == user_id, Paper.id != new_paper.id, Paper.status == "ready")
        .all()
    )
    if not candidates:
        db.commit()
        return None

    best_paper: Paper | None = None
    best_text_sim = 0.0
    best_sem_sim = 0.0

    # 仅对 MinHash / 标题 / arXiv ID 疑似重合的候选调用 Embedding，降低成本与延迟
    shortlist: list[tuple[Paper, float, bool]] = []
    for cand in candidates:
        jac = minhash_jaccard(new_sig, cand.fingerprint or [])
        same_arxiv = bool(new_paper.arxiv_id and cand.arxiv_id and new_paper.arxiv_id == cand.arxiv_id)
        title_hit = bool(cand.title and fuzz.partial_ratio(cand.title.lower(), first_lines.lower()) >= 85)
        if jac >= 0.25 or same_arxiv or title_hit:
            shortlist.append((cand, jac, same_arxiv))

    if not shortlist:
        db.commit()
        return None

    # 批量计算语义向量相似度
    new_excerpt = quick_text[:1800]
    cand_excerpts: list[str] = []
    for cand, _, _ in shortlist:
        ctext = _existing_paper_text(db, cand)[:1800] or f"{cand.title}\n{cand.abstract}"
        cand_excerpts.append(ctext)

    sem_scores: list[float] = [0.0] * len(shortlist)
    try:
        vecs = llm.embed([new_excerpt] + cand_excerpts)
        q_vec = vecs[0]
        sem_scores = [cosine_sim(q_vec, v) for v in vecs[1:]]
    except Exception:  # noqa: BLE001
        log.warning("语义去重向量计算失败，回退为文本指纹对比")

    for (cand, jac, same_arxiv), sem in zip(shortlist, sem_scores, strict=False):
        score = max(jac, sem) + (0.2 if same_arxiv else 0.0)
        best_score = max(best_text_sim, best_sem_sim)
        if score > best_score and (jac >= 0.40 or sem >= 0.84 or same_arxiv):
            best_paper = cand
            best_text_sim = jac
            best_sem_sim = sem

    if not best_paper:
        db.commit()
        return None

    # 判定版本新旧与建议
    old_v = int(best_paper.version[1:]) if best_paper.version.startswith("v") and best_paper.version[1:].isdigit() else 0
    new_v = ver_info["version_num"]

    if old_v and new_v:
        if old_v > new_v:
            suggestion = "keep_existing"
            reason = (
                f"库中已有更新版本 {best_paper.version}（{best_paper.pub_date or '较新'}），"
                f"本次上传为较旧版本 {new_paper.version}（{new_paper.pub_date or '较早'}），建议保留现有版本。"
            )
        elif new_v > old_v:
            suggestion = "overwrite"
            reason = (
                f"本次上传版本 {new_paper.version} 比库中版本 {best_paper.version} 更新，"
                "建议用新版覆盖现有文献。"
            )
        else:
            suggestion = "keep_existing"
            reason = f"两份文件版本一致（均为 {best_paper.version}），内容高度重合，建议保留现有版本。"
    else:
        suggestion = "keep_existing"
        reason = "检测到正文语义内容高度重合，建议保留库中已解析版本或选择覆盖。"

    old_full = _existing_paper_text(db, best_paper)
    new_full = _full_pdf_text(pdf_path) or quick_text
    old_label = f"库中版本({best_paper.version or best_paper.filename})"
    new_label = f"新上传版本({new_paper.version or new_paper.filename})"
    diff_summary = summarize_revision_diff(old_full, new_full, old_label, new_label)

    rec = DuplicateRecord(
        new_paper_id=new_paper.id,
        existing_paper_id=best_paper.id,
        text_similarity=round(best_text_sim, 4),
        semantic_similarity=round(best_sem_sim, 4),
        suggestion=suggestion,
        suggestion_reason=reason,
        diff_summary=diff_summary,
        decision="pending",
    )
    new_paper.status = "duplicate_pending"
    new_paper.status_msg = "检测到高度重合论文，等待版本处理决策"
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec
