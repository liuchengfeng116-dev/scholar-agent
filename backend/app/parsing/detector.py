"""PDF 类型与结构特征探测：区分矢量论文、图像扫描 PDF 与百页以上超长书籍。"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.dedup.service import extract_version_info

# PyMuPDF 底层 C 库非线程安全，跨线程并发调用易在 Windows 触发 Access Violation，统一用全局可重入锁串行化 PDF C 操作
MUPDF_LOCK = threading.RLock()

# 100 页及以上判定为超长书籍（兼顾如 GPT-3 等 75 页带长附录的学术论文与 541 页教材）
LONG_DOC_PAGES = 100
_PDF_DATE_YEAR_RE = re.compile(r"D:(\d{4})")


@dataclass
class PdfProfile:
    page_count: int
    has_text_layer: bool
    is_long_doc: bool
    doc_type: str  # "paper" | "book"
    toc: list[list] = field(default_factory=list)  # [[level, title, page], ...]
    arxiv_id: str = ""
    version: str = ""
    pub_date: str = ""
    meta_year: int | None = None
    sample_chars_per_page: float = 0.0


def inspect_pdf(pdf_path: Path, filename: str = "") -> PdfProfile:
    with MUPDF_LOCK:
        doc = pymupdf.open(pdf_path)
        try:
            n = len(doc)
            sample_idxs = [i for i in (0, 1, 2, n // 2, n - 1) if 0 <= i < n]
            sample_idxs = list(dict.fromkeys(sample_idxs))
            texts = [doc[i].get_text("text").strip() for i in sample_idxs]
            avg_chars = sum(len(t) for t in texts) / max(1, len(texts))
            has_text = avg_chars >= 60

            ver_info = extract_version_info(texts[0] if texts else "", filename or pdf_path.name)
            cdate = (doc.metadata or {}).get("creationDate") or ""
            ym = _PDF_DATE_YEAR_RE.search(cdate)
            meta_year = int(ym.group(1)) if ym else None
            is_long = n >= LONG_DOC_PAGES
            return PdfProfile(
                page_count=n,
                has_text_layer=has_text,
                is_long_doc=is_long,
                doc_type="book" if is_long else "paper",
                toc=doc.get_toc() or [],
                arxiv_id=ver_info["arxiv_id"],
                version=ver_info["version"],
                pub_date=ver_info["pub_date"],
                meta_year=meta_year,
                sample_chars_per_page=round(avg_chars, 1),
            )
        finally:
            doc.close()
