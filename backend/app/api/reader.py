"""模块三 API：双向引用跳转映射、局部划词翻译、整篇学术翻译（保留代码与公式）及对比导出。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import Block, Paper, Reference, Section, Translation, User
from app.db.session import SessionLocal, get_db
from app.llm import client as llm
from app.tasks import runner

log = logging.getLogger(__name__)
router = APIRouter(tags=["阅读与双向联动翻译"])

_TERM_CACHE_HINT = "专有名词/模型名（如 Transformer、Attention、BLEU、LayerNorm、BERT、GPT）首次出现可保留英文或括注，LaTeX 公式（$...$ / $$...$$）与代码片段必须原样保留英文。"


class SnippetTranslateReq(BaseModel):
    text: str
    context: str = ""
    target_lang: str = "zh"


class FullTranslateReq(BaseModel):
    target_lang: str = "zh"
    page_from: int = 1
    page_to: int | None = None


@router.get("/papers/{paper_id}/citations-map")
def get_citations_map(
    paper_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """返回正文引用标注 [n] 与 References 条目之间的双向锚点映射。"""
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")

    refs = db.query(Reference).filter(Reference.paper_id == paper_id).order_by(Reference.idx.asc()).all()
    forward: dict[str, list[dict]] = {}  # page -> [{idx, bbox, snippet}]
    items = []
    for r in refs:
        ctxs = r.cite_contexts or []
        items.append(
            {
                "id": r.id,
                "idx": r.idx,
                "raw_text": r.raw_text,
                "title": r.title,
                "year": r.year,
                "page": r.page,
                "bbox": r.bbox or [],
                "cite_contexts": ctxs,
            }
        )
        for c in ctxs:
            pg = str(c.get("page", 1))
            forward.setdefault(pg, []).append(
                {
                    "ref_idx": r.idx,
                    "ref_page": r.page,
                    "ref_bbox": r.bbox or [],
                    "bbox": c.get("bbox") or [],
                    "snippet": c.get("snippet", ""),
                }
            )
    return {"paper_id": paper_id, "references": items, "by_page": forward}


@router.post("/translate/snippet")
def translate_snippet(
    req: SnippetTranslateReq,
    user: User = Depends(get_current_user),
) -> dict:
    """局部划词/悬停翻译：精准翻译选中英文片段，保留公式与代码。"""
    txt = req.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="待翻译文本不能为空")
    prompt = (
        f"请将以下学术论文片段精准翻译为中文。{_TERM_CACHE_HINT}\n"
        + (f"上下文参考：{req.context[:300]}\n" if req.context else "")
        + f"待译片段：\n{txt[:2500]}\n\n"
        "返回 JSON：{\"translation\": \"中文译文\", \"terms\": [{\"en\": \"原词\", \"zh\": \"译法\"}]}"
    )
    try:
        res = llm.chat_json(prompt, model=settings.llm_model_fast, temperature=0.1)
        return {
            "source": txt,
            "translation": res.get("translation", ""),
            "terms": res.get("terms") or [],
        }
    except Exception:  # noqa: BLE001
        trans = llm.complete(
            f"将下面学术英文翻译成中文，代码与公式保留原文：\n{txt[:2500]}",
            model=settings.llm_model_fast,
            temperature=0.1,
        )
        return {"source": txt, "translation": trans, "terms": []}


def _translate_block_batch(blocks: list[Block]) -> dict[int, str]:
    """批量翻译文本块，保持数学公式与代码原样保留。"""
    results: dict[int, str] = {}
    to_translate: list[Block] = []
    for b in blocks:
        if b.type == "formula":
            # 公式保留原始 LaTeX
            results[b.id] = b.latex or b.content
        elif b.type == "table":
            results[b.id] = b.content
        elif b.content.strip():
            to_translate.append(b)

    # 每 6 个段落合并为一次批量调用，兼顾速度与上下文连贯
    for i in range(0, len(to_translate), 6):
        batch = to_translate[i : i + 6]
        numbered = "\n\n".join(f"[[B{b.id}]]\n{b.content[:1500]}" for b in batch)
        prompt = (
            f"请将以下带 [[B数字]] 编号的学术论文段落逐段翻译为流畅准确的中文。{_TERM_CACHE_HINT}\n"
            "严格保留每个 [[B数字]] 标记行，不要合并或遗漏编号：\n\n"
            f"{numbered}"
        )
        try:
            out = llm.complete(prompt, model=settings.llm_model_fast, temperature=0.1)
            parts = re.split(r"\[\[B(\d+)\]\]\s*", out)
            if len(parts) >= 3:
                for k in range(1, len(parts) - 1, 2):
                    bid = int(parts[k])
                    results[bid] = parts[k + 1].strip()
                continue
        except Exception:  # noqa: BLE001
            log.warning("批量翻译片段失败，降级逐块翻译")
        for b in batch:
            if b.id not in results:
                try:
                    results[b.id] = llm.complete(
                        f"译为学术中文，保留公式与代码：\n{b.content[:1500]}",
                        model=settings.llm_model_fast,
                        temperature=0.1,
                    )
                except Exception:  # noqa: BLE001
                    results[b.id] = b.content
    return results


@router.post("/papers/{paper_id}/translate")
def start_full_translation(
    paper_id: int,
    req: FullTranslateReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """触发整篇（或指定页码范围）学术翻译后台任务，用于中英对照联动阅读。"""
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")

    def _job(task_id: int) -> dict:
        with SessionLocal() as s:
            bq = s.query(Block).filter(Block.paper_id == paper_id, Block.page >= req.page_from)
            if req.page_to:
                bq = bq.filter(Block.page <= req.page_to)
            elif p.page_count > 60:
                # 超长书默认先译前 25 页，避免一次性消耗大量时间
                bq = bq.filter(Block.page <= req.page_from + 24)
            blocks = bq.order_by(Block.page.asc(), Block.order.asc()).all()
            existing_ids = {
                t.block_id
                for t in s.query(Translation).filter(
                    Translation.paper_id == paper_id, Translation.lang == req.target_lang
                )
            }
            pending = [b for b in blocks if b.id not in existing_ids]
            total = len(pending)
            if total == 0:
                return {"translated": 0, "total": len(blocks)}

            done = 0
            for i in range(0, total, 12):
                batch = pending[i : i + 12]
                trans_map = _translate_block_batch(batch)
                for b in batch:
                    txt = trans_map.get(b.id, b.content)
                    s.add(
                        Translation(
                            paper_id=paper_id,
                            block_id=b.id,
                            lang=req.target_lang,
                            text=txt,
                        )
                    )
                s.commit()
                done += len(batch)
                runner.update_task(
                    task_id,
                    progress=round(10.0 + (done / total) * 90.0, 1),
                    message=f"已翻译 {done}/{total} 个内容块",
                )
            return {"translated": done, "total": len(blocks)}

    tid = runner.submit("translate_paper", user.id, _job, payload={"paper_id": paper_id})
    return {"paper_id": paper_id, "task_id": tid}


@router.get("/papers/{paper_id}/translation")
def get_translation(
    paper_id: int,
    page_from: int = Query(default=1, ge=1),
    page_to: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """返回原文块与中文译文块的逐块对齐数据（用于中英对比阅读与同步滚动）。"""
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user.id).first()
    if not p:
        raise HTTPException(status_code=404, detail="文献不存在")

    bq = db.query(Block).filter(Block.paper_id == paper_id, Block.page >= page_from)
    if page_to:
        bq = bq.filter(Block.page <= page_to)
    elif p.page_count > 60:
        bq = bq.filter(Block.page <= page_from + 24)
    blocks = bq.order_by(Block.page.asc(), Block.order.asc()).all()

    trans_rows = db.query(Translation).filter(Translation.paper_id == paper_id).all()
    trans_map = {t.block_id: t.text for t in trans_rows}

    secs = db.query(Section).filter(Section.paper_id == paper_id).all()
    sec_map = {s.id: s.title for s in secs}

    items = [
        {
            "block_id": b.id,
            "type": b.type,
            "page": b.page,
            "bbox": b.bbox,
            "section": sec_map.get(b.section_id, "") if b.section_id else "",
            "source": b.latex or b.content,
            "translation": trans_map.get(b.id, ""),
        }
        for b in blocks
    ]
    translated_count = sum(1 for it in items if it["translation"])
    return {
        "paper_id": paper_id,
        "total_blocks": len(items),
        "translated_blocks": translated_count,
        "items": items,
    }


@router.get("/papers/{paper_id}/translation/export")
def export_translation(
    paper_id: int,
    fmt: str = Query(default="md", pattern="^(md|html)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导出整篇双语/中文译文为 Markdown 或 HTML（代码与公式保留英文）。"""
    data = get_translation(paper_id=paper_id, page_from=1, page_to=None, user=user, db=db)
    p = db.get(Paper, paper_id)
    title = p.title if p else f"Paper {paper_id}"

    if fmt == "md":
        lines = [f"# {title}\n"]
        cur_sec = ""
        for it in data["items"]:
            if it["section"] and it["section"] != cur_sec:
                cur_sec = it["section"]
                lines.append(f"\n## {cur_sec}\n")
            lines.append(it["translation"] or it["source"])
            lines.append("")
        return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")

    rows_html = "\n".join(
        f"<div class='pair'><div class='src'>{it['source']}</div>"
        f"<div class='tgt'>{it['translation'] or '<em>未翻译</em>'}</div></div>"
        for it in data["items"]
    )
    html = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
        "<style>body{font-family:sans-serif;max-width:1200px;margin:auto;padding:24px}"
        ".pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:10px 0;border-bottom:1px solid #eee}"
        "</style></head><body>"
        f"<h1>{title}</h1>{rows_html}</body></html>"
    )
    return PlainTextResponse(html, media_type="text/html; charset=utf-8")
