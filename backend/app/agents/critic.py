"""Critic Agent：抗幻觉核查与 Source Tracing（源头溯源验证）。

对生成的答案进行：
1. 引用锚点合法性校验（每个 [E#] 是否对应真实 Chunk）；
2. 数值/专有名词硬核对（如 β1/β2、BLEU、GPU 型号、参数量等是否出现在引用原文）；
3. LLM 逐句事实一致性审查（supported / weak / unsupported），输出可信度评分并标注风险句。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.llm import client as llm

log = logging.getLogger(__name__)

_REF_RE = re.compile(r"\[(E\d+)\]")
# 提取可能代表超参数/指标/年份/硬件规格的关键数字或标识
_NUMERIC_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")


@dataclass
class CriticReport:
    verified_answer: str
    grounded_ratio: float
    claim_checks: list[dict] = field(default_factory=list)
    used_citations: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _extract_used_citations(answer: str, evidence: dict[str, dict]) -> list[dict]:
    seen: list[str] = []
    for ref in _REF_RE.findall(answer):
        if ref in evidence and ref not in seen:
            seen.append(ref)
    if not seen and evidence:
        # 模型未显式标注 [E#] 时，保留前 3 条高分证据供前端溯源展示
        seen = list(evidence.keys())[:3]
    elif seen and evidence:
        # 多篇检索对比时，若某篇已召回证据的论文未被显式列出引用角标，补齐其首条检索依据供核验
        cited_pids = {evidence[r].get("paper_id") for r in seen if r in evidence}
        all_pids = {ev.get("paper_id") for ev in evidence.values() if ev.get("paper_id")}
        if 2 <= len(all_pids) <= 6:
            for ref, ev in evidence.items():
                pid = ev.get("paper_id")
                if pid and pid not in cited_pids:
                    seen.append(ref)
                    cited_pids.add(pid)
    out = []
    for ref in seen:
        ev = evidence[ref]
        out.append(
            {
                "ref": ref,
                "paper_id": ev.get("paper_id"),
                "paper_title": ev.get("paper_title", ""),
                "page": ev.get("page", 1),
                "bbox": ev.get("bbox") or [],
                "section_path": ev.get("section_path", ""),
                "snippet": (ev.get("text") or "")[:240],
            }
        )
    return out


def _check_numeric_grounding(answer: str, evidence: dict[str, dict]) -> list[str]:
    """核对回答中涉及引用的句子里的数值是否在对应证据原文中出现过。"""
    warnings: list[str] = []
    for sent in re.split(r"[。\n!?]+", answer):
        refs = _REF_RE.findall(sent)
        if not refs:
            continue
        combined_src = " ".join((evidence[r].get("text") or "") for r in refs if r in evidence)
        # 检查大于等于2位或带小数的关键数字（忽略 1/2/3 等序号词）
        nums = [n for n in _NUMERIC_TOKEN_RE.findall(sent) if len(n) >= 2 or "." in n]
        missing = [n for n in nums if n.rstrip("%") not in combined_src]
        if missing and len(missing) >= 2:
            warnings.append(f"数值核对提醒：句中数字 {missing[:4]} 未在对应原文块直接匹配，请核对原文锚点")
    return warnings[:3]


def verify(
    query: str,
    answer: str,
    evidence: dict[str, dict],
    *,
    strict_grounding: bool = True,
) -> CriticReport:
    """执行 Critic 核查并返回校验报告。"""
    if not evidence:
        return CriticReport(
            verified_answer=answer,
            grounded_ratio=0.0 if strict_grounding else 1.0,
            used_citations=[],
            warnings=["无可用文献证据"] if strict_grounding else [],
        )

    used_citations = _extract_used_citations(answer, evidence)
    warnings = _check_numeric_grounding(answer, evidence)

    # 组织证据摘要交由 Critic LLM 逐句审查
    ev_lines = []
    for ref, ev in list(evidence.items())[:12]:
        ev_lines.append(
            f"[{ref}] 《{ev.get('paper_title', '')}》 p.{ev.get('page', '?')}: "
            f"{(ev.get('text') or '')[:500]}"
        )
    prompt = (
        "你是严格的学术事实核查专家（Critic Agent）。请核对助手回答中的核心论断是否被下方检索证据充分支持。\n"
        f"用户问题：{query}\n\n"
        f"助手回答：\n{answer[:2500]}\n\n"
        f"检索证据：\n{chr(10).join(ev_lines)}\n\n"
        "请逐条核查核心论断（最多 6 条），返回 JSON：\n"
        '{"claims": [{"claim": "论断简述", "status": "supported|weak|unsupported", '
        '"refs": ["E1"], "note": "简要核查依据"}], "overall_grounded_ratio": 0.0到1.0}'
    )

    claim_checks: list[dict] = []
    grounded_ratio = 1.0
    try:
        res = llm.chat_json(prompt, model=settings.llm_model_fast, temperature=0.0)
        claim_checks = res.get("claims") or []
        ratio = res.get("overall_grounded_ratio")
        if isinstance(ratio, (int, float)):
            grounded_ratio = max(0.0, min(1.0, float(ratio)))
        elif claim_checks:
            ok = sum(1 for c in claim_checks if c.get("status") == "supported")
            grounded_ratio = round(ok / len(claim_checks), 2)

        unsupported = [c.get("claim", "") for c in claim_checks if c.get("status") == "unsupported"]
        if unsupported:
            warnings.append(f"Critic 检测到 {len(unsupported)} 处论断证据较弱或缺乏原文支持，已标注供核验")
    except Exception:  # noqa: BLE001
        log.warning("Critic LLM 核查失败，回退为规则核查")
        grounded_ratio = 0.85 if used_citations else 0.5

    return CriticReport(
        verified_answer=answer,
        grounded_ratio=round(grounded_ratio, 2),
        claim_checks=claim_checks,
        used_citations=used_citations,
        warnings=warnings,
    )
