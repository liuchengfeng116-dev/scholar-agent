"""FastAPI 应用入口：路由挂载、CORS、启动自检与评审测试账号初始化。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, qa, tasks
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.security import hash_password
from app.db.models import User
from app.db.session import Base, SessionLocal, engine
from app.llm import client as llm

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed_reviewer()
    yield


app = FastAPI(
    title="ScholarAgent · 智研助手 API",
    version="0.1.0",
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
app.include_router(tasks.router, prefix="/api")
app.include_router(qa.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": bool(settings.dashscope_api_key),
        "models": {
            "chat": settings.llm_model,
            "strong": settings.llm_model_strong,
            "vlm": settings.vlm_model,
            "embedding": settings.embedding_model,
        },
        "usage": llm.usage_stats,
    }
