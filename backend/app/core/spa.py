"""单端口静态前端 SPA 托管工具（仅在存在构建产物 dist/index.html 时挂载）。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Windows / 部分精简 Linux 镜像默认将 .mjs 识别为 text/plain，会导致 PDF.js ES Module Worker 被浏览器拦截
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/javascript", ".js")


def mount_frontend_dist(target_app: FastAPI, dist_root: Path) -> None:
    """将编译后的 Vite 前端 dist 目录挂载到 FastAPI 同源根路径（不干扰 /api 路由）。"""
    mimetypes.add_type("application/javascript", ".mjs")
    dist_dir = dist_root.resolve()
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        target_app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @target_app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (dist_dir / full_path).resolve()
            if candidate.is_relative_to(dist_dir) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index_file)
