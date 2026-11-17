"""Graph Agent 工具：允许中央 Agent 直接执行引用图谱构建与领域前沿洞察。"""

from __future__ import annotations

from app.agents.core import AgentContext, tool
from app.graph import service as graph_svc


@tool(
    name="build_and_analyze_citation_graph",
    agent="graph",
    description="为选定（或全库）论文构建局部引用拓扑图谱，用图论算法找出基石论文与边缘衍生论文。",
    parameters={
        "type": "object",
        "properties": {
            "paper_ids": {"type": "array", "items": {"type": "integer"}, "description": "可选论文ID列表"},
            "name": {"type": "string", "default": "Agent引用图谱"},
        },
    },
)
def build_and_analyze_citation_graph(
    ctx: AgentContext, paper_ids: list[int] | None = None, name: str = "Agent引用图谱"
) -> dict:
    g = graph_svc.build_citation_graph(
        ctx.db, ctx.user_id, paper_ids=paper_ids or ctx.paper_ids or None, name=name
    )
    return {"graph_id": g.id, "metrics": g.data.get("metrics"), "nodes": g.data.get("nodes", [])[:15]}
