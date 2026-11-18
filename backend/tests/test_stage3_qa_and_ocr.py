"""阶段三测试：T3 图像扫描件 OCR 解析、M2.1 单篇精准抗幻觉问答、文献引用精确查寻、抗幻觉拒答与 M2.2 多文对比。"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

OFFICIAL_DIR = Path(__file__).resolve().parents[2] / "data" / "official_pdf"


@pytest.fixture(scope="module")
def qa_client(tmp_path_factory):
    tmp_dir: Path = tmp_path_factory.mktemp("stage3_data")
    os.environ["DATA_DIR"] = str(tmp_dir)
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp_dir / 'test3.db').as_posix()}"
    os.environ["JWT_SECRET"] = "test-secret-stage3"
    os.environ["REVIEWER_USERNAME"] = "reviewer"
    os.environ["REVIEWER_PASSWORD"] = "Test-Pass-2026"

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "reviewer", "password": "Test-Pass-2026"})
        tok = r.json()["access_token"]
        yield c, {"Authorization": f"Bearer {tok}"}, tmp_dir


def test_t3_scanned_pdf_ocr_slice(qa_client, tmp_path):
    """验证 T3：对无文本层的图像扫描 PDF 进行 OCR 识别、公式还原与结构化抽取。"""
    c, headers, _ = qa_client
    img_pdf = OFFICIAL_DIR / "1706.03762v1_img.pdf"
    if not img_pdf.exists():
        pytest.skip("扫描件 PDF 不存在")

    # 取前 3 页构建扫描件切片（涵盖标题/作者/摘要/Introduction/Model Architecture/公式）
    src = pymupdf.open(img_pdf)
    sl = pymupdf.open()
    sl.insert_pdf(src, from_page=0, to_page=2)
    slice_path = tmp_path / "scanned_3p.pdf"
    sl.save(slice_path)
    sl.close()
    src.close()

    from app.parsing.pipeline import parse_paper

    with slice_path.open("rb") as f:
        up = c.post(
            "/api/papers/upload?skip_dedup=true&auto_parse=false",
            headers=headers,
            files={"files": ("scanned_3p.pdf", f, "application/pdf")},
        )
    assert up.status_code == 200
    pid = up.json()["items"][0]["paper"]["id"]
    res = parse_paper(pid, enable_vlm_formulas=False)
    assert res["stats"]["blocks"] >= 6

    st = c.get(f"/api/papers/{pid}/structure", headers=headers).json()
    assert not st["paper"]["has_text_layer"]
    assert "Attention Is All You Need" in st["paper"]["title"]
    all_text = " ".join(b["content"] + " " + b["latex"] for b in st["blocks"])
    assert "Transformer" in all_text or "self-attention" in all_text.lower()


def test_m2_single_paper_qa_and_multi_paper_compare(qa_client, tmp_path):
    """验证 M2.1 单篇细粒度抗幻觉问答、参考文献查寻、拒答机制与 M2.2 多文对比。"""
    c, headers, _ = qa_client
    v7_path = OFFICIAL_DIR / "1706.03762v7.pdf"
    if not v7_path.exists():
        pytest.skip("v7 PDF 不存在")

    from app.parsing.pipeline import parse_paper

    with v7_path.open("rb") as f:
        up = c.post(
            "/api/papers/upload?skip_dedup=true&auto_parse=false",
            headers=headers,
            files={"files": ("1706.03762v7.pdf", f, "application/pdf")},
        )
    p7_id = up.json()["items"][0]["paper"]["id"]
    parse_paper(p7_id, enable_vlm_formulas=False)

    # 1. M2.1 单篇精准问答：超参数、GPU、BLEU 分数与溯源锚点
    r1 = c.post(
        "/api/chat",
        headers=headers,
        json={
            "paper_ids": [p7_id],
            "stream": False,
            "query": "论文中 Adam 优化器的 beta1 和 beta2 是多少？训练用了什么型号和数量的 GPU？WMT 2014 English-to-French 的单模型 BLEU 分数是多少？",
        },
    )
    assert r1.status_code == 200
    d1 = r1.json()
    ans1 = d1["answer"]
    assert "0.9" in ans1 and "0.98" in ans1
    assert "P100" in ans1 and "8" in ans1
    assert "41.8" in ans1
    assert len(d1["citations"]) >= 1
    assert d1["citations"][0]["page"] >= 1
    assert d1["critic"]["grounded_ratio"] >= 0.6

    # 2. 参考文献 [11] 完整引用信息
    r2 = c.post(
        "/api/chat",
        headers=headers,
        json={
            "paper_ids": [p7_id],
            "stream": False,
            "query": "请给出该论文参考文献 [11] 的完整引用信息。",
        },
    )
    assert r2.status_code == 200
    assert "Kaiming He" in r2.json()["answer"] or "Deep residual learning" in r2.json()["answer"]

    # 3. 抗幻觉边界测试：询问文中根本不存在的概念
    r3 = c.post(
        "/api/chat",
        headers=headers,
        json={
            "paper_ids": [p7_id],
            "stream": False,
            "query": "请问这篇论文第8节中实验使用的 Quantum Flux Accelerator Q9000 芯片主频是多少 GHz？",
        },
    )
    assert r3.status_code == 200
    ans3 = r3.json()["answer"]
    assert any(k in ans3 for k in ("未", "没有", "不包含", "无法", "未提及", "不存在"))

    # 4. M2.2 多文对比：生成一份简短 BERT 对比测试 PDF 并跨文献联合推理
    bert_pdf = tmp_path / "bert_summary.pdf"
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text(
        (60, 70),
        "BERT: Pre-training of Deep Bidirectional Transformers\n"
        "Jacob Devlin, Ming-Wei Chang, Kenton Lee, Kristina Toutanova (Google AI Language, 2018)\n\n"
        "Abstract: BERT uses a multi-layer bidirectional Transformer encoder based on the original implementation.\n"
        "Unlike the original Transformer which includes both an encoder and a decoder for sequence-to-sequence tasks,\n"
        "BERT keeps only the Transformer Encoder stack and discards the Transformer Decoder and cross-attention.\n"
        "Furthermore, BERT replaces autoregressive left-to-right language modeling with Masked Language Modeling (MLM).\n",
        fontsize=11,
    )
    doc.save(bert_pdf)
    doc.close()

    with bert_pdf.open("rb") as f:
        up_b = c.post(
            "/api/papers/upload?skip_dedup=true&auto_parse=false",
            headers=headers,
            files={"files": ("bert_summary.pdf", f, "application/pdf")},
        )
    pb_id = up_b.json()["items"][0]["paper"]["id"]
    parse_paper(pb_id, enable_vlm_formulas=False)

    r4 = c.post(
        "/api/chat",
        headers=headers,
        json={
            "paper_ids": [p7_id, pb_id],
            "stream": False,
            "query": "请对比 BERT 与原始 Transformer 在架构上的承袭与抛弃关系。",
        },
    )
    assert r4.status_code == 200
    ans4 = r4.json()["answer"]
    assert "Encoder" in ans4 and "Decoder" in ans4
    cited_pids = {cit["paper_id"] for cit in r4.json()["citations"]}
    assert pb_id in cited_pids
