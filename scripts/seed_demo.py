"""预置演示数据脚本：为 reviewer 账号导入官方 PDF、T6 多排版风格（IEEE/ACM/Springer/Nature）及经典演进论文，并预构建引用图谱与推荐流。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.papers import ingest_pdf_bytes  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.models import CitationGraph, Paper, User  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.graph import service as graph_svc  # noqa: E402
from app.main import _seed_reviewer  # noqa: E402
from app.parsing.pipeline import parse_paper, register_index_hook  # noqa: E402
from app.rag.chunker import build_chunks_for_paper  # noqa: E402
from app.scout import service as scout_svc  # noqa: E402

setup_logging()
log = logging.getLogger("seed_demo")


def _ensure_multi_paper_and_style_corpus(out_dir: Path) -> list[Path]:
    """生成/准备覆盖 M2.2 跨文对比（BERT/GPT-1-2-3/LN位置/Context Window）与 T6 多排版风格（IEEE/ACM/Springer/Nature）的标准测试 PDF。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        (
            "IEEE_Trans_BERT_vs_Transformer_2019.pdf",
            "two_col",
            "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding\n"
            "Jacob Devlin, Ming-Wei Chang, Kenton Lee, Kristina Toutanova\n"
            "Google AI Language (IEEE/ACL Formatting Style, 2019)\n\n"
            "Abstract\n"
            "We introduce BERT, which stands for Bidirectional Encoder Representations from Transformers. "
            "In terms of architectural inheritance and abandonment compared with the original Transformer (Vaswani et al., 2017) [1]: "
            "BERT inherits the multi-layer bidirectional Self-Attention Encoder stack, residual connections, Layer Normalization (Post-LN), and Position-wise Feed-Forward Networks. "
            "However, BERT completely abandons the autoregressive Transformer Decoder stack and encoder-decoder cross-attention, replacing causal left-to-right modeling with Masked Language Modeling (MLM) and Next Sentence Prediction (NSP). "
            "BERT-Base has L=12 layers, H=768, A=12 heads (110M parameters), and BERT-Large has L=24, H=1024, A=16 (340M parameters), with a maximum context window of 512 tokens.",
            "1 Introduction\n"
            "Pre-trained language representations have proven effective for NLP [1, 2]. Unlike GPT-1 [2] which uses a unidirectional left-to-right Transformer Decoder, BERT employs bidirectional self-attention across all layers.\n\n"
            "2 Architectural Comparison with Transformer\n"
            "The original Transformer [1] uses an Encoder-Decoder architecture for machine translation. BERT retains only the Encoder stack to extract contextual representations and discards the Decoder stack.\n\n"
            "3 Conclusion and Future Work\n"
            "In future work, we plan to extend bidirectional pre-training to longer context windows beyond 512 tokens and explore more efficient pre-training objectives without NSP.\n\n"
            "References\n"
            "[1] Ashish Vaswani et al. Attention Is All You Need. NeurIPS, 2017. arXiv:1706.03762\n"
            "[2] Alec Radford et al. Improving Language Understanding by Generative Pre-Training (GPT-1). OpenAI, 2018.\n",
        ),
        (
            "ACM_Style_GPT1_GPT2_GPT3_Evolution_2020.pdf",
            "two_col",
            "Language Models are Few-Shot Learners: Evolution from GPT-1 and GPT-2 to GPT-3\n"
            "Tom B. Brown, Benjamin Mann, Nick Ryder, Dario Amodei, Ilya Sutskever\n"
            "OpenAI (ACM Conference Double-Column Style, NeurIPS 2020)\n\n"
            "Abstract\n"
            "We trace the scaling evolution of Generative Pre-trained Transformers from GPT-1 [2] and GPT-2 [3] to GPT-3, built upon the Transformer architecture [1]. "
            "1) Parameter scale evolution: GPT-1 (2018) has 117M (0.117B) parameters (12 layers, d_model=768); GPT-2 (2019) scales up to 1.5B parameters (48 layers, d_model=1600); GPT-3 (2020) scales to 175B parameters (96 layers, d_model=12288, 96 heads). "
            "2) Context Window progression: GPT-1 uses a context window of 512 tokens; GPT-2 doubles the context window to 1024 tokens; GPT-3 further expands the context window to 2048 tokens (512 -> 1024 -> 2048). "
            "3) LayerNorm (LN) position adjustment: Original Transformer [1] and GPT-1 use Post-LN (LayerNorm placed after residual addition: LN(x + Sublayer(x))). Starting from GPT-2 and inherited by GPT-3, LayerNorm is moved to the input of each sub-block (Pre-LN: x + Sublayer(LN(x))), and an additional LayerNorm is added after the final self-attention block, stabilizing deep training without warmup.",
            "1 Scaling Laws and Architecture\n"
            "Building upon Attention Is All You Need [1] and Pre-LN analysis [4], GPT-3 uses alternating dense and locally banded sparse attention layers.\n\n"
            "2 Conclusion and Future Work\n"
            "Future work includes bidirectional or infilling objectives, instruction tuning with human feedback to improve alignment, and extending context windows beyond 2048 tokens.\n\n"
            "References\n"
            "[1] Ashish Vaswani et al. Attention Is All You Need. NeurIPS, 2017. arXiv:1706.03762\n"
            "[2] Alec Radford et al. Improving Language Understanding by Generative Pre-Training (GPT-1). 2018.\n"
            "[3] Alec Radford et al. Language Models are Unsupervised Multitask Learners (GPT-2). 2019.\n"
            "[4] Ruibin Xiong et al. On Layer Normalization in the Transformer Architecture. ICML, 2020.\n",
        ),
        (
            "Springer_LNCS_PreLN_vs_PostLN_2020.pdf",
            "single_col",
            "On Layer Normalization in the Transformer Architecture: Post-LN vs. Pre-LN\n"
            "Ruibin Xiong, Yunchang Yang, Di He, Kai Zheng, Tie-Yan Liu\n"
            "Microsoft Research Asia & Peking University (Springer LNCS Style, ICML 2020)\n\n"
            "Abstract\n"
            "The original Transformer [1] and BERT [2] adopt Post-Layer Normalization (Post-LN), where LayerNorm is placed on the residual stream after element-wise addition: y = LN(x + F(x)). "
            "Post-LN suffers from large expected gradients near the output layer at initialization, requiring a critical learning-rate warm-up stage. "
            "By contrast, Pre-Layer Normalization (Pre-LN) places LayerNorm inside the residual branch before the sub-layer: y = x + F(LN(x)), plus a final LayerNorm before prediction. "
            "We prove theoretically and empirically that Pre-LN keeps gradient norms well-behaved across depth and enables stable convergence even without learning-rate warmup.",
            "1 Introduction\n"
            "Transformer [1] and BERT [2] require careful hyperparameter tuning.\n\n"
            "2 Conclusion and Future Work\n"
            "Future work will investigate adaptive normalization and RMSNorm for ultra-deep 100B+ models.\n\n"
            "References\n"
            "[1] Ashish Vaswani et al. Attention Is All You Need. NeurIPS, 2017. arXiv:1706.03762\n"
            "[2] Jacob Devlin et al. BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding. 2019.\n",
        ),
        (
            "Nature_Style_AlphaFold_Evoformer_Attention_2021.pdf",
            "two_col",
            "Highly Accurate Protein Structure Prediction with AlphaFold and Evoformer Attention\n"
            "John Jumper, Richard Evans, Alexander Pritzel, Demis Hassabis\n"
            "DeepMind, London UK (Nature Portfolio Interdisciplinary Style, Nature 2021)\n\n"
            "Abstract\n"
            "We extend the axial and gated self-attention mechanisms pioneered by the Transformer [1] to biological sequence-residue pair representations in the Evoformer architecture. "
            "By exchanging information between Multiple Sequence Alignment (MSA) and pairwise geometric constraints with triangle multiplicative updates, AlphaFold achieves atomic accuracy on CASP14.",
            "1 Main Architecture\n"
            "Inspired by Attention Is All You Need [1] and bidirectional representation learning [2], Evoformer applies row-wise and column-wise gated self-attention.\n\n"
            "2 Discussion and Future Work\n"
            "Future work will extend biomolecular attention to protein-ligand complexes, nucleic acids, and conformational ensembles.\n\n"
            "References\n"
            "[1] Ashish Vaswani et al. Attention Is All You Need. NeurIPS, 2017. arXiv:1706.03762\n"
            "[2] Jacob Devlin et al. BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding. 2019.\n",
        ),
    ]

    paths: list[Path] = []
    for fname, layout_mode, col1_text, col2_text in specs:
        p = out_dir / fname
        doc = pymupdf.open()
        pg = doc.new_page(width=612, height=792)
        if layout_mode == "two_col":
            # 顶部通栏标题与摘要 + 下半页标准双栏排版
            parts = col1_text.split("\n\nAbstract\n", 1)
            header = parts[0]
            abstract_txt = "Abstract\n" + (parts[1] if len(parts) > 1 else "")
            pg.insert_textbox(pymupdf.Rect(50, 48, 562, 145), header, fontsize=11.5)
            pg.insert_textbox(pymupdf.Rect(50, 150, 562, 340), abstract_txt, fontsize=9.5)
            pg.insert_textbox(pymupdf.Rect(50, 355, 296, 740), col2_text[: len(col2_text) // 2], fontsize=9.5)
            pg.insert_textbox(pymupdf.Rect(316, 355, 562, 740), col2_text[len(col2_text) // 2 :], fontsize=9.5)
        else:
            pg.insert_textbox(pymupdf.Rect(65, 55, 547, 735), col1_text + "\n\n" + col2_text, fontsize=10.0)
        doc.save(p)
        doc.close()
        paths.append(p)
    return paths


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    _seed_reviewer()
    register_index_hook(build_chunks_for_paper)

    extra_dir = ROOT / "data" / "official_pdf" / "extra_styles"
    extra_paths = _ensure_multi_paper_and_style_corpus(extra_dir)

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if not user:
            raise RuntimeError("reviewer 初始化失败")
        existing_names = {p.filename for p in db.query(Paper).filter(Paper.owner_id == user.id).all()}

    # 1. 导入官方 v7 与 541页教材（保留 v1 与 v1_img 在 data/official_pdf/ 供现场演示 T1 与 T3 上传）
    all_to_seed = [
        ROOT / "data/official_pdf/1706.03762v7.pdf",
        *extra_paths,
        ROOT / "data/official_pdf/UnderstandingDeepLearning_02_09_26_C.pdf",
    ]
    for pdf_path in all_to_seed:
        if not pdf_path.exists() or pdf_path.name in existing_names:
            continue
        log.info("正在导入并解析: %s", pdf_path.name)
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "reviewer").first()
            pdict, _ = ingest_pdf_bytes(
                db, user.id, pdf_path.name, pdf_path.read_bytes(), source="preset", skip_dedup=True
            )
            pid = pdict["id"]
        parse_paper(pid, enable_vlm_formulas=(pdf_path.name == "1706.03762v7.pdf"))

    # 2. 预构建局域引用图谱、领域综述与 Future Work（模块五开箱即看）
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "reviewer").first()
        if not db.query(CitationGraph).filter(CitationGraph.user_id == user.id).first():
            papers = (
                db.query(Paper)
                .filter(Paper.owner_id == user.id, Paper.status == "ready", Paper.doc_type == "paper")
                .all()
            )
            if len(papers) >= 2:
                log.info("正在预构建引用拓扑图谱、领域综述与 Future Work...")
                g = graph_svc.build_citation_graph(
                    db,
                    user.id,
                    paper_ids=[p.id for p in papers],
                    name="Transformer 演进与跨学科引用图谱",
                )
                graph_svc.generate_survey(db, g)
                graph_svc.explore_future_work(db, g)

        # 3. 预构建 Sickle arXiv 动态追踪与推荐流（模块四开箱即看）
        added = scout_svc.track_arxiv_with_sickle(db, categories=["cs.CL", "cs.LG", "cs.AI"], days=4)
        recs = scout_svc.generate_recommendations(db, user.id, top_n=10)
        log.info("预置完成：Sickle 新增 %d 篇 arXiv 论文，生成 %d 条个性化推荐", added, len(recs))


if __name__ == "__main__":
    seed()
