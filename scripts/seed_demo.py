"""预置演示数据脚本：为 reviewer 账号导入官方 PDF 与真实公开论文，并预构建引用图谱与推荐流。

演示语料全部为真实论文（arXiv 预印本 / OpenAI 官方公开 PDF），不包含任何人工合成内容：
- 跨文献对比（M2.2）：BERT、GPT-1、GPT-2、GPT-3、Pre-LN（On Layer Normalization in the Transformer Architecture）
- 多排版风格泛化（T6）：IEEE TPAMI（SegNet）、ACM KDD（XGBoost）、Springer LNCS / MICCAI（U-Net）、
  Nature 2023 收录论文 Pangu-Weather（arXiv 技术报告版；nature.com 原文 PDF 不允许脚本直接下载）

用法：.venv\\Scripts\\python.exe scripts\\seed_demo.py [--rebuild-graph]
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import orchestrator
from app.agents.core import AgentContext
from app.api.papers import ingest_pdf_bytes
from app.api.reader import _translate_block_batch
from app.core.logging import setup_logging
from app.db.models import (
    ArxivPaper,
    Block,
    CitationGraph,
    Conversation,
    Paper,
    Recommendation,
    Translation,
    User,
)
from app.db.session import Base, SessionLocal, engine
from app.graph import service as graph_svc
from app.main import _seed_reviewer
from app.parsing.pipeline import parse_paper, register_index_hook
from app.rag.chunker import build_chunks_for_paper
from app.scout import service as scout_svc

setup_logging()
log = logging.getLogger("seed_demo")

OFFICIAL_DIR = ROOT / "data" / "official_pdf"
REAL_CORPUS_DIR = OFFICIAL_DIR / "real_corpus"

# (本地文件名, 公开下载地址, 用途说明)
REAL_CORPUS: list[tuple[str, str, str]] = [
    ("BERT_1810.04805.pdf", "https://arxiv.org/pdf/1810.04805v2", "M2.2 BERT vs Transformer Encoder"),
    (
        "GPT1_OpenAI_2018.pdf",
        "https://cdn.openai.com/research-covers/language-unsupervised/language_understanding_paper.pdf",
        "M2.2 GPT 系列演化",
    ),
    (
        "GPT2_OpenAI_2019.pdf",
        "https://cdn.openai.com/better-language-models/language_models_are_unsupervised_multitask_learners.pdf",
        "M2.2 GPT 系列演化 / LN 位置调整",
    ),
    ("GPT3_2005.14165.pdf", "https://arxiv.org/pdf/2005.14165v4", "M2.2 GPT 系列参数量 / Context Window"),
    ("PreLN_2002.04745.pdf", "https://arxiv.org/pdf/2002.04745v2", "M2.2 LN 位置调整（Post-LN vs Pre-LN）"),
    ("IEEE_TPAMI_SegNet_1511.00561.pdf", "https://arxiv.org/pdf/1511.00561v3", "T6 IEEE Transactions 排版"),
    ("ACM_KDD_XGBoost_1603.02754.pdf", "https://arxiv.org/pdf/1603.02754v3", "T6 ACM sigconf 排版"),
    ("Springer_MICCAI_UNet_1505.04597.pdf", "https://arxiv.org/pdf/1505.04597v1", "T6 Springer LNCS 排版"),
    ("Nature_PanguWeather_2211.02556.pdf", "https://arxiv.org/pdf/2211.02556v1", "T6 Nature 收录论文（预印本）"),
]


GRAPH_FILES = {
    "1706.03762v7.pdf",
    "BERT_1810.04805.pdf",
    "GPT1_OpenAI_2018.pdf",
    "GPT2_OpenAI_2019.pdf",
    "GPT3_2005.14165.pdf",
    "PreLN_2002.04745.pdf",
}


def ensure_real_corpus() -> list[Path]:
    """确保真实论文语料已下载到 data/official_pdf/real_corpus/，缺失则从公开地址下载。"""
    REAL_CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fname, url, purpose in REAL_CORPUS:
        p = REAL_CORPUS_DIR / fname
        if not p.exists() or p.stat().st_size < 10_000:
            log.info("下载真实论文 %s（%s）", fname, purpose)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ScholarAgent/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = resp.read()
            except Exception as e:  # noqa: BLE001
                log.warning("下载失败，跳过 %s：%s", fname, e)
                continue
            if not data.startswith(b"%PDF"):
                log.warning("返回内容不是 PDF，跳过 %s", fname)
                continue
            p.write_bytes(data)
        paths.append(p)
    return paths


def seed(rebuild_graph: bool = False, reparse_papers: bool = False) -> None:
    Base.metadata.create_all(bind=engine)
    _seed_reviewer()
    register_index_hook(build_chunks_for_paper)

    corpus = ensure_real_corpus()

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if not user:
            raise RuntimeError("reviewer 初始化失败")
        existing_by_name = {p.filename: p.id for p in db.query(Paper).filter(Paper.owner_id == user.id).all()}

    # 1. 导入官方 v7、真实对比/多排版语料与 541 页教材
    #    （v1 与 v1_img 保留在 data/official_pdf/，供现场演示 T1 语义去重与 T3 扫描件 OCR 上传）
    all_to_seed = [
        OFFICIAL_DIR / "1706.03762v7.pdf",
        *corpus,
        OFFICIAL_DIR / "UnderstandingDeepLearning_02_09_26_C.pdf",
    ]
    for pdf_path in all_to_seed:
        if not pdf_path.exists():
            continue
        is_book = pdf_path.name.startswith("UnderstandingDeepLearning")
        if pdf_path.name in existing_by_name:
            if not reparse_papers or is_book:
                continue
            pid = existing_by_name[pdf_path.name]
            log.info("正在重新解析并重建索引: %s (id=%d)", pdf_path.name, pid)
            parse_paper(pid, enable_vlm_formulas=(pdf_path.name == "1706.03762v7.pdf"))
            continue
        log.info("正在导入并解析: %s", pdf_path.name)
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "reviewer").first()
            pdict, _ = ingest_pdf_bytes(
                db, user.id, pdf_path.name, pdf_path.read_bytes(), source="preset", skip_dedup=True, auto_parse=False
            )
            pid = pdict["id"]
        parse_paper(pid, enable_vlm_formulas=(pdf_path.name == "1706.03762v7.pdf"))

    # 2. 预构建局域引用图谱、领域综述与 Future Work（模块五开箱即看）
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if rebuild_graph:
            db.query(CitationGraph).filter(CitationGraph.user_id == user.id).delete()
            db.commit()
        if not db.query(CitationGraph).filter(CitationGraph.user_id == user.id).first():
            papers = (
                db.query(Paper)
                .filter(Paper.owner_id == user.id, Paper.status == "ready", Paper.doc_type == "paper")
                .all()
            )
            # 图谱聚焦 Transformer 预训练语言模型谱系（多排版泛化语料属于不同领域，不纳入同一局域图谱）
            papers = [p for p in papers if p.filename in GRAPH_FILES]
            if len(papers) >= 2:
                log.info("正在预构建引用拓扑图谱、领域综述与 Future Work（%d 篇）...", len(papers))
                g = graph_svc.build_citation_graph(
                    db,
                    user.id,
                    paper_ids=[p.id for p in papers],
                    name="Transformer 预训练语言模型演进引用图谱",
                )
                graph_svc.generate_survey(db, g)
                graph_svc.explore_future_work(db, g)

        # 3. 预构建 Sickle arXiv 动态追踪与推荐流（模块四开箱即看）
        if db.query(ArxivPaper).count() < 15 or not db.query(Recommendation).filter(Recommendation.user_id == user.id).first():
            added = scout_svc.track_arxiv_with_sickle(db, categories=["cs.CL", "cs.LG", "cs.AI"], days=4)
            recs = scout_svc.generate_recommendations(db, user.id, top_n=10)
            log.info("预置完成：Sickle 新增 %d 篇 arXiv 论文，生成 %d 条个性化推荐", added, len(recs))

        # 4. 为 reviewer 账号预置一篇双语对照翻译（1706.03762v7 第1-6页）与一条带轨迹问答示例，确保登录即开箱演示
        v7 = db.query(Paper).filter(Paper.owner_id == user.id, Paper.filename == "1706.03762v7.pdf").first()
        if v7:
            has_tr = db.query(Translation).filter(Translation.paper_id == v7.id).first()
            if not has_tr:
                blks = (
                    db.query(Block)
                    .filter(Block.paper_id == v7.id, Block.page <= 6)
                    .order_by(Block.page.asc(), Block.order.asc())
                    .all()
                )
                for i in range(0, len(blks), 12):
                    batch = blks[i : i + 12]
                    tmap = _translate_block_batch(batch)
                    for b in batch:
                        db.add(Translation(paper_id=v7.id, block_id=b.id, lang="zh", text=tmap.get(b.id, b.content)))
                db.commit()
                log.info("已预翻译 1706.03762v7.pdf 前6页共 %d 个内容块", len(blks))

            if not db.query(Conversation).filter(Conversation.user_id == user.id).first():
                q_demo = "《Attention Is All You Need》中 Adam 优化器的 beta1、beta2 是多少？训练使用了什么型号和数量的 GPU？WMT 2014 英德和英法 BLEU 分数分别是多少？"
                conv = Conversation(user_id=user.id, title=q_demo[:32], paper_ids=[v7.id])
                db.add(conv)
                db.commit()
                db.refresh(conv)
                ctx = AgentContext(db=db, user_id=user.id, conversation_id=conv.id, paper_ids=[v7.id])
                orchestrator.run(ctx, q_demo)
                log.info("已预生成评审演示对话与 Agent 执行轨迹")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-graph", action="store_true", help="删除已有图谱并基于当前文库重建")
    ap.add_argument("--reparse", action="store_true", help="用最新解析器重新解析已有论文（跳过541页教材）")
    ns = ap.parse_args()
    seed(rebuild_graph=ns.rebuild_graph, reparse_papers=ns.reparse)
