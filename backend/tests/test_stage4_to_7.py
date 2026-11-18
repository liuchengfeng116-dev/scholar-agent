"""阶段四至七测试：模块三（阅读翻译）、模块四（检索追踪推荐）、模块五（图谱综述）、模块六（写作可视化Copilot）。"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

OFFICIAL_DIR = Path(__file__).resolve().parents[2] / "data" / "official_pdf"


@pytest.fixture(scope="module")
def full_client(tmp_path_factory):
    tmp_dir: Path = tmp_path_factory.mktemp("stage4_7_data")
    os.environ["DATA_DIR"] = str(tmp_dir)
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp_dir / 'test_full.db').as_posix()}"
    os.environ["JWT_SECRET"] = "test-secret-full"
    os.environ["APP_ENV"] = "test"
    os.environ["REVIEWER_USERNAME"] = "reviewer"
    os.environ["REVIEWER_PASSWORD"] = "Test-Pass-2026"

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "reviewer", "password": "Test-Pass-2026"})
        tok = r.json()["access_token"]
        yield c, {"Authorization": f"Bearer {tok}"}


def _make_mini_pdf(path: Path, body: str) -> None:
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((50, 60), body, fontsize=10.5)
    doc.save(path)
    doc.close()


def test_module3_reader_and_translation(full_client):
    c, headers = full_client
    v7 = OFFICIAL_DIR / "1706.03762v7.pdf"
    if not v7.exists():
        pytest.skip("v7 不存在")

    from app.parsing.pipeline import parse_paper

    with v7.open("rb") as f:
        up = c.post("/api/papers/upload?skip_dedup=true&auto_parse=false", headers=headers, files={"files": ("1706.03762v7.pdf", f, "application/pdf")})
    pid = up.json()["items"][0]["paper"]["id"]
    parse_paper(pid, enable_vlm_formulas=False)

    # 1. 双向引用映射
    cmap = c.get(f"/api/papers/{pid}/citations-map", headers=headers)
    assert cmap.status_code == 200
    assert len(cmap.json()["references"]) >= 25
    assert len(cmap.json()["by_page"]) >= 3

    # 2. 划词翻译
    snip = c.post(
        "/api/translate/snippet",
        headers=headers,
        json={"text": "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms."},
    )
    assert snip.status_code == 200
    assert "Transformer" in snip.json()["translation"]

    # 3. 分页范围中英对照翻译与导出（保留公式）
    from app.api.reader import _translate_block_batch
    from app.db.models import Block, Translation
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        blks = db.query(Block).filter(Block.paper_id == pid, Block.page == 1).limit(6).all()
        tmap = _translate_block_batch(blks)
        for bid, ttxt in tmap.items():
            db.add(Translation(paper_id=pid, block_id=bid, lang="zh", text=ttxt))
        db.commit()

    tr = c.get(f"/api/papers/{pid}/translation?page_from=1&page_to=1", headers=headers)
    assert tr.status_code == 200
    assert tr.json()["translated_blocks"] >= 3

    exp = c.get(f"/api/papers/{pid}/translation/export?fmt=md", headers=headers)
    assert exp.status_code == 200


def test_module4_and_module5_scout_graph_and_future_work(full_client, tmp_path):
    c, headers = full_client
    from app.parsing.pipeline import parse_paper

    # 构造 3 篇存在明确引用拓扑与时间线的测试论文：基石(2017) <- 桥梁(2018) <- 衍生(2020)
    p1 = tmp_path / "base2017.pdf"
    _make_mini_pdf(
        p1,
        "Attention Is All You Need\nAlice (Google, 2017)\n\n"
        "Abstract: We introduce the Transformer architecture based on self-attention.\n\n"
        "1 Introduction\nSequence modeling is important [1].\n\n"
        "5 Conclusion\nIn future work, we plan to extend Transformer to very long sequences beyond 512 tokens.\n\n"
        "References\n[1] Bahdanau et al. Neural Machine Translation. ICLR 2015.\n",
    )
    p2 = tmp_path / "mid2018.pdf"
    _make_mini_pdf(
        p2,
        "BERT Pretraining\nBob (Google, 2018)\n\n"
        "Abstract: We pretrain bidirectional Transformers building upon [1].\n\n"
        "1 Introduction\nBuilding directly on Attention Is All You Need [1], we propose MLM.\n"
        "Note also early background works [2, 3, 4].\n\n"
        "5 Conclusion and Future Work\nScaling laws and sparse attention remain open.\n\n"
        "References\n"
        "[1] Alice. Attention Is All You Need. NeurIPS, 2017. arXiv:1706.03762\n"
        "[2] Smith. Background Survey A. 2014.\n"
        "[3] Lee. Background Survey B. 2015.\n"
        "[4] Wang. Background Survey C. 2016.\n",
    )
    p3 = tmp_path / "top2020.pdf"
    _make_mini_pdf(
        p3,
        "Longformer Sparse Attention\nCarol (AI2, 2020)\n\n"
        "Abstract: We solve the quadratic context length limit of [1] and [2] for long documents up to 4096 tokens.\n\n"
        "1 Introduction\nUnlike [1] and [2], our sliding window attention scales linearly.\n\n"
        "References\n"
        "[1] Alice. Attention Is All You Need. NeurIPS, 2017.\n"
        "[2] Bob. BERT Pretraining. NAACL, 2018.\n",
    )

    pids = []
    for path in (p1, p2, p3):
        with path.open("rb") as f:
            up = c.post("/api/papers/upload?skip_dedup=true&auto_parse=false", headers=headers, files={"files": (path.name, f, "application/pdf")})
        pid = up.json()["items"][0]["paper"]["id"]
        parse_paper(pid, enable_vlm_formulas=False)
        pids.append(pid)

    # 模块四：引用含金量评估（应付式引用 vs 核心引用）
    qual = c.get(f"/api/scout/references/{pids[1]}/quality", headers=headers)
    assert qual.status_code == 200
    qitems = {it["idx"]: it for it in qual.json()["items"]}
    assert qitems[1]["score"] > qitems[2]["score"]

    # 模块四：arXiv 追踪与推荐 + 点赞/踩交互
    trk = c.post("/api/scout/track", headers=headers, json={"categories": ["cs.CL"], "days": 4})
    assert trk.status_code == 200
    recs = trk.json()["recommendations"]
    if recs:
        fb = c.post(f"/api/recommend/{recs[0]['id']}/feedback", headers=headers, json={"feedback": 1})
        assert fb.status_code == 200 and fb.json()["feedback"] == 1

    # 模块五：引用图谱 + 基石/衍生挖掘 + 综述 + Future Work
    g = c.post("/api/graph/build", headers=headers, json={"name": "Transformer演进图谱", "paper_ids": pids})
    assert g.status_code == 200
    gd = g.json()
    assert gd["data"]["metrics"]["edge_count"] >= 2
    assert pids[0] in gd["data"]["metrics"]["keystone_ids"]
    gid = gd["id"]

    surv = c.post(f"/api/graph/{gid}/survey", headers=headers)
    assert surv.status_code == 200
    assert len(surv.json()["survey"]) > 100

    fw = c.post(f"/api/graph/{gid}/future-work", headers=headers)
    assert fw.status_code == 200
    assert "open_directions" in fw.json()["future_work"]


def test_module6_copilot_and_sandbox(full_client):
    c, headers = full_client

    # 1. 安全沙箱拦截危险代码
    from app.copilot.sandbox import validate_code_safety

    with pytest.raises(ValueError):
        validate_code_safety("import os\nos.system('whoami')")

    # 2. 无幻觉论文框架 + 真实文献校验
    out = c.post(
        "/api/copilot/outline",
        headers=headers,
        json={"idea": "Retrieval-augmented multi-agent system for anti-hallucination academic paper QA", "lang": "en"},
    )
    assert out.status_code == 200
    od = out.json()
    assert od["abstract"] and od["related_work"]
    assert all(r["verified"] for r in od["references"])

    # 3. 架构拓扑图脚本（Mermaid + Matplotlib 沙箱渲染 PNG）
    d_mmd = c.post("/api/copilot/diagram", headers=headers, json={"idea": "Planner, Retriever, Critic Multi-Agent Pipeline", "format": "mermaid"})
    assert d_mmd.status_code == 200
    assert "flowchart" in d_mmd.json()["code"].lower() or "graph" in d_mmd.json()["code"].lower()

    d_mpl = c.post("/api/copilot/diagram", headers=headers, json={"idea": "Encoder-Decoder Attention Block", "format": "matplotlib"})
    assert d_mpl.status_code == 200
    assert d_mpl.json()["preview_url"]

    # 4. CSV 数据可视化：柱状图 & 三维雷达图 + Figure Caption
    csv_sample = "Model,BLEU,ROUGE,Speed,Memory,Accuracy\nBase,27.3,41.2,1.0,8.2,84.1\nBig,28.4,43.5,0.6,15.8,86.7\nOurs,29.8,45.1,0.9,9.4,89.2\n"
    for ctype in ("bar", "radar3d"):
        r = c.post(
            "/api/copilot/chart",
            headers=headers,
            data={"chart_type": ctype, "title": f"Model Comparison ({ctype})", "csv_text": csv_sample},
        )
        assert r.status_code == 200
        res = r.json()
        assert res["image_url"].startswith("/api/copilot/assets/")
        img_resp = c.get(res["image_url"], headers=headers)
        assert img_resp.status_code == 200 and len(img_resp.content) > 5000
        assert res["caption_en"] and res["caption_zh"]

    # 5. 学术中译英
    tr = c.post(
        "/api/copilot/translate",
        headers=headers,
        json={"text": "本文提出一种面向科研文献的抗幻觉多智能体检索增强框架，在跨文献推理任务中显著降低事实错误率。"},
    )
    assert tr.status_code == 200
    assert len(tr.json()["english"]) > 30
