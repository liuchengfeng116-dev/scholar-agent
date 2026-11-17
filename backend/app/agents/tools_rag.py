"""Retriever Agent 工具：混合检索、单篇精查、多篇逐一检索与参考文献精确查询。"""

from __future__ import annotations

from app.agents.core import AgentContext, tool
from app.db.models import Paper, Reference
from app.rag.retriever import hybrid_search


def _register_hits(ctx: AgentContext, hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        ref = ctx.add_evidence(h)
        out.append(
            {
                "ref": ref,
                "paper_id": h["paper_id"],
                "paper_title": h["paper_title"],
                "page": h["page"],
                "section_path": h["section_path"],
                "type": h["type"],
                "text": h["text"][:1400],
            }
        )
    return out


@tool(
    name="search_papers",
    agent="retriever",
    description="在知识库（或当前会话限定的论文范围）内执行 BM25+向量混合检索，返回带页码、章节与引用标记 [E#] 的原文证据。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词或英文/中文学术问句（英文论文建议用英文关键术语提升命中率）"},
            "paper_ids": {"type": "array", "items": {"type": "integer"}, "description": "可选，限定论文ID列表"},
            "top_k": {"type": "integer", "default": 8},
        },
        "required": ["query"],
    },
)
def search_papers(
    ctx: AgentContext,
    query: str,
    paper_ids: list[int] | None = None,
    top_k: int = 8,
) -> list[dict]:
    scope = paper_ids or ctx.paper_ids or None
    hits = hybrid_search(
        ctx.db,
        user_id=ctx.user_id,
        query=query,
        paper_ids=scope,
        top_k=min(14, max(3, top_k)),
        per_paper_balance=True,
    )
    return _register_hits(ctx, hits)


@tool(
    name="search_single_paper",
    agent="retriever",
    description="针对单篇论文执行深度检索，适合多文对比时逐篇核对具体细节（如超参数、参数量、LN位置、Context Window、BLEU）。",
    parameters={
        "type": "object",
        "properties": {
            "paper_id": {"type": "integer", "description": "目标论文ID"},
            "query": {"type": "string", "description": "检索问题或关键词"},
            "top_k": {"type": "integer", "default": 6},
        },
        "required": ["paper_id", "query"],
    },
)
def search_single_paper(ctx: AgentContext, paper_id: int, query: str, top_k: int = 6) -> list[dict]:
    hits = hybrid_search(
        ctx.db,
        user_id=ctx.user_id,
        query=query,
        paper_ids=[paper_id],
        top_k=min(12, max(3, top_k)),
        per_paper_balance=False,
    )
    return _register_hits(ctx, hits)


@tool(
    name="lookup_reference",
    agent="retriever",
    description="精确查询指定论文的参考文献条目（可按编号如 11 或按作者/标题关键词查询完整引用信息）。",
    parameters={
        "type": "object",
        "properties": {
            "paper_id": {"type": "integer", "description": "论文ID"},
            "ref_idx": {"type": "integer", "description": "参考文献编号，如 11"},
            "keyword": {"type": "string", "description": "作者名或标题关键词"},
        },
        "required": ["paper_id"],
    },
)
def lookup_reference(
    ctx: AgentContext,
    paper_id: int,
    ref_idx: int | None = None,
    keyword: str | None = None,
) -> list[dict]:
    p = ctx.db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == ctx.user_id).first()
    if not p:
        return [{"error": f"论文 {paper_id} 不存在"}]
    q = ctx.db.query(Reference).filter(Reference.paper_id == paper_id)
    if ref_idx is not None:
        q = q.filter(Reference.idx == ref_idx)
    rows = q.order_by(Reference.idx.asc()).all()
    if keyword:
        kl = keyword.lower()
        rows = [r for r in rows if kl in r.raw_text.lower() or kl in r.title.lower()]
    out = []
    for r in rows[:10]:
        text = f"[{r.idx}] {r.raw_text}"
        ref = ctx.add_evidence(
            {
                "paper_id": p.id,
                "paper_title": p.title or p.filename,
                "block_id": f"ref_{r.id}",
                "page": r.page or 1,
                "bbox": r.bbox or [],
                "section_path": f"References [{r.idx}]",
                "text": text,
            }
        )
        out.append(
            {
                "ref": ref,
                "idx": r.idx,
                "raw_text": r.raw_text,
                "page": r.page,
                "cite_contexts": r.cite_contexts or [],
            }
        )
    return out
