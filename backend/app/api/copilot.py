"""模块六 API：无幻觉论文框架、架构图脚本、CSV 数据可视化与学术中译英。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.copilot import service as copilot_svc
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/copilot", tags=["学术写作与可视化Copilot"])


class OutlineReq(BaseModel):
    idea: str = Field(min_length=4)
    paper_ids: list[int] = Field(default_factory=list)
    lang: str = "en"


class DiagramReq(BaseModel):
    idea: str = Field(min_length=4)
    format: str = "mermaid"  # mermaid | tikz | graphviz | matplotlib


class AcademicTranslateReq(BaseModel):
    text: str = Field(min_length=2)
    style: str = "IEEE / NeurIPS"


@router.post("/outline")
def create_outline(
    req: OutlineReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return copilot_svc.generate_outline(
        db, user.id, idea=req.idea, paper_ids=req.paper_ids or None, lang=req.lang
    )


@router.post("/diagram")
def create_diagram(
    req: DiagramReq,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        return copilot_svc.generate_diagram(req.idea, fmt=req.format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/chart")
async def create_csv_chart(
    chart_type: str = Form(default="bar"),
    title: str = Form(default="Experimental Results"),
    instruction: str = Form(default=""),
    csv_text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
) -> dict:
    if file is not None:
        raw = await file.read()
    elif csv_text.strip():
        raw = csv_text.encode("utf-8")
    else:
        raise HTTPException(status_code=400, detail="请上传 CSV 文件或提供 CSV 文本")
    try:
        return copilot_svc.visualize_csv(
            raw, chart_type=chart_type, title=title, instruction=instruction
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"CSV 可视化失败: {e}") from e


@router.post("/translate")
def translate_to_academic_en(
    req: AcademicTranslateReq,
    user: User = Depends(get_current_user),
) -> dict:
    try:
        return copilot_svc.academic_translate(req.text, style=req.style)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/assets/{filename}")
def get_copilot_asset(
    filename: str,
    user: User = Depends(get_current_user),
):
    safe = Path(filename).name
    p = settings.data_path / "copilot" / safe
    if not p.exists():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(p)
