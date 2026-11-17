"""Agent 运行时核心：工具注册（Function Calling）、执行上下文、轨迹事件。

所有专职 Agent（Retriever / Scout / Graph / Writer ...）都通过
`@tool` 注册能力，由 Orchestrator 统一调度；每一步思考、工具调用、
工具结果都会作为 TraceEvent 推送给前端“执行轨迹面板”，实现过程可审查。
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# 执行上下文与轨迹
# ----------------------------------------------------------------------------
@dataclass
class AgentContext:
    db: Session
    user_id: int
    conversation_id: int | None = None
    paper_ids: list[int] = field(default_factory=list)  # 用户限定的文献范围（空=全库）
    emit: Callable[[dict], None] | None = None  # SSE 推送回调
    trace: list[dict] = field(default_factory=list)
    # 本轮收集到的证据（供回答引用与 Critic 校验）：{ref_id: evidence}
    evidence: dict[str, dict] = field(default_factory=dict)
    scratch: dict[str, Any] = field(default_factory=dict)

    def event(self, type_: str, **data: Any) -> None:
        ev = {"type": type_, "ts": round(time.time(), 3), **data}
        self.trace.append(ev)
        if self.emit:
            try:
                self.emit(ev)
            except Exception:  # noqa: BLE001 - 推送失败不影响主流程
                log.exception("trace emit failed")

    def add_evidence(self, ev: dict) -> str:
        """登记一条证据，返回引用编号（如 E3）。同一 chunk 去重。"""
        key = f"{ev.get('paper_id')}:{ev.get('chunk_id', ev.get('block_id', len(self.evidence)))}"
        for rid, e in self.evidence.items():
            if e.get("_key") == key:
                return rid
        rid = f"E{len(self.evidence) + 1}"
        self.evidence[rid] = {**ev, "_key": key, "ref": rid}
        return rid


# ----------------------------------------------------------------------------
# 工具注册
# ----------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    agent: str  # 归属的专职 Agent，用于轨迹展示与分组授权

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict, agent: str = "general"):
    """装饰器：注册一个可被大模型 Function Calling 调用的工具。

    被装饰函数签名为 func(ctx: AgentContext, **kwargs) -> Any（可 JSON 序列化）。
    """

    def deco(func: Callable[..., Any]):
        REGISTRY[name] = Tool(name, description, parameters, func, agent)
        return func

    return deco


def tools_for(names: list[str] | None = None, agents: list[str] | None = None) -> list[Tool]:
    out = []
    for t in REGISTRY.values():
        if names is not None and t.name not in names:
            continue
        if agents is not None and t.agent not in agents:
            continue
        out.append(t)
    return out


def execute_tool(ctx: AgentContext, name: str, args: dict) -> Any:
    """执行工具并记录轨迹；异常转为错误信息返回给模型，让其自我纠正。"""
    t = REGISTRY.get(name)
    if not t:
        return {"error": f"未知工具 {name}"}
    ctx.event("tool_call", agent=t.agent, tool=name, args=args)
    start = time.time()
    try:
        sig = inspect.signature(t.func)
        valid = {k: v for k, v in args.items() if k in sig.parameters}
        result = t.func(ctx, **valid)
        ok = True
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s failed", name)
        result = {"error": f"{type(e).__name__}: {e}"}
        ok = False
    ctx.event(
        "tool_result",
        agent=t.agent,
        tool=name,
        ok=ok,
        elapsed=round(time.time() - start, 2),
        preview=_preview(result),
    )
    return result


def _preview(obj: Any, limit: int = 400) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


def to_tool_message(result: Any, limit: int = 12000) -> str:
    s = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "\n…（结果已截断）"
