"""Scout Agent 工具：允许中央 Agent 主动调用外部文献检索、自动抓取入库与个性化推荐。"""

from __future__ import annotations

from app.agents.core import AgentContext, tool
from app.api.papers import ingest_pdf_bytes
from app.scout import service as scout_svc


@tool(
    name="search_and_fetch_paper",
    agent="scout",
    description="根据论文标题或关键词检索外部学术库（Semantic Scholar/OpenAlex/arXiv），并自动下载 PDF 入库解析。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "论文标题或 arXiv ID"},
            "auto_ingest": {"type": "boolean", "default": True, "description": "是否自动下载并入库"},
        },
        "required": ["query"],
    },
)
def search_and_fetch_paper(ctx: AgentContext, query: str, auto_ingest: bool = True) -> dict:
    hits = scout_svc.search_external_papers(query, limit=5)
    if not hits:
        return {"found": False, "message": f"未检索到与 {query} 匹配的公开论文"}
    if not auto_ingest:
        return {"found": True, "candidates": hits}

    for h in hits:
        res = scout_svc.download_pdf_bytes(h.get("pdf_url", ""), h.get("arxiv_id", ""))
        if res:
            raw, fname = res
            pdict, tid = ingest_pdf_bytes(ctx.db, ctx.user_id, fname, raw, source="agent_scout")
            return {
                "found": True,
                "ingested": True,
                "paper": pdict,
                "task_id": tid,
                "meta": h,
            }
    return {"found": True, "ingested": False, "candidates": hits}


@tool(
    name="evaluate_reference_quality",
    agent="scout",
    description="评估指定论文参考文献的「引用含金量」，识别核心基石引用与低价值应付式引用。",
    parameters={
        "type": "object",
        "properties": {"paper_id": {"type": "integer", "description": "论文ID"}},
        "required": ["paper_id"],
    },
)
def evaluate_reference_quality(ctx: AgentContext, paper_id: int) -> dict:
    items = scout_svc.evaluate_references_quality(ctx.db, paper_id)
    core = [it for it in items if it["label"] == "core"][:8]
    perf = [it for it in items if it["label"] == "perfunctory"][:6]
    return {
        "total": len(items),
        "core_highlights": core,
        "filtered_perfunctory": perf,
    }


@tool(
    name="get_recommended_papers",
    agent="scout",
    description="获取基于用户本地库、arXiv 最新动态与 GitHub 开源代码加权的个性化论文推荐。",
    parameters={"type": "object", "properties": {"top_n": {"type": "integer", "default": 5}}},
)
def get_recommended_papers(ctx: AgentContext, top_n: int = 5) -> list[dict]:
    return scout_svc.generate_recommendations(ctx.db, ctx.user_id, top_n=min(10, top_n))
