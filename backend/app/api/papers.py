"""模块一 API：PDF 文献上传、语义去重决策、细粒度结构查询与 CRUD。"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import Block, DuplicateRecord, Paper, Reference, Section, User
from app.db.session import SessionLocal, get_db
from app.dedup import service as dedup_svc
from app.parsing.detector import inspect_pdf
from app.parsing.pipeline import parse_paper
from app.tasks import runner

router = APIRouter(prefix="/papers", tags=["文献管理与解析"])

# 删除文献时清理向量索引的可选回调（阶段3注册）
_delete_index_hook = None


def register_delete_index_hook(fn) -> None:
    global _delete_index_hook
    _delete_index_hook = fn


class PaperUpdateReq(BaseModel):
    title: str | None = None
    venue: str | None = None
    year: int | None = None
    tags: list[str] | None = None
    note: str | None = None
    github_url: str | None = None


class DedupDecisionReq(BaseModel):
    decision: str  # keep_existing | overwrite | keep_both


def _paper_dict(p: Paper, dup: DuplicateRecord | None = None, existing: Paper | None = None) -> dict:
    d = {
        "id": p.id,
        "title": p.title or p.filename,
        "filename": p.filename,
        "authors": p.authors or [],
        "affiliations": p.affiliations or [],
        "abstract": p.abstract or "",
        "keywords": p.keywords or [],
        "arxiv_id": p.arxiv_id or "",
        "version": p.version or "",
        "year": p.year,
        "pub_date": p.pub_date or "",
        "venue": p.venue or "",
        "doi": p.doi or "",
        "github_url": p.github_url or "",
        "tags": p.tags or [],
        "note": p.note or "",
        "page_count": p.page_count,
        "has_text_layer": p.has_text_layer,
        "doc_type": p.doc_type,
        "source": p.source,
        "status": p.status,
        "progress": p.progress,
        "status_msg": p.status_msg,
        "stats": p.stats or {},
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if dup:
        d["duplicate"] = {
            "id": dup.id,
            "existing_paper_id": dup.existing_paper_id,
            "existing_title": existing.title if existing else "",
            "existing_version": existing.version if existing else "",
            "existing_filename": existing.filename if existing else "",
            "text_similarity": dup.text_similarity,
            "semantic_similarity": dup.semantic_similarity,
            "suggestion": dup.suggestion,
            "suggestion_reason": dup.suggestion_reason,
            "diff_summary": dup.diff_summary,
            "decision": dup.decision,
        }
    return d


def submit_parse_task(paper_id: int, user_id: int) -> int:
    def _job(task_id: int) -> dict:
        return parse_paper(
            paper_id,
            progress_cb=lambda pct, msg: runner.update_task(task_id, progress=pct, message=msg),
        )

    return runner.submit("parse_paper", user_id, _job, payload={"paper_id": paper_id})


def ingest_pdf_bytes(
    db: Session,
    user_id: int,
    filename: str,
    raw: bytes,
    *,
    source: str = "upload",
    skip_dedup: bool = False,
) -> tuple[dict, int | None]:
    """统一入库入口（本地上传与阶段5自动抓取共用）。"""
    sha = hashlib.sha256(raw).hexdigest()
    safe_name = Path(filename or "paper.pdf").name
    paper = Paper(
        owner_id=user_id,
        filename=safe_name,
        title=Path(safe_name).stem,
        file_hash=sha,
        file_size=len(raw),
        source=source,
        status="uploaded",
        status_msg="已接收，准备去重检测",
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)

    pdir = settings.data_path / "papers" / str(paper.id)
    pdir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdir / safe_name
    pdf_path.write_bytes(raw)
    paper.file_path = str(pdf_path)

    # 快速读取页数与文本层标记
    try:
        prof = inspect_pdf(pdf_path, safe_name)
        paper.page_count = prof.page_count
        paper.has_text_layer = prof.has_text_layer
        paper.doc_type = prof.doc_type
        if prof.arxiv_id:
            paper.arxiv_id = prof.arxiv_id
        if prof.version:
            paper.version = prof.version
        if prof.pub_date:
            paper.pub_date = prof.pub_date
    except Exception as e:  # noqa: BLE001
        paper.status = "failed"
        paper.status_msg = f"PDF 文件损坏或无法打开: {e}"
        db.commit()
        raise HTTPException(status_code=400, detail=paper.status_msg) from e

    db.commit()

    # 语义去重检测
    if not skip_dedup:
        dup = dedup_svc.detect_duplicate(db, user_id, paper, pdf_path)
        if dup:
            existing = db.get(Paper, dup.existing_paper_id)
            return _paper_dict(paper, dup, existing), None

    task_id = submit_parse_task(paper.id, user_id)
    db.refresh(paper)
    return _paper_dict(paper), task_id


@router.post("/upload")
async def upload_papers(
    files: list[UploadFile] = File(...),
    skip_dedup: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """支持单篇/批量 PDF 上传，自动执行语义去重与后台细粒度解析。"""
    results = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"仅支持 PDF 文件: {f.filename}")
        raw = await f.read()
        if not raw:
            continue
        pdict, task_id = ingest_pdf_bytes(
            db, user.id, f.filename or "paper.pdf", raw, skip_dedup=skip_dedup
        )
        results.append({"paper": pdict, "task_id": task_id})
    return {"items": results}


@router.post("/{paper_id}/dedup-decision")
def resolve_duplicate(
    paper_id: int,
    req: DedupDecisionReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """处理语义去重预警：保留现有版本(keep_existing) / 覆盖更新(overwrite) / 两者都保留(keep_both)。"""
    if req.decision not in ("keep_existing", "overwrite", "keep_both"):
        raise HTTPException(status_code=400, detail="decision 必须为 keep_existing / overwrite / keep_both")

    new_paper = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not new_paper:
        raise HTTPException(status_code=404, detail="文献不存在")

    dup = (
        db.query(DuplicateRecord)
        .filter(DuplicateRecord.new_paper_id == paper_id, DuplicateRecord.decision == "pending")
        .first()
    )
    if not dup:
        raise HTTPException(status_code=400, detail="该文献没有待处理的去重记录")

    existing = db.get(Paper, dup.existing_paper_id)
    if req.decision == "keep_existing":
        existing_id = dup.existing_paper_id
        pdir = settings.data_path / "papers" / str(new_paper.id)
        db.delete(dup)
        db.delete(new_paper)
        db.commit()
        shutil.rmtree(pdir, ignore_errors=True)
        return {"action": "keep_existing", "paper_id": existing_id, "task_id": None}

    if req.decision == "overwrite" and existing:
        # 用新上传 PDF 替换现有文献文件并重新解析
        old_dir = settings.data_path / "papers" / str(existing.id)
        old_dir.mkdir(parents=True, exist_ok=True)
        target_pdf = old_dir / new_paper.filename
        shutil.copy2(new_paper.file_path, target_pdf)
        existing.filename = new_paper.filename
        existing.file_path = str(target_pdf)
        existing.file_hash = new_paper.file_hash
        existing.file_size = new_paper.file_size
        existing.arxiv_id = new_paper.arxiv_id or existing.arxiv_id
        existing.version = new_paper.version or existing.version
        existing.pub_date = new_paper.pub_date or existing.pub_date
        existing.fingerprint = new_paper.fingerprint or existing.fingerprint
        existing.status = "uploaded"
        existing.progress = 0.0
        existing.status_msg = "已按用户决策覆盖为新版本，准备重新解析"

        new_dir = settings.data_path / "papers" / str(new_paper.id)
        db.delete(dup)
        db.delete(new_paper)
        db.commit()
        shutil.rmtree(new_dir, ignore_errors=True)
        if _delete_index_hook:
            _delete_index_hook(existing.id)
        tid = submit_parse_task(existing.id, user.id)
        return {"action": "overwrite", "paper_id": existing.id, "task_id": tid}

    # keep_both
    dup.decision = "keep_both"
    new_paper.status = "uploaded"
    new_paper.status_msg = "用户选择保留双版本，正在解析"
    db.commit()
    tid = submit_parse_task(new_paper.id, user.id)
    return {"action": "keep_both", "paper_id": new_paper.id, "task_id": tid}


@router.get("")
def list_papers(
    q: str = Query(default=""),
    tag: str = Query(default=""),
    status: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(Paper).filter(Paper.owner_id == user.id)
    if status:
        query = query.filter(Paper.status == status)
    rows = query.order_by(Paper.id.desc()).all()

    out = []
    for p in rows:
        if q:
            ql = q.lower()
            hay = f"{p.title} {p.filename} {p.abstract} {p.arxiv_id}".lower()
            if ql not in hay:
                continue
        if tag and tag not in (p.tags or []):
            continue
        dup = (
            db.query(DuplicateRecord)
            .filter(DuplicateRecord.new_paper_id == p.id, DuplicateRecord.decision == "pending")
            .first()
        )
        existing = db.get(Paper, dup.existing_paper_id) if dup else None
        out.append(_paper_dict(p, dup, existing))
    return out


@router.get("/{paper_id}")
def get_paper(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")
    dup = (
        db.query(DuplicateRecord)
        .filter(DuplicateRecord.new_paper_id == p.id, DuplicateRecord.decision == "pending")
        .first()
    )
    existing = db.get(Paper, dup.existing_paper_id) if dup else None
    return _paper_dict(p, dup, existing)


@router.patch("/{paper_id}")
def update_paper(
    paper_id: int,
    req: PaperUpdateReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")
    for field in ("title", "venue", "year", "tags", "note", "github_url"):
        val = getattr(req, field)
        if val is not None:
            setattr(p, field, val)
    db.commit()
    db.refresh(p)
    return _paper_dict(p)


@router.delete("/{paper_id}")
def delete_paper(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")
    pdir = settings.data_path / "papers" / str(p.id)
    if _delete_index_hook:
        _delete_index_hook(p.id)
    db.query(DuplicateRecord).filter(
        (DuplicateRecord.new_paper_id == p.id) | (DuplicateRecord.existing_paper_id == p.id)
    ).delete()
    db.delete(p)
    db.commit()
    shutil.rmtree(pdir, ignore_errors=True)
    return {"ok": True}


@router.post("/{paper_id}/reparse")
def reparse_paper(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")
    tid = submit_parse_task(p.id, user.id)
    return {"paper_id": p.id, "task_id": tid}


@router.get("/{paper_id}/structure")
def get_structure(
    paper_id: int,
    page_from: int = Query(default=1, ge=1),
    page_to: int | None = Query(default=None, ge=1),
    block_type: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取细粒度解析结构：元数据、1-3级标题树、按页或类型过滤的内容块与参考文献。"""
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")

    secs = (
        db.query(Section)
        .filter(Section.paper_id == paper_id)
        .order_by(Section.order.asc())
        .all()
    )
    bq = db.query(Block).filter(Block.paper_id == paper_id, Block.page >= page_from)
    if page_to is not None:
        bq = bq.filter(Block.page <= page_to)
    elif p.page_count > 60 and not block_type:
        # 百页以上超长书籍默认返回前 20 页块，避免单次响应过大，可按页码分页加载
        bq = bq.filter(Block.page <= page_from + 19)
    if block_type:
        bq = bq.filter(Block.type == block_type)
    blocks = bq.order_by(Block.page.asc(), Block.order.asc()).all()

    refs = (
        db.query(Reference)
        .filter(Reference.paper_id == paper_id)
        .order_by(Reference.idx.asc())
        .all()
    )

    return {
        "paper": _paper_dict(p),
        "sections": [
            {
                "id": s.id,
                "parent_id": s.parent_id,
                "level": s.level,
                "number": s.number,
                "title": s.title,
                "page": s.page,
                "kind": s.kind,
            }
            for s in secs
        ],
        "blocks": [
            {
                "id": b.id,
                "section_id": b.section_id,
                "type": b.type,
                "page": b.page,
                "bbox": b.bbox,
                "content": b.content,
                "latex": b.latex,
                "html": b.html,
                "image_url": f"/api/papers/{paper_id}/assets/{b.image_path}" if b.image_path else "",
            }
            for b in blocks
        ],
        "references": [
            {
                "id": r.id,
                "idx": r.idx,
                "raw_text": r.raw_text,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "venue": r.venue,
                "doi": r.doi,
                "arxiv_id": r.arxiv_id,
                "page": r.page,
                "bbox": r.bbox,
                "cite_contexts": r.cite_contexts or [],
            }
            for r in refs
        ],
    }


@router.get("/{paper_id}/file")
def get_pdf_file(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p or not p.file_path or not Path(p.file_path).exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")
    return FileResponse(p.file_path, media_type="application/pdf", filename=p.filename)


@router.get("/{paper_id}/assets/{filename}")
def get_asset_file(
    paper_id: int,
    filename: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")
    safe = Path(filename).name
    asset = settings.data_path / "papers" / str(paper_id) / "assets" / safe
    if not asset.exists():
        raise HTTPException(status_code=404, detail="图片资源不存在")
    return FileResponse(asset)


def parse_existing_local_pdf(user_id: int, pdf_path: Path) -> int:
    """辅助函数：供预置脚本直接导入本地 PDF 并解析。"""
    with SessionLocal() as db:
        raw = pdf_path.read_bytes()
        pdict, _ = ingest_pdf_bytes(db, user_id, pdf_path.name, raw, source="preset", skip_dedup=True)
        pid = pdict["id"]
    parse_paper(pid)
    return pid
