"""问答评测集自动化打分脚本（阶段 3.6 / 阶段 9.3）。

全面覆盖赛题模块二列出的全部单篇细粒度问答与多篇跨文献联合推理问题：
- Q1: 作者与所属机构
- Q2: 超参数 β1/β2 设置与训练 GPU 型号及数量
- Q3: 特定任务 BLEU 分数比对
- Q4: 指定文献 [11] 完整引用信息
- Q5: 抗幻觉拒答机制（无依据问题绝不胡编）
- Q6 (多文对比): BERT 与 Transformer Encoder 的承袭与抛弃关系
- Q7 (多文对比): GPT-1 / 2 / 3 参数量演化、LN 位置调整细节及 Context Window 递增过程
- Q8-Q11 (T6 泛化): IEEE / ACM / Springer / Nature 四种排版真实论文的细粒度问答
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import orchestrator
from app.agents.core import AgentContext
from app.db.models import Paper, User
from app.db.session import SessionLocal
from app.main import _seed_reviewer

BENCHMARK_CASES = [
    {
        "id": "Q1_authors_affiliations",
        "files": ["1706.03762v7.pdf"],
        "category": "单篇-作者与机构",
        "query": "《Attention Is All You Need》论文有哪些作者？分别来自哪些机构？",
        "must_contain_all": ["Ashish Vaswani", "Google"],
        "must_contain_any": ["University of Toronto", "Aidan"],
        "expect_citations": True,
    },
    {
        "id": "Q2_hyperparams_and_gpu",
        "files": ["1706.03762v7.pdf"],
        "category": "单篇-超参数与GPU硬件",
        "query": "《Attention Is All You Need》中 Adam 优化器的 beta1、beta2、epsilon 设置是多少？训练使用了什么型号和数量的 GPU？",
        "must_contain_all": ["0.9", "0.98", "P100", "8"],
        "must_contain_any": ["10", "9"],
        "expect_citations": True,
    },
    {
        "id": "Q3_bleu_comparison",
        "files": ["1706.03762v7.pdf"],
        "category": "单篇-BLEU分数比对",
        "query": "Transformer (big) 在 WMT 2014 英德 (EN-DE) 与英法 (EN-FR) 翻译任务上的 BLEU 分数分别是多少？",
        "must_contain_all": ["28.4"],
        "must_contain_any": ["41.8", "41.0"],
        "expect_citations": True,
    },
    {
        "id": "Q4_reference_11",
        "files": ["1706.03762v7.pdf"],
        "category": "单篇-指定文献完整引用",
        "query": "请列出《Attention Is All You Need》参考文献 [11] 的完整引用信息。",
        "must_contain_all": ["Kaiming He"],
        "must_contain_any": ["Deep residual learning", "2016"],
        "expect_citations": True,
    },
    {
        "id": "Q5_anti_hallucination_refusal",
        "files": ["1706.03762v7.pdf"],
        "category": "抗幻觉-缺失证据拒答",
        "query": "请问《Attention Is All You Need》第9节里提到的 Quantum Hyper-Attention 硬件参数是多少？",
        "must_contain_all": [],
        "must_contain_any": ["未", "没有", "不包含", "未提及", "不存在"],
        "expect_citations": False,
    },
    {
        "id": "Q6_multi_bert_vs_transformer",
        "files": ["1706.03762v7.pdf", "BERT_1810.04805.pdf"],
        "category": "多篇对比-BERT与Transformer承袭与抛弃",
        "query": "对比 BERT 与原始 Transformer 在模型架构上的承袭与抛弃关系（保留了什么、抛弃了什么、训练目标有何变化）？",
        "must_contain_all": ["Encoder", "Decoder"],
        "must_contain_any": ["MLM", "Masked", "双向", "bidirectional"],
        "expect_citations": True,
    },
    {
        "id": "Q7_multi_gpt_ln_context_window",
        "files": [
            "1706.03762v7.pdf",
            "GPT1_OpenAI_2018.pdf",
            "GPT2_OpenAI_2019.pdf",
            "GPT3_2005.14165.pdf",
            "PreLN_2002.04745.pdf",
        ],
        "category": "多篇对比-GPT演化/LN位置/ContextWindow递增",
        "query": "请对比 GPT-1、GPT-2、GPT-3 的参数量演化、Context Window 递增过程，以及相比原始 Transformer 在 LayerNorm (Post-LN vs Pre-LN) 位置上的调整细节。",
        "must_contain_all": ["117", "1.5", "175", "512", "1024", "2048"],
        "must_contain_any": ["Pre-LN", "Post-LN", "LayerNorm"],
        "expect_citations": True,
    },
    {
        "id": "Q8_t6_ieee_segnet",
        "files": ["IEEE_TPAMI_SegNet_1511.00561.pdf"],
        "category": "T6泛化-IEEE TPAMI(SegNet)",
        "query": "SegNet 的编码器网络由多少个卷积层构成？与哪个网络的拓扑相同？解码器如何进行上采样？",
        "must_contain_all": ["13", "VGG16"],
        "must_contain_any": ["pooling indices", "池化索引", "max-pooling", "最大池化"],
        "expect_citations": True,
    },
    {
        "id": "Q9_t6_acm_xgboost",
        "files": ["ACM_KDD_XGBoost_1603.02754.pdf"],
        "category": "T6泛化-ACM KDD(XGBoost)",
        "query": "XGBoost 论文为处理稀疏数据和近似分裂查找分别提出了什么算法？",
        "must_contain_all": [],
        "must_contain_any": ["sparsity", "稀疏感知", "quantile sketch", "分位数"],
        "expect_citations": True,
    },
    {
        "id": "Q10_t6_springer_unet",
        "files": ["Springer_MICCAI_UNet_1505.04597.pdf"],
        "category": "T6泛化-Springer LNCS(U-Net)",
        "query": "U-Net 在 ISBI 2015 细胞追踪挑战赛 PhC-U373 与 DIC-HeLa 数据集上的平均 IOU 分别是多少？",
        "must_contain_all": ["92", "77"],
        "must_contain_any": ["IOU", "IoU"],
        "expect_citations": True,
    },
    {
        "id": "Q11_t6_nature_pangu",
        "files": ["Nature_PanguWeather_2211.02556.pdf"],
        "category": "T6泛化-Nature收录(Pangu-Weather)",
        "query": "Pangu-Weather 使用了多少年的再分析数据进行训练？采用了什么网络结构与层级时间聚合策略？",
        "must_contain_all": ["39"],
        "must_contain_any": ["Earth-Specific", "3DEST", "hierarchical temporal aggregation", "层级时间聚合"],
        "expect_citations": True,
    },
]


def run_eval() -> dict:
    _seed_reviewer()
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if not user:
            raise RuntimeError("reviewer 用户不存在")
        # 限定在核心论文集合上评测，保证单篇/多篇对比快速且精准
        papers = (
            db.query(Paper)
            .filter(Paper.owner_id == user.id, Paper.status == "ready", Paper.doc_type == "paper")
            .all()
        )
        if not papers:
            raise RuntimeError("请先运行 scripts/seed_demo.py 导入预置论文后再跑评测")
        by_name = {p.filename: p.id for p in papers}

        report = []
        passed = 0
        for case in BENCHMARK_CASES:
            t0 = time.time()
            # 每题限定在对应论文范围内（与前端「选定文献后提问」的真实使用方式一致）
            pids = [by_name[f] for f in case["files"] if f in by_name]
            if not pids:
                print(f"[SKIP] {case['id']}：文库中缺少 {case['files']}")
                continue
            ctx = AgentContext(db=db, user_id=user.id, paper_ids=pids)
            res = orchestrator.run(ctx, case["query"])
            elapsed = round(time.time() - t0, 2)
            ans = res["answer"]
            cits = res.get("citations") or []

            miss_all = [k for k in case["must_contain_all"] if k.lower() not in ans.lower()]
            ok_all = not miss_all
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
                    "papers": case["files"],
                    "passed": ok,
                    "elapsed_sec": elapsed,
                    "citation_count": len(cits),
                    "grounded_ratio": res.get("critic", {}).get("grounded_ratio"),
                    "missing": miss_all,
                    "answer_preview": ans[:220].replace("\n", " "),
                }
            )
            extra = f" miss={miss_all}" if miss_all else ""
            print(f"[{'PASS' if ok else 'FAIL'}] {case['id']} ({elapsed}s, cits={len(cits)}){extra}")

    summary = {
        "total": len(BENCHMARK_CASES),
        "passed": passed,
        "accuracy": round(passed / len(BENCHMARK_CASES), 3),
        "cases": report,
    }
    out_file = ROOT / "docs" / "qa_eval_report.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n评测完成: {passed}/{len(BENCHMARK_CASES)} ({summary['accuracy']*100:.1f}%) -> {out_file}")
    return summary


if __name__ == "__main__":
    run_eval()
