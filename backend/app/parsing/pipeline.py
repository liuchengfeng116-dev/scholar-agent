"""多模态 PDF 解析主流水线（统一矢量/扫描件/百页长文档，分批落库控制内存）。"""

from __future__ import annotations

import gc
import logging
from collections.abc import Callable
from pathlib import Path

import pymupdf
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Block, Paper, Reference, Section
from app.db.session import SessionLocal
from app.dedup.service import compute_minhash
from app.parsing.detector import inspect_pdf
from app.parsing.layout import ParsedBlock, enrich_formulas_with_vlm, parse_vector_page
from app.parsing.ocr import parse_scanned_pdf
from app.parsing.structure import (
    assign_block_sections,
    build_section_tree,
    extract_metadata,
    parse_references_and_links,
)

log = logging.getLogger(__name__)

# 模块3 RAG 分块索引回调（阶段3注册，解耦解析与检索）
_index_hook: Callable[[Session, int], None] | None = None


def register_index_hook(fn: Callable[[Session, int], None]) -> None:
    global _index_hook
    _index_hook = fn


def _paper_assets_dir(paper_id: int) -> Path:
    d = settings.data_path / "papers" / str(paper_id) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_paper(
    paper_id: int,
    *,
    progress_cb: Callable[[float, str], None] | None = None,
    enable_vlm_formulas: bool = True,
) -> dict:
    """执行完整解析流程并持久化到数据库。"""

    def _report(pct: float, msg: str) -> None:
        with SessionLocal() as s:
            p = s.get(Paper, paper_id)
            if p:
                p.progress = round(pct, 1)
                p.status_msg = msg
                s.commit()
        if progress_cb:
            progress_cb(pct, msg)

    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        if not paper:
            raise ValueError(f"Paper {paper_id} 不存在")
        pdf_path = Path(paper.file_path)
        filename = paper.filename
        paper.status = "parsing"
        paper.progress = 2.0
        paper.status_msg = "正在探测 PDF 结构"
        db.commit()

    profile = inspect_pdf(pdf_path, filename)
    assets_dir = _paper_assets_dir(paper_id)

    _report(
        8.0,
        f"探测完成：{profile.page_count} 页，"
        + ("矢量文本层" if profile.has_text_layer else "无文本层，启用视觉 OCR")
        + ("（超长文档分批模式）" if profile.is_long_doc else ""),
    )

    all_blocks: list[ParsedBlock] = []
    if profile.has_text_layer:
        doc = pymupdf.open(pdf_path)
        try:
            batch_size = 25 if profile.is_long_doc else 15
            for start in range(0, profile.page_count, batch_size):
                end = min(profile.page_count, start + batch_size)
                for pi in range(start, end):
                    blks = parse_vector_page(
                        doc[pi],
                        pi + 1,
                        assets_dir=assets_dir,
                        fast_mode=profile.is_long_doc,
                    )
                    all_blocks.extend(blks)
                # 百页级长文档每批显式回收临时渲染对象，保证内存平稳
                if profile.is_long_doc:
                    gc.collect()
                pct = 10.0 + (end / max(1, profile.page_count)) * 55.0
                _report(pct, f"已解析 {end}/{profile.page_count} 页")

            if enable_vlm_formulas and not profile.is_long_doc:
                _report(68.0, "正在使用视觉模型精准还原公式 LaTeX")
                enrich_formulas_with_vlm(doc, all_blocks, max_formulas=12)
        finally:
            doc.close()
    else:
        def _ocr_prog(done: int, total: int) -> None:
            _report(10.0 + (done / max(1, total)) * 60.0, f"OCR 识别中 {done}/{total} 页")

        all_blocks = parse_scanned_pdf(pdf_path, assets_dir, progress_cb=_ocr_prog)

    _report(74.0, "正在抽取标题树、元数据与参考文献关联")
    meta = extract_metadata(all_blocks, fallback_filename=Path(filename).stem)
    sections = build_section_tree(all_blocks, profile.toc, is_long_doc=profile.is_long_doc)
    block_sec_idxs = assign_block_sections(all_blocks, sections)
    refs = parse_references_and_links(all_blocks, sections, block_sec_idxs)

    # 持久化：清空旧解析记录后批量写入
    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        if not paper:
            return {}
        db.query(Block).filter(Block.paper_id == paper_id).delete()
        db.query(Section).filter(Section.paper_id == paper_id).delete()
        db.query(Reference).filter(Reference.paper_id == paper_id).delete()
        db.commit()

        # 写章节并保存 id 映射
        sec_id_by_order: dict[int, int] = {}
        for sec in sections:
            parent_db_id = sec_id_by_order.get(sec.parent_order) if sec.parent_order is not None else None
            row = Section(
                paper_id=paper_id,
                parent_id=parent_db_id,
                level=sec.level,
                number=sec.number,
                title=sec.title[:500],
                page=sec.page,
                y=sec.y,
                order=sec.order,
                kind=sec.kind,
            )
            db.add(row)
            db.flush()
            sec_id_by_order[sec.order] = row.id

        # 分批写入 Block，防止 541 页书籍一次性构造过大事务
        for i in range(0, len(all_blocks), 400):
            batch = []
            for offset, (b, si) in enumerate(
                zip(all_blocks[i : i + 400], block_sec_idxs[i : i + 400], strict=False)
            ):
                batch.append(
                    Block(
                        paper_id=paper_id,
                        section_id=sec_id_by_order.get(si) if si is not None else None,
                        type=b.type,
                        page=b.page,
                        bbox=b.bbox,
                        order=i + offset,
                        content=b.content,
                        latex=b.latex,
                        html=b.html,
                        image_path=b.image_path,
                        extra=b.extra,
                    )
                )
            db.bulk_save_objects(batch)
            db.flush()

        for r in refs:
            db.add(
                Reference(
                    paper_id=paper_id,
                    idx=r.idx,
                    raw_text=r.raw_text,
                    title=r.title,
                    authors=r.authors,
                    year=r.year,
                    venue=r.venue,
                    doi=r.doi,
                    arxiv_id=r.arxiv_id,
                    page=r.page,
                    bbox=r.bbox,
                    cite_contexts=r.cite_contexts,
                )
            )

        # 更新 Paper 元数据与指纹
        paper.title = meta.title or paper.title or Path(filename).stem
        paper.authors = meta.authors
        paper.affiliations = meta.affiliations
        paper.abstract = meta.abstract
        paper.keywords = meta.keywords
        paper.year = meta.year or paper.year
        paper.venue = meta.venue or paper.venue
        if profile.arxiv_id:
            paper.arxiv_id = profile.arxiv_id
        if profile.version:
            paper.version = profile.version
        if profile.pub_date:
            paper.pub_date = profile.pub_date
        paper.page_count = profile.page_count
        paper.has_text_layer = profile.has_text_layer
        paper.doc_type = profile.doc_type

        sample_text = "\n".join(b.content for b in all_blocks[:60])
        if not paper.fingerprint:
            paper.fingerprint = compute_minhash(sample_text)

        stats = {
            "sections": len(sections),
            "blocks": len(all_blocks),
            "tables": sum(1 for b in all_blocks if b.type == "table"),
            "figures": sum(1 for b in all_blocks if b.type == "figure"),
            "formulas": sum(1 for b in all_blocks if b.type == "formula"),
            "references": len(refs),
            "has_appendix": any(s.kind == "appendix" for s in sections),
        }
        paper.stats = stats
        paper.status = "ready"
        paper.progress = 90.0 if _index_hook else 100.0
        paper.status_msg = "正在构建检索索引" if _index_hook else "解析完成"
        db.commit()

        if _index_hook:
            _index_hook(db, paper_id)
            paper = db.get(Paper, paper_id)
            if paper:
                paper.progress = 100.0
                paper.status_msg = "解析与索引完成"
                db.commit()

    return {"paper_id": paper_id, "stats": stats}
