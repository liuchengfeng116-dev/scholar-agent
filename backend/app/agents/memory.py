"""Memory 管理：短期对话记忆（摘要+滑窗）与长期用户画像记忆（主题/偏好/事实）。"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Conversation, Memory, Message, User
from app.llm import client as llm

log = logging.getLogger(__name__)

RECENT_WINDOW = 6
SUMMARY_TRIGGER = 10


def load_short_term(db: Session, conversation_id: int | None) -> tuple[str, list[dict]]:
    """读取短期记忆：返回 (压缩摘要, 最近 N 条消息 [{"role","content"}])。"""
    if not conversation_id:
        return "", []
    conv = db.get(Conversation, conversation_id)
    if not conv:
        return "", []
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(RECENT_WINDOW)
        .all()
    )
    recent = [{"role": r.role, "content": r.content} for r in reversed(rows)]
    return conv.summary or "", recent


def compress_conversation(db: Session, conversation_id: int) -> None:
    """对话较长时，将早期历史压缩为滚动摘要，控制上下文长度。"""
    conv = db.get(Conversation, conversation_id)
    if not conv:
        return
    total = db.query(Message).filter(Message.conversation_id == conversation_id).count()
    if total < SUMMARY_TRIGGER or total % 4 != 0:
        return

    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
        .all()
    )
    older = rows[:-RECENT_WINDOW]
    if not older:
        return
    transcript = "\n".join(f"{m.role}: {m.content[:400]}" for m in older[-12:])
    prompt = (
        f"已有对话摘要：\n{conv.summary or '（无）'}\n\n"
        f"新增历史消息：\n{transcript}\n\n"
        "请用 3-5 句话提炼关键研究主题、已涉及的论文和用户的核心约束条件。"
    )
    try:
        conv.summary = llm.complete(prompt, model=settings.llm_model_fast, temperature=0.1)
        db.commit()
    except Exception:  # noqa: BLE001
        log.warning("对话摘要压缩失败，跳过")


def load_long_term(db: Session, user_id: int, limit: int = 8) -> list[dict]:
    """读取用户长期记忆（按权重排序）与研究兴趣画像。"""
    items: list[dict] = []
    user = db.get(User, user_id)
    if user and user.interests:
        kws = user.interests.get("keywords") or []
        cats = user.interests.get("categories") or []
        if kws or cats:
            items.append(
                {
                    "kind": "profile",
                    "content": f"研究关键词: {', '.join(kws)}; arXiv 方向: {', '.join(cats)}",
                }
            )
    rows = (
        db.query(Memory)
        .filter(Memory.user_id == user_id)
        .order_by(Memory.weight.desc(), Memory.id.desc())
        .limit(limit)
        .all()
    )
    items.extend({"kind": r.kind, "content": r.content, "weight": r.weight} for r in rows)
    return items


def save_memory(
    db: Session, user_id: int, kind: str, content: str, weight: float = 1.0
) -> Memory:
    """写入或强化一条长期记忆（避免完全重复）。"""
    clean = content.strip()
    existing = (
        db.query(Memory)
        .filter(Memory.user_id == user_id, Memory.kind == kind, Memory.content == clean)
        .first()
    )
    if existing:
        existing.weight = min(5.0, existing.weight + 0.5)
        db.commit()
        return existing
    mem = Memory(user_id=user_id, kind=kind, content=clean, weight=weight)
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


def extract_and_update(db: Session, user_id: int, query: str, answer: str) -> None:
    """从对话中沉淀用户长期研究兴趣或偏好（轻量模型异步友好调用）。"""
    if len(query.strip()) < 8:
        return
    prompt = (
        "判断以下科研对话是否包含值得长期记忆的用户研究主题、关注模型/任务或阅读偏好。"
        "如果没有明确长期价值，返回 {\"items\": []}。\n"
        f"用户提问：{query[:500]}\n助手回答摘要：{answer[:300]}\n"
        '返回 JSON：{"items": [{"kind": "topic|preference|fact", "content": "简短中文描述"}]}'
    )
    try:
        data = llm.chat_json(prompt, model=settings.llm_model_fast, temperature=0.1)
        for item in (data.get("items") or [])[:2]:
            kind = item.get("kind", "topic")
            if kind not in ("topic", "preference", "fact"):
                kind = "topic"
            content = (item.get("content") or "").strip()
            if content:
                save_memory(db, user_id, kind, content)
    except Exception:  # noqa: BLE001
        log.debug("长期记忆提炼跳过")
