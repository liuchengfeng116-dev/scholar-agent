"""Guardrails 安全防护与学术伦理边界校验。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"忽略(上面|之前|所有)(的)?(指令|提示|规则)"),
    re.compile(r"(reveal|print|show)\s+(your\s+)?system\s+prompt", re.I),
]

_UNETHICAL_PATTERNS = [
    re.compile(r"(伪造|编造|捏造|篡改).*(实验数据|结果|参考文献|引用)"),
    re.compile(r"(fabricate|fake|forge)\s+.*(data|results|references|citations)", re.I),
]

MAX_QUERY_CHARS = 6000


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str = ""
    sanitized_query: str = ""
    flags: list[str] | None = None


def check_input(query: str) -> GuardrailResult:
    """输入侧防护：空输入/超长检测、提示词注入拦截、学术伦理违规拦截。"""
    text = (query or "").strip()
    if not text:
        return GuardrailResult(False, "请输入有效的研究问题或指令。")
    if len(text) > MAX_QUERY_CHARS:
        return GuardrailResult(False, f"输入长度超过上限（{MAX_QUERY_CHARS} 字符），请精简后重试。")

    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return GuardrailResult(
                False, "检测到疑似提示词注入指令，已由 Guardrails 拦截。请直接提出科研问题。"
            )

    for pat in _UNETHICAL_PATTERNS:
        if pat.search(text):
            return GuardrailResult(
                False,
                "根据学术伦理防护策略，系统不支持伪造实验数据或编造虚假参考文献。"
                "如需无幻觉写作框架，请提供真实研究思路，系统将仅引用经过校验的真实文献。",
            )

    return GuardrailResult(True, sanitized_query=text, flags=[])


def check_output(
    answer: str,
    evidence: dict[str, dict],
    *,
    strict_grounding: bool = True,
) -> tuple[str, list[str]]:
    """输出侧防护：
    1. 严格溯源问答但没有任何证据命中时，强制给出明确拒答/缺失提示，杜绝凭空编造。
    2. 过滤无效的虚假引用标签（如引用了不存在的 [E99]）。
    """
    warnings: list[str] = []
    valid_refs = set(evidence.keys())

    # 将 <E8>、【E8】 及合并写法 [E1, E7] / [E1、E7] 统一规范化为独立的 [E1][E7]
    def _expand_group(m: re.Match[str]) -> str:
        refs = re.findall(r"E\d+", m.group(0))
        return "".join(f"[{r}]" for r in refs)

    normalized = re.sub(
        r"[\[<【]\s*E\d+(?:\s*[,，、;；]\s*E\d+)*\s*[\]>】]",
        _expand_group,
        answer or "",
    )

    # 清理不存在的引用编号
    def _clean_ref(match: re.Match[str]) -> str:
        ref = match.group(1)
        if ref in valid_refs:
            return match.group(0)
        warnings.append(f"已剔除无效引用标记 [{ref}]")
        return ""

    cleaned = re.sub(r"\[(E\d+)\]", _clean_ref, normalized)

    if strict_grounding and not evidence:
        refusal = (
            "抱歉，当前已入库的文献中未检索到与该问题直接相关的原文证据。"
            "为遵循抗幻觉与严谨溯源原则，系统不作无依据猜测。\n\n"
            "建议：\n1. 检查左侧是否已勾选目标文献，或确认相关 PDF 已完成解析；\n"
            "2. 若文献尚未入库，可让我通过「检索下载」自动从 arXiv 抓取。"
        )
        warnings.append("证据库未命中，触发抗幻觉拒答机制")
        return refusal, warnings

    return cleaned.strip(), warnings
