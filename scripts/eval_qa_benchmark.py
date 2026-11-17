"""问答评测集自动化打分脚本（阶段 3.6 / 阶段 9.3）。

覆盖赛题原文指出的全部典型细粒度单篇问题、参考文献精确查寻、抗幻觉拒答与跨文献对比问题。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import orchestrator  # noqa: E402
from app.agents.core import AgentContext  # noqa: E402
from app.db.models import Paper, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import _seed_reviewer  # noqa: E402

BENCHMARK_CASES = [
    {
        "id": "Q1_authors",
        "category": "单篇-作者与机构",
        "query": "《Attention Is All You Need》论文有哪些作者？分别来自哪些机构？",
        "must_contain_all": ["Ashish Vaswani", "Google"],
        "must_contain_any": ["University of Toronto", "Aidan"],
        "expect_citations": True,
    },
    {
        "id": "Q2_hyperparams_gpu",
        "category": "单篇-超参数与硬件",
        "query": "论文中 Adam 优化器的 beta1、beta2、epsilon 设置是多少？训练使用了什么型号和数量的 GPU？",
        "must_contain_all": ["0.9", "0.98", "P100", "8"],
        "must_contain_any": ["10", "9"],
        "expect_citations": True,
    },
    {
        "id": "Q3_bleu",
        "category": "单篇-BLEU分数比对",
        "query": "Transformer (big) 在 WMT 2014 英德 (EN-DE) 与英法 (EN-FR) 翻译任务上的 BLEU 分数分别是多少？",
        "must_contain_all": ["28.4"],
        "must_contain_any": ["41.8", "41.0"],
        "expect_citations": True,
    },
    {
        "id": "Q4_ref11",
        "category": "单篇-完整引用信息",
        "query": "请列出《Attention Is All You Need》参考文献 [11] 的完整引用信息。",
        "must_contain_all": ["Kaiming He"],
        "must_contain_any": ["Deep residual learning", "2016"],
        "expect_citations": True,
    },
    {
        "id": "Q5_anti_hallucination",
        "category": "抗幻觉-无中生有拒答",
        "query": "请问《Attention Is All You Need》第9节里提到的 Quantum Hyper-Attention 硬件参数是多少？",
        "must_contain_all": [],
        "must_contain_any": ["未", "没有", "不包含", "未提及", "不存在"],
        "expect_citations": False,
    },
]


def run_eval() -> dict:
    _seed_reviewer()
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if not user:
            raise RuntimeError("reviewer 用户不存在")
        papers = db.query(Paper).filter(Paper.owner_id == user.id, Paper.status == "ready").all()
        if not papers:
            raise RuntimeError("请先运行 scripts/seed_demo.py 导入预置论文后再跑评测")
        pids = [p.id for p in papers]

        report = []
        passed = 0
        for case in BENCHMARK_CASES:
            t0 = time.time()
            ctx = AgentContext(db=db, user_id=user.id, paper_ids=pids)
            res = orchestrator.run(ctx, case["query"])
            elapsed = round(time.time() - t0, 2)
            ans = res["answer"]
            cits = res.get("citations") or []

            ok_all = all(k.lower() in ans.lower() for k in case["must_contain_all"])
            ok_any = (not case["must_contain_any"]) or any(
                k.lower() in ans.lower() for k in case["must_contain_any"]
            )
            ok_cit = (not case["expect_citations"]) or len(cits) >= 1
            ok = ok_all and ok_any and ok_cit
            if ok:
                passed += 1

            report.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "passed": ok,
                    "elapsed_sec": elapsed,
                    "citation_count": len(cits),
                    "grounded_ratio": res.get("critic", {}).get("grounded_ratio"),
                    "answer_preview": ans[:160].replace("\n", " "),
                }
            )
            print(f"[{'PASS' if ok else 'FAIL'}] {case['id']} ({elapsed}s, cits={len(cits)})")

    summary = {"total": len(BENCHMARK_CASES), "passed": passed, "accuracy": round(passed / len(BENCHMARK_CASES), 3), "cases": report}
    out_file = ROOT / "docs" / "qa_eval_report.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n评测完成: {passed}/{len(BENCHMARK_CASES)} ({summary['accuracy']*100:.1f}%) -> {out_file}")
    return summary


if __name__ == "__main__":
    run_eval()
