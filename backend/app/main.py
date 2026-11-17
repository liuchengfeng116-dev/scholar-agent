"""FastAPI 应用入口：挂载六大模块路由、Agent 工具注册、定时 arXiv 追踪与评审账号初始化。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 导入以触发全部专职 Agent 工具注册
from app.agents import (  # noqa: F401
    tools_graph,
    tools_parser,
    tools_rag,
    tools_scout,
    tools_writer,
)
from app.agents.core import REGISTRY
from app.api import auth, copilot, graph, papers, qa, reader, scout, tasks
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.security import hash_password
from app.db.models import User
from app.db.session import Base, SessionLocal, engine
from app.llm import client as llm
from app.parsing.pipeline import register_index_hook
from app.rag.chunker import build_chunks_for_paper, delete_paper_index
from app.scout.service import track_arxiv_with_sickle

setup_logging()
log = logging.getLogger(__name__)


def _seed_reviewer() -> None:
    """首次启动自动创建评审专用测试账号，方便评审专家免注册体验。"""
    with SessionLocal() as db:
        exists = db.query(User).filter(User.username == settings.reviewer_username).first()
        if not exists:
            db.add(
                User(
                    username=settings.reviewer_username,
                    password_hash=hash_password(settings.reviewer_password),
                    role="reviewer",
                    interests={
                        "keywords": ["Transformer", "Large Language Model", "RAG", "AI Agent"],
                        "categories": ["cs.CL", "cs.LG", "cs.AI"],
                    },
                )
            )
            db.commit()
            log.info("已初始化评审测试账号: %s", settings.reviewer_username)


def _delete_index(paper_id: int) -> None:
    with SessionLocal() as db:
        delete_paper_index(db, paper_id)


def _scheduled_arxiv_track() -> None:
    try:
        with SessionLocal() as db:
            added = track_arxiv_with_sickle(db)
            log.info("定时 arXiv Sickle 追踪完成，新增 %d 篇元数据", added)
    except Exception:  # noqa: BLE001
        log.exception("定时 arXiv 追踪任务失败")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed_reviewer()
    register_index_hook(build_chunks_for_paper)
    papers.register_delete_index_hook(_delete_index)

    scheduler = BackgroundScheduler()
    if settings.app_env != "test":
        parts = settings.arxiv_track_cron.split()
        if len(parts) == 5:
            minute, hour, day, month, dow = parts
            scheduler.add_job(
                _scheduled_arxiv_track,
                "cron",
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=dow,
                id="arxiv_sickle_daily",
                replace_existing=True,
            )
            scheduler.start()
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


app = FastAPI(
    title="ScholarAgent · 智研助手 API",
    version="0.2.0",
    description="基于大模型与 Multi-Agent 架构的全流程科研助手后端服务",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(papers.router, prefix="/api")
app.include_router(reader.router, prefix="/api")
app.include_router(qa.router, prefix="/api")
app.include_router(scout.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(copilot.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": bool(settings.dashscope_api_key),
        "tools_registered": sorted(REGISTRY.keys()),
        "models": {
            "chat": settings.llm_model,
            "strong": settings.llm_model_strong,
            "fast": settings.llm_model_fast,
            "vlm": settings.vlm_model,
            "embedding": settings.embedding_model,
        },
        "usage": llm.usage_stats,
    }
