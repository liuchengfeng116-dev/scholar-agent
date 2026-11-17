"""认证与用户偏好接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import Memory, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["认证与用户"])


class AuthReq(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)


class InterestsReq(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "interests": u.interests or {"keywords": [], "categories": []},
    }


@router.post("/register")
def register(req: AuthReq, db: Session = Depends(get_db)) -> dict:
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    u = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role="user",
        interests={"keywords": ["Large Language Model", "AI Agent"], "categories": ["cs.CL", "cs.LG"]},
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"access_token": create_access_token(u.id), "token_type": "bearer", "user": _user_dict(u)}


@router.post("/login")
def login(req: AuthReq, db: Session = Depends(get_db)) -> dict:
    u = db.query(User).filter(User.username == req.username).first()
    if not u or not verify_password(req.password, u.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return {"access_token": create_access_token(u.id), "token_type": "bearer", "user": _user_dict(u)}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return _user_dict(user)


@router.put("/interests")
def update_interests(
    req: InterestsReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    user.interests = {"keywords": req.keywords, "categories": req.categories}
    db.commit()
    return _user_dict(user)


@router.get("/memories")
def list_memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(Memory)
        .filter(Memory.user_id == user.id)
        .order_by(Memory.weight.desc(), Memory.id.desc())
        .all()
    )
    return [{"id": r.id, "kind": r.kind, "content": r.content, "weight": r.weight} for r in rows]


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.query(Memory).filter(Memory.id == memory_id, Memory.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="记忆不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
