"""混合检索与重排：BM25 词法精确命中 + Chroma 语义向量检索 + RRF 融合 + 多篇均衡召回。"""

from __future__ import annotations

import logging
import re

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.db.models import Chunk, Paper
from app.llm import client as llm
from app.rag.chunker import get_chroma_collection

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.+-]+|[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    # 同时展开常见希腊字母符号别名，让 "beta1" / "beta_1" 也能命中 "β1"
    norm = (
        (text or "")
        .replace("β", " beta ")
        .replace("α", " alpha ")
        .replace("ϵ", " epsilon ")
        .replace("ε", " epsilon ")
        .replace("₁", "1")
        .replace("₂", "2")
    )
    for part in _TOKEN_RE.findall(norm.lower()):
        if re.match(r"[\u4e00-\u9fff]+", part):
            tokens.extend(jieba.lcut(part))
        else:
            tokens.append(part)
            # 对带下划线/点号词同时切分子词（如 beta_1 -> beta, 1）
            if "_" in part or "-" in part:
                tokens.extend(p for p in re.split(r"[_-]", part) if p)
    return tokens


def _bm25_search(chunks: list[Chunk], query: str, top_k: int) -> list[tuple[int, float]]:
    if not chunks:
        return []
    corpus = [_tokenize(c.text) for c in chunks]
    bm25 = BM25Okapi(corpus)
    q_toks = _tokenize(query)
    if not q_toks:
        return []
    scores = bm25.get_scores(q_toks)
    ranked = sorted(
        [(chunks[i].id, float(scores[i])) for i in range(len(chunks)) if scores[i] > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[:top_k]


def _dense_search(
    query: str, user_id: int, paper_ids: list[int] | None, top_k: int
) -> list[tuple[int, float]]:
    try:
        q_vec = llm.embed_one(query)
        col = get_chroma_collection()
        if paper_ids and len(paper_ids) == 1:
            where = {"paper_id": paper_ids[0]}
        elif paper_ids:
            where = {"paper_id": {"$in": paper_ids}}
        else:
            where = {"owner_id": user_id}
        res = col.query(query_embeddings=[q_vec], n_results=top_k, where=where)
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        return [(int(cid), max(0.0, 1.0 - float(d))) for cid, d in zip(ids, dists, strict=False)]
    except Exception:  # noqa: BLE001
        log.warning("Chroma 向量检索异常，降级为纯 BM25")
        return []


def hybrid_search(
    db: Session,
    *,
    user_id: int,
    query: str,
    paper_ids: list[int] | None = None,
    top_k: int = 8,
    per_paper_balance: bool = True,
) -> list[dict]:
    """执行 BM25 + 向量混合检索，使用 RRF 融合并保留多篇均衡覆盖。"""
    pq = db.query(Paper).filter(Paper.owner_id == user_id, Paper.status == "ready")
    if paper_ids:
        pq = pq.filter(Paper.id.in_(paper_ids))
    papers = pq.all()
    if not papers:
        return []
    paper_map = {p.id: p for p in papers}
    target_pids = list(paper_map.keys())

    all_chunks = db.query(Chunk).filter(Chunk.paper_id.in_(target_pids)).all()
    if not all_chunks:
        return []
    chunk_map = {c.id: c for c in all_chunks}

    bm25_hits = _bm25_search(all_chunks, query, top_k=top_k * 3)
    dense_hits = _dense_search(query, user_id, target_pids, top_k=top_k * 3)

    # RRF 倒数秩融合
    rrf: dict[int, float] = {}
    for rank, (cid, _) in enumerate(bm25_hits, start=1):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (50 + rank)
    for rank, (cid, _) in enumerate(dense_hits, start=1):
        rrf[cid] = rrf.get(cid, 0.0) + 1.2 / (50 + rank)

    # 表格与元数据轻微加权（数值/作者类精准问答更稳定）
    for cid in list(rrf.keys()):
        c = chunk_map.get(cid)
        if c and c.type in ("table", "meta"):
            rrf[cid] *= 1.15

    ranked_ids = sorted(rrf.keys(), key=lambda cid: rrf[cid], reverse=True)

    # 多篇文献对比场景：避免单本 541 页长书垄断所有名额，每篇保底至少 1-2 个最佳片段
    selected: list[int] = []
    if per_paper_balance and 1 < len(target_pids) <= 8:
        by_paper: dict[int, list[int]] = {pid: [] for pid in target_pids}
        for cid in ranked_ids:
            c = chunk_map.get(cid)
            if c and c.paper_id in by_paper:
                by_paper[c.paper_id].append(cid)
        quota = max(1, top_k // len(target_pids))
        for pid in target_pids:
            selected.extend(by_paper[pid][:quota])

    for cid in ranked_ids:
        if cid not in selected:
            selected.append(cid)
        if len(selected) >= top_k:
            break

    out = []
    for cid in selected[:top_k]:
        c = chunk_map.get(cid)
        if not c:
            continue
        p = paper_map.get(c.paper_id)
        first_anchor = (c.anchors or [{}])[0]
        out.append(
            {
                "chunk_id": c.id,
                "paper_id": c.paper_id,
                "paper_title": (p.title or p.filename) if p else "",
                "section_path": c.section_path,
                "type": c.type,
                "page": c.page,
                "bbox": first_anchor.get("bbox") or [],
                "score": round(rrf.get(cid, 0.0), 4),
                "text": c.text,
            }
        )
    return out
