"""阶段二测试：T1 语义去重与版本比对、T2 矢量精细解析、T3 扫描件 OCR 管线、T4 541页超长文档。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

OFFICIAL_DIR = Path(__file__).resolve().parents[2] / "data" / "official_pdf"


@pytest.fixture(scope="module")
def authed_client(tmp_path_factory):
    tmp_dir: Path = tmp_path_factory.mktemp("stage2_data")
    os.environ["DATA_DIR"] = str(tmp_dir)
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp_dir / 'test2.db').as_posix()}"
    os.environ["JWT_SECRET"] = "test-secret-stage2"
    os.environ["REVIEWER_USERNAME"] = "reviewer"
    os.environ["REVIEWER_PASSWORD"] = "Test-Pass-2026"

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "reviewer", "password": "Test-Pass-2026"})
        tok = r.json()["access_token"]
        yield c, {"Authorization": f"Bearer {tok}"}


def test_t2_vector_parsing_and_t1_semantic_dedup(authed_client):
    """验证 T2（1706.03762v7 细粒度解析）与 T1（再传改名后的 v1 触发语义去重与修订总结）。"""
    c, headers = authed_client
    v7_path = OFFICIAL_DIR / "1706.03762v7.pdf"
    v1_path = OFFICIAL_DIR / "1706.03762v1.pdf"
    if not v7_path.exists() or not v1_path.exists():
        pytest.skip("官方测试 PDF 不存在")

    # 1. 上传 v7 并同步解析验证结构化结果
    from app.db.session import SessionLocal
    from app.parsing.pipeline import parse_paper

    with v7_path.open("rb") as f:
        up7 = c.post("/api/papers/upload", headers=headers, files={"files": ("1706.03762v7.pdf", f, "application/pdf")})
    assert up7.status_code == 200
    p7 = up7.json()["items"][0]["paper"]
    p7_id = p7["id"]

    # 直接同步跑解析便于测试断言（生产走后台任务）
    res = parse_paper(p7_id, enable_vlm_formulas=True)
    assert res["stats"]["sections"] >= 15
    assert res["stats"]["tables"] >= 3
    assert res["stats"]["references"] >= 25
    assert res["stats"]["figures"] >= 1

    st = c.get(f"/api/papers/{p7_id}/structure", headers=headers).json()
    assert "Attention Is All You Need" in st["paper"]["title"]
    assert len(st["paper"]["authors"]) >= 6
    assert any(s["level"] == 3 for s in st["sections"])
    assert any(s["kind"] == "references" for s in st["sections"])
    assert any(s["kind"] == "appendix" for s in st["sections"])
    # 验证正文引用锚点双向映射已建立（如 [11]）
    ref11 = next((r for r in st["references"] if r["idx"] == 11), None)
    assert ref11 is not None and len(ref11["cite_contexts"]) >= 1

    # 2. 用完全不同的文件名上传 v1，验证语义去重识别（不依赖文件名）
    with v1_path.open("rb") as f:
        up1 = c.post("/api/papers/upload", headers=headers, files={"files": ("renamed_old_paper.pdf", f, "application/pdf")})
    assert up1.status_code == 200
    p1 = up1.json()["items"][0]["paper"]
    assert p1["status"] == "duplicate_pending"
    dup = p1["duplicate"]
    assert dup["existing_paper_id"] == p7_id
    assert dup["suggestion"] == "keep_existing"
    assert dup["semantic_similarity"] >= 0.85 or dup["text_similarity"] >= 0.45
    assert len(dup["diff_summary"]) > 20

    # 3. 决策：保留现有 v7 版本
    dec = c.post(f"/api/papers/{p1['id']}/dedup-decision", headers=headers, json={"decision": "keep_existing"})
    assert dec.status_code == 200
    assert dec.json()["action"] == "keep_existing"

    with SessionLocal() as db:
        from app.db.models import Paper

        assert db.get(Paper, p1["id"]) is None
        assert db.get(Paper, p7_id) is not None


def test_t4_long_book_541_pages(authed_client):
    """验证 T4（541 页《Understanding Deep Learning》分批解析与章节树提取）。"""
    c, headers = authed_client
    book_path = OFFICIAL_DIR / "UnderstandingDeepLearning_02_09_26_C.pdf"
    if not book_path.exists():
        pytest.skip("UDL 教材 PDF 不存在")

    from app.parsing.pipeline import parse_paper

    with book_path.open("rb") as f:
        up = c.post(
            "/api/papers/upload?skip_dedup=true",
            headers=headers,
            files={"files": (book_path.name, f, "application/pdf")},
        )
    assert up.status_code == 200
    pid = up.json()["items"][0]["paper"]["id"]
    res = parse_paper(pid, enable_vlm_formulas=False)
    assert res["stats"]["sections"] >= 100
    assert res["stats"]["blocks"] >= 1000

    st = c.get(f"/api/papers/{pid}/structure?page_from=1&page_to=15", headers=headers).json()
    assert st["paper"]["page_count"] == 541
    assert st["paper"]["doc_type"] == "book"
