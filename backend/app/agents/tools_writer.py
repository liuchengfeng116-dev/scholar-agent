"""Writer Agent 工具：允许中央 Agent 调用无幻觉框架、拓扑图脚本与学术中译英。"""

from __future__ import annotations

from app.agents.core import AgentContext, tool
from app.copilot import service as copilot_svc


@tool(
    name="draft_paper_outline",
    agent="writer",
    description="根据研究 Idea 自动生成包含 Abstract、Introduction、Related Work 与真实校验 References 的论文框架。",
    parameters={
        "type": "object",
        "properties": {
            "idea": {"type": "string", "description": "研究构想或主题"},
            "lang": {"type": "string", "enum": ["en", "zh"], "default": "en"},
        },
        "required": ["idea"],
    },
)
def draft_paper_outline(ctx: AgentContext, idea: str, lang: str = "en") -> dict:
    return copilot_svc.generate_outline(
        ctx.db, ctx.user_id, idea=idea, paper_ids=ctx.paper_ids or None, lang=lang
    )


@tool(
    name="generate_architecture_diagram",
    agent="writer",
    description="根据系统/模型描述生成架构拓扑图脚本（支持 mermaid / tikz / graphviz / matplotlib）。",
    parameters={
        "type": "object",
        "properties": {
            "idea": {"type": "string"},
            "format": {"type": "string", "enum": ["mermaid", "tikz", "graphviz", "matplotlib"], "default": "mermaid"},
        },
        "required": ["idea"],
    },
)
def generate_architecture_diagram(ctx: AgentContext, idea: str, format: str = "mermaid") -> dict:
    return copilot_svc.generate_diagram(idea, fmt=format)
