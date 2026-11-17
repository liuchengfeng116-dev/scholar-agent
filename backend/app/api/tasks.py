"""后台任务进度查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Task, User
from app.db.session import get_db

router = APIRouter(prefix="/tasks", tags=["后台任务"])


@router.get("")
def list_tasks(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(Task)
        .filter(Task.user_id == user.id)
        .order_by(Task.id.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": t.id,
            "type": t.type,
            "status": t.status,
            "progress": t.progress,
            "message": t.message,
            "result": t.result,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in rows
    ]


@router.get("/{task_id}")
def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    t = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": t.id,
        "type": t.type,
        "status": t.status,
        "progress": t.progress,
        "message": t.message,
        "result": t.result,
    }
