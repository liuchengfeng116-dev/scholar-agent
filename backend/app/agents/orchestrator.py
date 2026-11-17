"""Multi-Agent Orchestrator：串联 Memory → Planner → 专职 Agent 工具调用 → Critic → Guardrails。"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agents import critic, guardrails, memory, planner
from app.agents.core import REGISTRY, AgentContext, execute_tool, to_tool_message, tools_for
from app.core.config import settings
from app.db.models import Message, Paper
from app.llm import client as llm

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6


def _summarize_papers(ctx: AgentContext) -> str:
    q = ctx.db.query(Paper).filter(Paper.owner_id == ctx.user_id, Paper.status == "ready")
    if ctx.paper_ids:
        q = q.filter(Paper.id.in_(ctx.paper_ids))
    papers = q.order_by(Paper.id.desc()).limit(25).all()
    if not papers:
        return "（知识库中暂无已解析完成的论文）"
    return "\n".join(
        f"- [paper_id={p.id}] {p.title or p.filename} ({p.year or '?'}, {p.page_count}页)"
        for p in papers
    )


def _execute_tool_loop(
    ctx: AgentContext,
    query: str,
    plan: planner.ExecutionPlan,
    short_summary: str,
    recent_msgs: list[dict],
    long_memories: list[dict],
    paper_summary: str,
) -> str:
    """带 Function Calling 的执行循环。"""
    available_tools = tools_for()
    tool_specs = [t.spec() for t in available_tools] if available_tools else None

    plan_text = "\n".join(
        f"步骤{s.get('step')}[{s.get('agent')}]: {s.get('goal')}"
        + (f" (建议工具: {s['suggested_tool']})" if s.get("suggested_tool") else "")
        for s in plan.steps
    )
    mem_text = "\n".join(f"- {m['content']}" for m in long_memories) or "无"

    system_prompt = (
        "你是 ScholarAgent（智研助手），一个严谨的科研协同 Agent 系统。\n"
        "请严格按照规划执行工具调用，并基于检索到的真实原文作答。\n\n"
        "【核心规范】\n"
        "1. 抗幻觉：凡论文中的作者、机构、超参数(如β1/β2)、硬件(GPU型号/数量)、BLEU/准确率数值、"
        "公式或结论，必须先调用检索工具获取原文后再回答；\n"
        "2. 溯源标记：工具返回每条证据时都会给出 ref（如 E1、E2），"
        "在正文中引用该证据时必须紧跟标记 `[E1]`、`[E2]`，不可编造编号；\n"
        "3. 如果工具未返回相关证据，明确说明“现有文献未涵盖”，绝不猜测或编造；\n"
        "4. 多篇对比类问题必须对每一篇被对比的文献分别检索或核对，使用结构化 Markdown 表格呈现差异，且**每一篇文献的观点/数值后均须附上其对应的 `[E#]` 引用标记**。\n\n"
        f"【可用文献范围】\n{paper_summary}\n\n"
        f"【用户长期记忆】\n{mem_text}\n\n"
        f"【Planner 规划步骤】\n{plan_text}"
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if short_summary:
        messages.append({"role": "system", "content": f"早期对话摘要：{short_summary}"})
    messages.extend(recent_msgs)
    messages.append({"role": "user", "content": query})

    draft_answer = ""
    for round_idx in range(MAX_TOOL_ROUNDS):
        msg = llm.chat(
            messages,
            model=settings.llm_model,
            temperature=0.2,
            tools=tool_specs,
        )
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            draft_answer = (msg.content or "").strip()
            # 如果需要严格溯源但还没触发任何证据检索，提示模型先检索
            if (
                plan.requires_strict_grounding
                and not ctx.evidence
                and REGISTRY
                and round_idx == 0
            ):
                ctx.event("replan", reason="未调用检索工具，触发强制溯源重规划")
                extra = planner.replan_on_empty(query, plan)
                messages.append({"role": "assistant", "content": draft_answer})
                messages.append(
                    {
                        "role": "user",
                        "content": f"请务必先调用检索工具核查原文证据（{extra[0]['goal']}），再给出带 [E#] 引用的回答。",
                    }
                )
                continue
            break

        # 记录 assistant tool_calls 消息
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            res = execute_tool(ctx, tc.function.name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": to_tool_message(res),
                }
            )
    else:
        # 达到最大工具轮次，强制生成总结
        messages.append({"role": "user", "content": "请基于已获取的证据立即给出完整回答，附带 [E#] 引用。"})
        draft_answer = (llm.chat(messages, model=settings.llm_model, temperature=0.2).content or "").strip()

    return draft_answer


def run(ctx: AgentContext, raw_query: str) -> dict[str, Any]:
    """执行完整的多 Agent 协作闭环，返回最终结果字典。"""
    # 1. 输入侧 Guardrails
    gr = guardrails.check_input(raw_query)
    if not gr.allowed:
        ctx.event("guardrail_blocked", reason=gr.reason)
        return {
            "answer": gr.reason,
            "citations": [],
            "trace": ctx.trace,
            "critic": {"grounded_ratio": 1.0, "warnings": [gr.reason], "claim_checks": []},
        }
    query = gr.sanitized_query

    # 2. 写入用户消息
    if ctx.conversation_id:
        ctx.db.add(Message(conversation_id=ctx.conversation_id, role="user", content=query))
        ctx.db.commit()

    # 3. 加载短期与长期记忆
    short_summary, recent_msgs = memory.load_short_term(ctx.db, ctx.conversation_id)
    # 去掉刚写入的那一条，避免重复出现在 messages 尾部
    if recent_msgs and recent_msgs[-1]["role"] == "user" and recent_msgs[-1]["content"] == query:
        recent_msgs = recent_msgs[:-1]
    long_memories = memory.load_long_term(ctx.db, ctx.user_id)
    ctx.event(
        "memory_loaded",
        short_turns=len(recent_msgs),
        has_summary=bool(short_summary),
        long_items=len(long_memories),
    )

    # 4. Planner 任务规划
    paper_summary = _summarize_papers(ctx)
    plan = planner.make_plan(
        query,
        paper_summary=paper_summary,
        memories=long_memories,
        history_summary=short_summary,
    )
    ctx.event(
        "plan",
        intent=plan.intent,
        strict_grounding=plan.requires_strict_grounding,
        reasoning=plan.reasoning,
        steps=plan.steps,
    )

    # 5. 多工具执行
    draft = _execute_tool_loop(
        ctx, query, plan, short_summary, recent_msgs, long_memories, paper_summary
    )

    # 6. 输出侧 Guardrails
    safe_answer, guard_warnings = guardrails.check_output(
        draft, ctx.evidence, strict_grounding=plan.requires_strict_grounding
    )

    # 7. Critic 逐句溯源校验
    report = critic.verify(
        query, safe_answer, ctx.evidence, strict_grounding=plan.requires_strict_grounding
    )
    all_warnings = guard_warnings + report.warnings
    ctx.event(
        "critic",
        grounded_ratio=report.grounded_ratio,
        claim_count=len(report.claim_checks),
        warnings=all_warnings,
    )
    for cit in report.used_citations:
        ctx.event("citation", **cit)

    # 8. 持久化助手回复与更新记忆
    if ctx.conversation_id:
        ctx.db.add(
            Message(
                conversation_id=ctx.conversation_id,
                role="assistant",
                content=report.verified_answer,
                citations=report.used_citations,
                trace=ctx.trace,
            )
        )
        ctx.db.commit()
        memory.compress_conversation(ctx.db, ctx.conversation_id)

    memory.extract_and_update(ctx.db, ctx.user_id, query, report.verified_answer)

    return {
        "answer": report.verified_answer,
        "citations": report.used_citations,
        "trace": ctx.trace,
        "critic": {
            "grounded_ratio": report.grounded_ratio,
            "claim_checks": report.claim_checks,
            "warnings": all_warnings,
        },
    }
