"""模块四 API：论文自动检索下钻入库、引用含金量筛选、Sickle arXiv 追踪与个性化推荐。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import memory as mem_svc
from app.api.deps import get_current_user
from app.api.papers import ingest_pdf_bytes
from app.db.models import Paper, Recommendation, Reference, User
from app.db.session import SessionLocal, get_db
from app.scout import service as scout_svc
from app.tasks import runner

router = APIRouter(tags=["检索追踪与智能推荐"])


class FetchReq(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=15)
    skip_dedup: bool = False


class FetchRefsReq(BaseModel):
    paper_id: int
    only_high_quality: bool = True
    limit: int = Field(default=5, ge=1, le=15)


class TrackReq(BaseModel):
    categories: list[str] = Field(default_factory=list)
    days: int = Field(default=5, ge=1, le=30)


class FeedbackReq(BaseModel):
    feedback: int = Field(ge=-1, le=1)  # 1=赞, -1=踩, 0=重置


@router.get("/scout/search")
def search_papers_external(
    q: str = Query(min_length=2),
    limit: int = Query(default=8, ge=1, le=20),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return scout_svc.search_external_papers(q, limit=limit)


@router.post("/scout/fetch")
def fetch_and_ingest(
    req: FetchReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """给定论文名称/arXiv ID 列表，自动检索下载 PDF 并入库解析。"""
    imported = []
    failed = []
    for q in req.queries:
        hits = scout_svc.search_external_papers(q.strip(), limit=3)
        downloaded = None
        picked = None
        for h in hits:
            if h.get("pdf_url") or h.get("arxiv_id"):
                res = scout_svc.download_pdf_bytes(h.get("pdf_url", ""), h.get("arxiv_id", ""))
                if res:
                    downloaded = res
                    picked = h
                    break
        if not downloaded or not picked:
            failed.append({"query": q, "reason": "未找到可开放下载的 PDF"})
            continue
        raw, fname = downloaded
        pdict, tid = ingest_pdf_bytes(
            db, user.id, fname, raw, source="scout", skip_dedup=req.skip_dedup
        )
        # 补充外部检索得到的年份、会议、引用数等元数据
        row = db.get(Paper, pdict["id"])
        if row:
            if not row.year and picked.get("year"):
                row.year = picked["year"]
            if not row.venue and picked.get("venue"):
                row.venue = picked["venue"]
            if not row.doi and picked.get("doi"):
                row.doi = picked["doi"]
            if not row.arxiv_id and picked.get("arxiv_id"):
                row.arxiv_id = picked["arxiv_id"]
            db.commit()
        imported.append({"query": q, "paper": pdict, "task_id": tid})
    return {"imported": imported, "failed": failed}


@router.get("/scout/references/{paper_id}/quality")
def get_references_quality(
    paper_id: int,
    refresh: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """评估论文参考文献的「引用含金量」，标注核心引用/常规引用/应付式引用。"""
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")
    refs = db.query(Reference).filter(Reference.paper_id == paper_id).order_by(Reference.idx.asc()).all()
    if refresh or any(not r.quality for r in refs[:5]):
        items = scout_svc.evaluate_references_quality(db, paper_id)
    else:
        items = [
            {
                "id": r.id,
                "idx": r.idx,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "raw_text": r.raw_text,
                **(r.quality or {"score": 40.0, "label": "normal", "reason": "待评估"}),
            }
            for r in refs
        ]
    perfunctory_count = sum(1 for it in items if it.get("label") == "perfunctory")
    core_count = sum(1 for it in items if it.get("label") == "core")
    return {
        "paper_id": paper_id,
        "total": len(items),
        "core_count": core_count,
        "perfunctory_count": perfunctory_count,
        "items": items,
    }


@router.post("/scout/fetch-references")
def fetch_high_value_references(
    req: FetchRefsReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """智能下钻下载：先按「引用含金量」过滤应付式引用，再将高价值参考文献自动抓取入库。"""
    p = db.query(Paper).filter(Paper.id == req.paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")

    items = scout_svc.evaluate_references_quality(db, req.paper_id)
    filtered = [
        it for it in items if (not req.only_high_quality or it.get("label") != "perfunctory")
    ]
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
    targets = filtered[: req.limit]

    def _job(task_id: int) -> dict:
        ok, skipped = [], []
        with SessionLocal() as s:
            for i, it in enumerate(targets, start=1):
                runner.update_task(
                    task_id,
                    progress=round((i / max(1, len(targets))) * 95, 1),
                    message=f"正在下钻抓取 [{it['idx']}] {it['title'][:40]}",
                )
                res = scout_svc.download_pdf_bytes(it.get("pdf_url", ""), it.get("arxiv_id", ""))
                if not res:
                    hits = scout_svc.search_external_papers(it.get("title") or it["raw_text"][:100], limit=2)
                    for h in hits:
                        res = scout_svc.download_pdf_bytes(h.get("pdf_url", ""), h.get("arxiv_id", ""))
                        if res:
                            break
                if not res:
                    skipped.append({"idx": it["idx"], "title": it["title"], "reason": "无开放 PDF"})
                    continue
                raw, fname = res
                pdict, tid = ingest_pdf_bytes(s, user.id, fname, raw, source="drilldown")
                ok.append({"idx": it["idx"], "paper_id": pdict["id"], "task_id": tid})
        return {"imported": ok, "skipped": skipped, "filtered_perfunctory": len(items) - len(filtered)}

    task_id = runner.submit("drilldown_refs", user.id, _job, payload={"paper_id": req.paper_id})
    return {
        "task_id": task_id,
        "selected_count": len(targets),
        "filtered_perfunctory_count": sum(1 for it in items if it.get("label") == "perfunctory"),
    }


@router.post("/scout/track")
def trigger_arxiv_track(
    req: TrackReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """触发 Sickle OAI-PMH 刷新最新 arXiv 论文，并重新计算个性化推荐。"""
    cats = req.categories or (user.interests or {}).get("categories") or None
    added = scout_svc.track_arxiv_with_sickle(db, categories=cats, days=req.days)
    recs = scout_svc.generate_recommendations(db, user.id, top_n=10)
    return {"fetched_new": added, "recommendations": recs}


@router.get("/recommend")
def get_recommendations(
    refresh: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if not refresh:
        rows = (
            db.query(Recommendation)
            .filter(Recommendation.user_id == user.id, Recommendation.feedback >= 0)
            .order_by(Recommendation.score.desc())
            .limit(12)
            .all()
        )
        if rows:
            return [
                {
                    "id": r.id,
                    "arxiv_id": r.arxiv_id,
                    "title": r.title,
                    "authors": r.authors,
                    "abstract": r.abstract,
                    "categories": r.categories,
                    "published": r.published,
                    "github_url": r.github_url,
                    "has_code": bool(r.github_url),
                    "score": r.score,
                    "score_detail": r.score_detail,
                    "reason": r.reason,
                    "feedback": r.feedback,
                }
                for r in rows
            ]
    return scout_svc.generate_recommendations(db, user.id, top_n=10)


@router.post("/recommend/{rec_id}/feedback")
def submit_feedback(
    rec_id: int,
    req: FeedbackReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """点赞 / 踩互动：实时记录反馈并沉淀到用户长期 Memory 画像。"""
    rec = (
        db.query(Recommendation)
        .filter(Recommendation.id == rec_id, Recommendation.user_id == user.id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="推荐条目不存在")
    rec.feedback = req.feedback
    db.commit()

    if req.feedback == 1:
        mem_svc.save_memory(db, user.id, "preference", f"感兴趣论文：{rec.title[:120]}", weight=1.5)
    elif req.feedback == -1:
        mem_svc.save_memory(db, user.id, "preference", f"不感兴趣方向：{rec.title[:100]}", weight=0.8)

    return {"id": rec.id, "feedback": rec.feedback}
