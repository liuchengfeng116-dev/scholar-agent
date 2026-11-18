"""模块五 API：引用拓扑图谱构建、查询、领域综述生成与 Future Work 前沿探索。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import CitationGraph, User
from app.db.session import get_db
from app.graph import service as graph_svc

router = APIRouter(prefix="/graph", tags=["引用图谱与综述"])


class BuildGraphReq(BaseModel):
    name: str = "局域文献引用图谱"
    paper_ids: list[int] = Field(default_factory=list)


def _graph_dict(g: CitationGraph) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "paper_ids": g.paper_ids or [],
        "data": g.data or {},
        "survey": g.survey or "",
        "future_work": graph_svc.normalize_future_work(g.future_work),
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


@router.get("")
def list_graphs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(CitationGraph)
        .filter(CitationGraph.user_id == user.id)
        .order_by(CitationGraph.id.desc())
        .limit(20)
        .all()
    )
    return [_graph_dict(g) for g in rows]


@router.post("/build")
def build_graph(
    req: BuildGraphReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        g = graph_svc.build_citation_graph(
            db, user.id, paper_ids=req.paper_ids or None, name=req.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _graph_dict(g)


@router.get("/{graph_id}")
def get_graph(
    graph_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    g = (
        db.query(CitationGraph)
        .filter(CitationGraph.id == graph_id, CitationGraph.user_id == user.id)
        .first()
    )
    if not g:
        raise HTTPException(status_code=404, detail="图谱不存在")
    return _graph_dict(g)


@router.post("/{graph_id}/survey")
def create_survey(
    graph_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    g = (
        db.query(CitationGraph)
        .filter(CitationGraph.id == graph_id, CitationGraph.user_id == user.id)
        .first()
    )
    if not g:
        raise HTTPException(status_code=404, detail="图谱不存在")
    survey = graph_svc.generate_survey(db, g)
    return {"graph_id": g.id, "survey": survey}


@router.post("/{graph_id}/future-work")
def create_future_work(
    graph_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    g = (
        db.query(CitationGraph)
        .filter(CitationGraph.id == graph_id, CitationGraph.user_id == user.id)
        .first()
    )
    if not g:
        raise HTTPException(status_code=404, detail="图谱不存在")
    fw = graph_svc.explore_future_work(db, g)
    return {"graph_id": g.id, "future_work": fw}
