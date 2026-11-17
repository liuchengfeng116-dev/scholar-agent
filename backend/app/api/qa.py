"""Agent 对话与抗幻觉问答路由（支持 SSE 流式轨迹推送）。"""

from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import orchestrator
from app.agents.core import AgentContext
from app.api.deps import get_current_user
from app.db.models import Conversation, Message, User
from app.db.session import SessionLocal, get_db

router = APIRouter(tags=["Agent问答"])


class ConvCreateReq(BaseModel):
    title: str = "新研究对话"
    paper_ids: list[int] = Field(default_factory=list)


class ChatReq(BaseModel):
    query: str
    conversation_id: int | None = None
    paper_ids: list[int] = Field(default_factory=list)
    stream: bool = True


@router.get("/conversations")
def list_conversations(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    rows = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.id.desc())
        .limit(40)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title,
            "paper_ids": c.paper_ids or [],
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in rows
    ]


@router.post("/conversations")
def create_conversation(
    req: ConvCreateReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    c = Conversation(user_id=user.id, title=req.title, paper_ids=req.paper_ids)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "title": c.title, "paper_ids": c.paper_ids}


@router.get("/conversations/{conv_id}/messages")
def get_messages(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    c = db.query(Conversation).filter(Conversation.id == conv_id, Conversation.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="对话不存在")
    rows = db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.id.asc()).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "citations": m.citations or [],
            "trace": m.trace or [],
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    c = db.query(Conversation).filter(Conversation.id == conv_id, Conversation.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="对话不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/chat")
def chat(
    req: ChatReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """运行 Multi-Agent 编排器，支持 SSE 流式返回规划、工具调用、Critic 校验及最终答案。"""
    conv_id = req.conversation_id
    if conv_id:
        c = db.query(Conversation).filter(Conversation.id == conv_id, Conversation.user_id == user.id).first()
        if not c:
            raise HTTPException(status_code=404, detail="对话不存在")
        if req.paper_ids:
            c.paper_ids = req.paper_ids
            db.commit()
    else:
        c = Conversation(
            user_id=user.id,
            title=req.query.strip()[:36] or "新研究对话",
            paper_ids=req.paper_ids,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        conv_id = c.id

    if not req.stream:
        ctx = AgentContext(db=db, user_id=user.id, conversation_id=conv_id, paper_ids=req.paper_ids)
        result = orchestrator.run(ctx, req.query)
        return {"conversation_id": conv_id, **result}

    ev_q: queue.Queue[dict | None] = queue.Queue()

    def _worker() -> None:
        with SessionLocal() as wdb:
            ctx = AgentContext(
                db=wdb,
                user_id=user.id,
                conversation_id=conv_id,
                paper_ids=req.paper_ids,
                emit=lambda ev: ev_q.put(ev),
            )
            try:
                res = orchestrator.run(ctx, req.query)
                ev_q.put({"type": "done", "conversation_id": conv_id, **res})
            except Exception as e:  # noqa: BLE001
                ev_q.put({"type": "error", "detail": str(e)})
            finally:
                ev_q.put(None)

    threading.Thread(target=_worker, daemon=True).start()

    def _sse():
        while True:
            item = ev_q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")
