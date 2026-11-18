"""补充回归测试：Future Work 字段规范化与单端口前端静态 SPA 托管。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.spa import mount_frontend_dist


def test_normalize_future_work_coerces_string_supporting_papers():
    from app.graph.service import normalize_future_work

    raw = {
        "resolved_ideas": [{"idea": "old", "origin_paper": "P1", "resolved_by": "P7"}],
        "open_directions": [
            {
                "direction": "长上下文一致性",
                "rationale": "长文档仍易丢失前文约束",
                "supporting_papers": "P10 | 2020 《GPT-3》",
            },
            {
                "direction": "归一化优化",
                "rationale": "深层网络收敛性有待分析",
                "supporting_papers": ["P11", "P12"],
            },
        ],
    }
    norm = normalize_future_work(raw)
    assert len(norm["resolved_ideas"]) == 1
    assert norm["open_directions"][0]["supporting_papers"] == ["P10 | 2020 《GPT-3》"]
    assert norm["open_directions"][1]["supporting_papers"] == ["P11", "P12"]
    assert normalize_future_work(None) == {"resolved_ideas": [], "open_directions": []}


def test_mount_frontend_dist_single_port_spa_and_api_404(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>ScholarAgent SPA</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (dist / "assets" / "pdf.worker.min.mjs").write_text("export default {}", encoding="utf-8")

    mini_app = FastAPI()

    @mini_app.get("/api/ping")
    def ping():
        return {"ok": True}

    mount_frontend_dist(mini_app, dist_root=dist)
    client = TestClient(mini_app)

    assert client.get("/api/ping").json() == {"ok": True}
    assert client.get("/api/nonexistent").status_code == 404
    assert "ScholarAgent SPA" in client.get("/").text
    assert "ScholarAgent SPA" in client.get("/graph/view").text
    assert client.get("/assets/app.js").text == "console.log('ok')"
    worker_resp = client.get("/assets/pdf.worker.min.mjs")
    assert worker_resp.status_code == 200
    assert "javascript" in worker_resp.headers["content-type"]
