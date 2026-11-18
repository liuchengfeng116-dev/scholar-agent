"""Task Planner：意图识别、多步规划与动态重规划。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.agents.core import REGISTRY
from app.core.config import settings
from app.llm import client as llm

log = logging.getLogger(__name__)


@dataclass
class ExecutionPlan:
    intent: str
    requires_strict_grounding: bool
    reasoning: str
    steps: list[dict] = field(default_factory=list)


def _describe_tools() -> str:
    if not REGISTRY:
        return "（当前暂无外部专职工具，仅使用通用对话）"
    lines = []
    for t in REGISTRY.values():
        lines.append(f"- [{t.agent}] {t.name}: {t.description}")
    return "\n".join(lines)


def make_plan(
    query: str,
    *,
    paper_summary: str,
    memories: list[dict],
    history_summary: str = "",
) -> ExecutionPlan:
    """根据用户提问、当前选定文献与记忆，生成显式多步任务计划。"""
    mem_text = "\n".join(f"- ({m['kind']}) {m['content']}" for m in memories) or "（无）"
    tools_text = _describe_tools()

    prompt = (
        "你是科研助手系统的中央规划器（Task Planner）。请分析用户需求并制定可执行的步骤计划。\n\n"
        f"当前可用专职 Agent 与工具：\n{tools_text}\n\n"
        f"当前会话关联的文献：\n{paper_summary or '（未限定，可检索全库）'}\n\n"
        f"用户长期记忆与偏好：\n{mem_text}\n\n"
        f"近期对话摘要：\n{history_summary or '（无）'}\n\n"
        f"用户本次输入：\n{query}\n\n"
        "请返回合法 JSON：\n"
        "{\n"
        '  "intent": "single_qa|multi_compare|paper_structure|scout_fetch|graph_insight|writing_copilot|general",\n'
        '  "requires_strict_grounding": true/false,\n'
        '  "reasoning": "一句话说明规划逻辑",\n'
        '  "steps": [\n'
        '    {"step": 1, "agent": "retriever|parser|scout|graph|writer|general", '
        '"goal": "该步要完成的具体子目标", "suggested_tool": "工具名或空", "depends_on": []}\n'
        "  ]\n"
        "}\n"
        "规则：\n"
        "1. 若涉及论文具体事实/参数/公式/对比，requires_strict_grounding 必须为 true，且先安排 retriever 检索原文证据；\n"
        "2. 若单篇或跨篇问题包含多个相对独立的子信息维度（例如同时询问「优化器超参」+「GPU 硬件型号数量」+「BLEU 分数」，或「参数量」+「Context Window」+「LayerNorm 位置」），"
        "务必拆分为多个聚焦不同英文/中文关键词的独立检索步骤（每步专注一个主题，避免把无关关键词混在一个查询中导致次要章节落榜）；\n"
        "3. 步骤控制在 1-5 步，简洁高效。"
    )

    try:
        data = llm.chat_json(prompt, model=settings.llm_model, temperature=0.1)
        steps = data.get("steps") or []
        if not steps:
            steps = [{"step": 1, "agent": "retriever", "goal": "检索相关文献片段", "depends_on": []}]
        return ExecutionPlan(
            intent=data.get("intent", "single_qa"),
            requires_strict_grounding=bool(data.get("requires_strict_grounding", True)),
            reasoning=data.get("reasoning", "检索文献证据并校验作答"),
            steps=steps,
        )
    except Exception:  # noqa: BLE001
        log.exception("Planner 规划失败，使用默认检索规划")
        return ExecutionPlan(
            intent="single_qa",
            requires_strict_grounding=True,
            reasoning="默认规划：先检索本地知识库原文，再进行溯源校验回答",
            steps=[{"step": 1, "agent": "retriever", "goal": "检索相关原文", "depends_on": []}],
        )


def replan_on_empty(query: str, prev_plan: ExecutionPlan) -> list[dict]:
    """当首选工具未找到足够结果时，动态生成补充检索策略。"""
    return [
        {
            "step": len(prev_plan.steps) + 1,
            "agent": "retriever",
            "goal": f"扩大关键词与语义范围重新检索：{query[:80]}",
            "suggested_tool": "search_papers" if "search_papers" in REGISTRY else "",
            "depends_on": [],
        }
    ]
