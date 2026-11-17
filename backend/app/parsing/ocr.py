"""图像扫描件 PDF 多模态 OCR 解析管线（基于视觉大模型 + 并发页级解析）。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf

from app.core.config import settings
from app.llm import client as llm
from app.parsing.layout import ParsedBlock

log = logging.getLogger(__name__)

_HEADING_MD_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_TABLE_LINE_RE = re.compile(r"^\|.*\|$")
_FIG_TAG_RE = re.compile(r"^(?:\[(?:Figure|Fig)\b[^\]]*\]|(?:Figure|Fig\.?)\s*\d+\s*[:.])", re.I)


_OCR_PROMPT = """你是一位精通学术排版的科研文档 OCR 解析专家。请准确识别整页学术论文图片中的所有内容，并严格按阅读顺序（注意双栏：先左栏从上到下，再右栏从上到下）输出为结构化 Markdown：
1. 标题与章节标题使用 `# `、`## `、`### ` 标明层级；
2. 数学公式还原为标准 LaTeX（行间公式用 `$$...$$` 单独成段，行内公式用 `$...$`）；
3. 表格使用标准 Markdown 表格（`| col | ... |`）；
4. 遇到架构图/图表时，用 `[Figure N: 图注与图内关键文字描述]` 概括，并完整保留原图注；
5. 参考文献保留原始编号 `[1] ...`；
6. 不要添加任何开场白或总结，直接输出识别结果。"""


def _markdown_to_blocks(
    md_text: str, page_no: int, page_w: float, page_h: float, page_img_name: str
) -> list[ParsedBlock]:
    """将 VLM 返回的结构化 Markdown 切分为统一 ParsedBlock，附带比例锚点坐标。"""
    cleaned_md = re.sub(r"^```(?:markdown|md)?\s*", "", md_text.strip(), flags=re.I)
    cleaned_md = re.sub(r"\s*```$", "", cleaned_md)
    cleaned_md = re.sub(r"<!--.*?-->", "", cleaned_md)
    lines = cleaned_md.splitlines()
    raw_items: list[tuple[str, str, int]] = []  # (type, content, level)
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        # 行间公式块 $$...$$
        if ln.startswith("$$"):
            buf = [ln]
            if not (ln.endswith("$$") and len(ln) > 4):
                i += 1
                while i < len(lines):
                    buf.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        break
                    i += 1
            raw_items.append(("formula", "\n".join(buf).strip(), 0))
            i += 1
            continue
        # Markdown 表格
        if _TABLE_LINE_RE.match(ln):
            buf = [ln]
            while i + 1 < len(lines) and _TABLE_LINE_RE.match(lines[i + 1].strip()):
                i += 1
                buf.append(lines[i].strip())
            raw_items.append(("table", "\n".join(buf), 0))
            i += 1
            continue
        # 标题
        hm = _HEADING_MD_RE.match(ln)
        if hm:
            lvl = len(hm.group(1))
            raw_items.append(("heading", hm.group(2).strip(), lvl))
            i += 1
            continue
        # 图表描述/图注
        if _FIG_TAG_RE.match(ln):
            raw_items.append(("figure", ln, 0))
            i += 1
            continue
        # 普通段落（合并连续非特殊行）
        buf = [ln]
        while i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if (
                not nxt
                or nxt.startswith(("$$", "#", "|", "[Figure", "Figure "))
                or re.match(r"^\[\d+\]\s+", nxt)
            ):
                break
            i += 1
            buf.append(nxt)
        raw_items.append(("text", " ".join(buf), 0))
        i += 1

    total = max(1, len(raw_items))
    usable_h = max(100.0, page_h - 100.0)
    out: list[ParsedBlock] = []
    for idx, (btype, content, lvl) in enumerate(raw_items):
        y0 = round(50.0 + (idx / total) * usable_h, 1)
        y1 = round(50.0 + ((idx + 1) / total) * usable_h, 1)
        bbox = [50.0, y0, round(page_w - 50.0, 1), y1]
        img = page_img_name if btype == "figure" else ""
        out.append(
            ParsedBlock(
                type="text" if btype == "heading" else btype,
                page=page_no,
                bbox=bbox,
                content=content,
                latex=content if btype == "formula" else "",
                image_path=img,
                font_size=14.0 if lvl == 1 else (12.0 if lvl == 2 else 10.0),
                is_bold=(btype == "heading"),
                extra={"md_heading_level": lvl} if btype == "heading" else {},
            )
        )
    return out


def parse_scanned_pdf(
    pdf_path: Path,
    assets_dir: Path,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    max_workers: int = 3,
) -> list[ParsedBlock]:
    """并发 OCR 解析图像扫描 PDF。"""
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    page_jobs: list[tuple[int, bytes, float, float, str]] = []
    try:
        for i in range(total_pages):
            page = doc[i]
            pix = page.get_pixmap(dpi=160, alpha=False)
            img_name = f"scan_p{i + 1}.png"
            img_bytes = pix.tobytes("png")
            (assets_dir / img_name).write_bytes(img_bytes)
            page_jobs.append((i + 1, img_bytes, page.rect.width, page.rect.height, img_name))
    finally:
        doc.close()

    results_by_page: dict[int, list[ParsedBlock]] = {}
    done_count = 0

    def _ocr_one(job: tuple[int, bytes, float, float, str]) -> tuple[int, list[ParsedBlock]]:
        pno, img_bytes, pw, ph, img_name = job
        try:
            md = llm.vision(img_bytes, _OCR_PROMPT, model=settings.vlm_model, max_tokens=4096)
        except Exception:  # noqa: BLE001
            log.warning("VLM OCR 第 %d 页失败，降级使用 OCR 模型重试", pno)
            md = llm.vision(img_bytes, _OCR_PROMPT, model=settings.ocr_model, max_tokens=4096)
        return pno, _markdown_to_blocks(md, pno, pw, ph, img_name)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_ocr_one, job) for job in page_jobs]
        for fut in as_completed(futs):
            pno, blks = fut.result()
            results_by_page[pno] = blks
            done_count += 1
            if progress_cb:
                progress_cb(done_count, total_pages)

    ordered_blocks: list[ParsedBlock] = []
    for pno in range(1, total_pages + 1):
        ordered_blocks.extend(results_by_page.get(pno, []))
    return ordered_blocks
