"""Parser Agent 工具：允许中央 Agent 查询文献目录树、元数据、表格与公式。"""

from __future__ import annotations

from app.agents.core import AgentContext, tool
from app.db.models import Block, Paper, Section


@tool(
    name="get_paper_outline",
    agent="parser",
    description="查看指定论文的标题、作者/机构、摘要与1-3级章节目录结构及统计信息。",
    parameters={
        "type": "object",
        "properties": {"paper_id": {"type": "integer", "description": "论文ID"}},
        "required": ["paper_id"],
    },
)
def get_paper_outline(ctx: AgentContext, paper_id: int) -> dict:
    p = ctx.db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == ctx.user_id).first()
    if not p:
        return {"error": f"论文 {paper_id} 不存在"}
    secs = (
        ctx.db.query(Section)
        .filter(Section.paper_id == paper_id)
        .order_by(Section.order.asc())
        .limit(80)
        .all()
    )
    meta_text = (
        f"Title: {p.title}\n"
        f"Authors: {p.authors}\n"
        f"Affiliations: {p.affiliations}\n"
        f"Year/Venue: {p.year} {p.venue}\n"
        f"Abstract: {p.abstract}"
    )
    ref = ctx.add_evidence(
        {
            "paper_id": p.id,
            "paper_title": p.title or p.filename,
            "block_id": f"outline_{p.id}",
            "page": 1,
            "bbox": [50, 50, 550, 300],
            "section_path": "Metadata & Abstract",
            "text": meta_text,
        }
    )
    return {
        "ref": ref,
        "paper_id": p.id,
        "title": p.title,
        "authors": p.authors,
        "affiliations": p.affiliations,
        "year": p.year,
        "venue": p.venue,
        "abstract": p.abstract,
        "stats": p.stats,
        "sections": [{"level": s.level, "title": s.title, "page": s.page, "kind": s.kind} for s in secs],
    }


@tool(
    name="get_paper_tables_and_formulas",
    agent="parser",
    description="获取指定论文提取出的表格（Markdown）与核心 LaTeX 公式。",
    parameters={
        "type": "object",
        "properties": {
            "paper_id": {"type": "integer", "description": "论文ID"},
            "kind": {"type": "string", "enum": ["table", "formula", "all"], "default": "all"},
        },
        "required": ["paper_id"],
    },
)
def get_paper_tables_and_formulas(ctx: AgentContext, paper_id: int, kind: str = "all") -> list[dict]:
    p = ctx.db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == ctx.user_id).first()
    if not p:
        return [{"error": f"论文 {paper_id} 不存在"}]
    types = ["table", "formula"] if kind == "all" else [kind]
    rows = (
        ctx.db.query(Block)
        .filter(Block.paper_id == paper_id, Block.type.in_(types))
        .order_by(Block.page.asc(), Block.order.asc())
        .limit(30)
        .all()
    )
    out = []
    for b in rows:
        content = b.latex or b.content
        ref = ctx.add_evidence(
            {
                "paper_id": p.id,
                "paper_title": p.title or p.filename,
                "block_id": b.id,
                "page": b.page,
                "bbox": b.bbox,
                "section_path": b.type,
                "text": content,
            }
        )
        out.append({"ref": ref, "type": b.type, "page": b.page, "content": content})
    return out
