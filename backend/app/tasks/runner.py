"""持久化后台任务执行器（用于长文档解析、批量翻译、批量检索等耗时任务）。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.db.models import Task
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scholar-task")


def create_task(task_type: str, user_id: int | None, payload: dict | None = None) -> int:
    with SessionLocal() as db:
        t = Task(type=task_type, user_id=user_id, payload=payload or {}, status="pending")
        db.add(t)
        db.commit()
        db.refresh(t)
        return t.id


def update_task(
    task_id: int,
    *,
    status: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    result: dict | None = None,
) -> None:
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            return
        if status is not None:
            t.status = status
        if progress is not None:
            t.progress = round(min(100.0, max(0.0, progress)), 1)
        if message is not None:
            t.message = message[:500]
        if result is not None:
            t.result = result
        db.commit()


def submit(task_type: str, user_id: int | None, fn: Callable[[int], dict], payload: dict | None = None) -> int:
    """创建 Task 记录并在后台线程池执行 fn(task_id)。"""
    task_id = create_task(task_type, user_id, payload)

    def _runner() -> None:
        update_task(task_id, status="running", progress=1.0, message="任务已启动")
        try:
            res = fn(task_id)
            update_task(task_id, status="done", progress=100.0, message="完成", result=res or {})
        except Exception as e:  # noqa: BLE001
            log.exception("后台任务 %s(%d) 失败", task_type, task_id)
            update_task(task_id, status="failed", message=f"失败: {e}")

    _pool.submit(_runner)
    return task_id
