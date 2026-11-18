"""结构感知分块与索引构建（保留章节路径、表格完整性及页码/坐标锚点）。"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chromadb
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Block, Chunk, Paper, Reference, Section
from app.llm import client as llm

log = logging.getLogger(__name__)

_chroma_client: chromadb.ClientAPI | None = None
_COLLECTION_NAME = "scholar_chunks"


def get_chroma_collection():
    global _chroma_client
    if _chroma_client is None:
        chroma_dir = settings.data_path / "chroma"
        # Windows 下 Chroma Rust HNSW 引擎对含中文路径存在兼容问题，非 ASCII 路径自动映射到 LOCALAPPDATA
        if not str(chroma_dir).isascii():
            import hashlib
            import os

            tag = hashlib.md5(str(chroma_dir).encode("utf-8")).hexdigest()[:10]
            base = Path(os.environ.get("LOCALAPPDATA", "C:/Temp")) / "scholar_agent_chroma"
            chroma_dir = base / tag
        chroma_dir.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
    return _chroma_client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _section_paths(sections: list[Section]) -> dict[int, str]:
    by_id = {s.id: s for s in sections}
    paths: dict[int, str] = {}
    for s in sections:
        parts = [s.title]
        cur = s
        guard = 0
        while cur.parent_id and cur.parent_id in by_id and guard < 5:
            cur = by_id[cur.parent_id]
            parts.append(cur.title)
            guard += 1
        paths[s.id] = " > ".join(reversed(parts))
    return paths


def _normalize_for_search(text: str) -> str:
    # 将 Unicode 希腊字母与上下标补充可检索别名（如 β -> beta，₁ -> 1）
    repl = {"β": "beta ", "α": "alpha ", "ϵ": "epsilon ", "ε": "epsilon ", "₁": "1", "₂": "2", "√": "sqrt "}
    out = text
    for k, v in repl.items():
        if k in out:
            out = out.replace(k, f"{k} {v}")
    return out


def build_chunks_for_paper(db: Session, paper_id: int) -> int:
    """将已解析的 Section / Block / Reference 切分为结构感知 Chunk 并写入 Chroma。"""
    paper = db.get(Paper, paper_id)
    if not paper:
        return 0

    delete_paper_index(db, paper_id)

    sections = db.query(Section).filter(Section.paper_id == paper_id).order_by(Section.order.asc()).all()
    sec_paths = _section_paths(sections)
    blocks = (
        db.query(Block)
        .filter(Block.paper_id == paper_id)
        .order_by(Block.page.asc(), Block.order.asc())
        .all()
    )
    refs = db.query(Reference).filter(Reference.paper_id == paper_id).order_by(Reference.idx.asc()).all()

    max_chars = 1200 if paper.doc_type == "book" else 750
    chunks_to_add: list[Chunk] = []

    # 1. 元数据与摘要专属分块（确保作者、机构、年份、摘要问题必命中）
    authors_str = ", ".join(
        f"{a.get('name', '')} ({', '.join(a.get('affiliations') or [])})" if isinstance(a, dict) else str(a)
        for a in (paper.authors or [])
    )
    front_text = "\n".join(
        part
        for part in [
            f"Title: {paper.title}",
            f"Authors & Affiliations: {authors_str}" if authors_str else "",
            f"Venue/Year: {paper.venue} {paper.year or ''}".strip(),
            f"Abstract: {paper.abstract}" if paper.abstract else "",
        ]
        if part
    )
    if front_text.strip():
        chunks_to_add.append(
            Chunk(
                paper_id=paper_id,
                section_path="Metadata & Abstract",
                type="meta",
                page=1,
                page_end=1,
                anchors=[{"page": 1, "bbox": [50, 50, 550, 300]}],
                text=front_text,
                order=0,
            )
        )

    # 2. 按章节与块类型聚合成 Chunk（表格与独立公式保持完整不被切碎）
    buf_texts: list[str] = []
    buf_anchors: list[dict] = []
    buf_sec_id: int | None = None
    buf_len = 0

    def _flush() -> None:
        nonlocal buf_texts, buf_anchors, buf_sec_id, buf_len
        if not buf_texts:
            return
        sec_path = sec_paths.get(buf_sec_id, "Body") if buf_sec_id else "Body"
        pages = [a["page"] for a in buf_anchors if a.get("page")]
        chunks_to_add.append(
            Chunk(
                paper_id=paper_id,
                section_path=sec_path[:480],
                type="text",
                page=min(pages) if pages else 1,
                page_end=max(pages) if pages else 1,
                anchors=buf_anchors[:8],
                text=f"[{sec_path}]\n" + "\n".join(buf_texts),
                order=len(chunks_to_add),
            )
        )
        # 保留最后一段作为轻度重叠，避免跨段信息丢失（超长书跳过重叠减小体量）
        if paper.doc_type != "book" and len(buf_texts) > 1 and len(buf_texts[-1]) < 260:
            buf_texts = [buf_texts[-1]]
            buf_anchors = [buf_anchors[-1]]
            buf_len = len(buf_texts[0])
        else:
            buf_texts, buf_anchors, buf_len = [], [], 0

    for b in blocks:
        # 参考文献区单独成组索引
        sec_title = sec_paths.get(b.section_id, "") if b.section_id else ""
        if re.search(r"\b(references|bibliography)\b", sec_title, re.I):
            continue

        if b.type in ("table", "formula"):
            _flush()
            body = b.latex or b.content
            if body.strip():
                chunks_to_add.append(
                    Chunk(
                        paper_id=paper_id,
                        section_path=(sec_title or b.type)[:480],
                        type=b.type,
                        page=b.page,
                        page_end=b.page,
                        anchors=[{"page": b.page, "bbox": b.bbox}],
                        text=f"[{sec_title or b.type} | p.{b.page}]\n{body}",
                        order=len(chunks_to_add),
                    )
                )
            continue

        txt = (b.content or "").strip()
        if not txt:
            continue
        if b.section_id != buf_sec_id and buf_texts:
            _flush()
        buf_sec_id = b.section_id
        buf_texts.append(txt)
        buf_anchors.append({"page": b.page, "bbox": b.bbox})
        buf_len += len(txt)
        if buf_len >= max_chars:
            _flush()

    _flush()

    # 3. 参考文献分批索引（每 8 条一个 Chunk，便于直接命中具体引用信息）
    for i in range(0, len(refs), 8):
        batch_refs = refs[i : i + 8]
        txt = "\n".join(f"[{r.idx}] {r.raw_text}" for r in batch_refs)
        first_p = batch_refs[0].page or 1
        last_p = batch_refs[-1].page or first_p
        chunks_to_add.append(
            Chunk(
                paper_id=paper_id,
                section_path=f"References [{batch_refs[0].idx}-{batch_refs[-1].idx}]",
                type="reference",
                page=first_p,
                page_end=last_p,
                anchors=[{"page": r.page, "bbox": r.bbox} for r in batch_refs if r.page],
                text=txt,
                order=len(chunks_to_add),
            )
        )

    if not chunks_to_add:
        return 0

    for c in chunks_to_add:
        db.add(c)
    db.commit()

    # 读取带自增 id 的 Chunk 并并发向量化写入 Chroma
    saved = db.query(Chunk).filter(Chunk.paper_id == paper_id).order_by(Chunk.order.asc()).all()
    texts = [_normalize_for_search(c.text) for c in saved]

    # 按 10 条一批切分并用 3 线程并发请求 DashScope Embedding（兼顾速率与百页长书构建速度）
    batches = [texts[i : i + 10] for i in range(0, len(texts), 10)]
    all_vecs: list[list[float]] = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for vec_batch in pool.map(llm.embed, batches):
                all_vecs.extend(vec_batch)
    except Exception:  # noqa: BLE001
        log.exception("向量化写入 Chroma 失败（检索将回退为 BM25 词法检索）")
        return len(saved)

    if len(all_vecs) == len(saved):
        col = get_chroma_collection()
        for i in range(0, len(saved), 200):
            sl = saved[i : i + 200]
            col.upsert(
                ids=[str(c.id) for c in sl],
                embeddings=all_vecs[i : i + 200],
                documents=[c.text[:2000] for c in sl],
                metadatas=[
                    {
                        "paper_id": paper_id,
                        "owner_id": paper.owner_id,
                        "page": c.page,
                        "type": c.type,
                    }
                    for c in sl
                ],
            )
    return len(saved)


def delete_paper_index(db: Session, paper_id: int) -> None:
    """删除文献对应的 Chunk 表与 Chroma 向量。"""
    try:
        col = get_chroma_collection()
        col.delete(where={"paper_id": paper_id})
    except Exception:  # noqa: BLE001
        log.debug("Chroma 删除跳过 paper_id=%d", paper_id)
    db.query(Chunk).filter(Chunk.paper_id == paper_id).delete()
    db.commit()
