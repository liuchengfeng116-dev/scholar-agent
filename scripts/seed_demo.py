"""预置演示数据脚本：为 reviewer 账号导入官方 PDF 与多篇关联经典论文（用于图谱、跨文对比与泛化展示）。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.papers import ingest_pdf_bytes  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.models import Paper, User  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.main import _seed_reviewer  # noqa: E402
from app.parsing.pipeline import parse_paper, register_index_hook  # noqa: E402
from app.rag.chunker import build_chunks_for_paper  # noqa: E402
from app.scout import service as scout_svc  # noqa: E402

setup_logging()
log = logging.getLogger("seed_demo")

# 经典演进论文集合（覆盖 Transformer -> BERT -> GPT-1/2/3 -> RoBERTa -> Longformer -> RAG -> InstructGPT -> LLaMA 等）
# 用于展示 M2.2 跨文献对比、M5 批量引用图谱、M4 追踪推荐与 T6 多领域泛化
CLASSIC_ARXIV_IDS = [
    ("1810.04805", "BERT_Pretraining.pdf"),
    ("1907.11692", "RoBERTa.pdf"),
    ("2004.05150", "Longformer.pdf"),
    ("2005.14165", "GPT3_Language_Models_are_FewShot_Learners.pdf"),
    ("2005.11401", "RAG_Retrieval_Augmented_Generation.pdf"),
    ("2002.04745", "On_Layer_Normalization_PreLN.pdf"),
    ("2106.09685", "LoRA_LowRank_Adaptation.pdf"),
    ("2203.02155", "InstructGPT_RLHF.pdf"),
    ("2210.03629", "ReAct_Reasoning_and_Acting.pdf"),
    ("2302.13971", "LLaMA_Open_Foundation_Models.pdf"),
]


def seed(include_remote: bool = True) -> None:
    Base.metadata.create_all(bind=engine)
    _seed_reviewer()
    register_index_hook(build_chunks_for_paper)

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if not user:
            raise RuntimeError("reviewer 初始化失败")
        existing_names = {p.filename for p in db.query(Paper).filter(Paper.owner_id == user.id).all()}

    # 1. 导入官方 PDF（注意：1706.03762v1.pdf 留给用户/评委现场上传演示 T1 语义去重！）
    official_files = [
        ROOT / "data/official_pdf/1706.03762v7.pdf",
        ROOT / "data/official_pdf/UnderstandingDeepLearning_02_09_26_C.pdf",
    ]
    for pdf_path in official_files:
        if not pdf_path.exists() or pdf_path.name in existing_names:
            continue
        log.info("导入官方文件: %s", pdf_path.name)
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "reviewer").first()
            pdict, _ = ingest_pdf_bytes(
                db, user.id, pdf_path.name, pdf_path.read_bytes(), source="official", skip_dedup=True
            )
            pid = pdict["id"]
        parse_paper(pid, enable_vlm_formulas=(pdf_path.name == "1706.03762v7.pdf"))

    # 2. 抓取并导入经典多文对比与图谱测试论文（T5/T6/M2.2/M5）
    if include_remote:
        for aid, fname in CLASSIC_ARXIV_IDS:
            if fname in existing_names:
                continue
            dl = scout_svc.download_pdf_bytes(f"https://arxiv.org/pdf/{aid}.pdf", aid)
            if not dl:
                log.warning("跳过下载失败: %s", aid)
                continue
            raw, _ = dl
            log.info("导入经典论文: %s (%d bytes)", fname, len(raw))
            with SessionLocal() as db:
                user = db.query(User).filter(User.username == "reviewer").first()
                pdict, _ = ingest_pdf_bytes(db, user.id, fname, raw, source="preset_arxiv", skip_dedup=True)
                pid = pdict["id"]
            parse_paper(pid, enable_vlm_formulas=False)

        # 3. 初始化 arXiv 动态推荐流
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "reviewer").first()
            added = scout_svc.track_arxiv_with_sickle(db, categories=["cs.CL", "cs.LG", "cs.AI"], days=4)
            recs = scout_svc.generate_recommendations(db, user.id, top_n=10)
            log.info("Sickle 追踪新增 %d 篇 arXiv 论文，生成 %d 条个性化推荐", added, len(recs))


if __name__ == "__main__":
    seed(include_remote="--local-only" not in sys.argv)
