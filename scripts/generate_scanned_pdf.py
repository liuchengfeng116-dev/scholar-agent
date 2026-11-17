"""从标准矢量 PDF 生成无文本层的图像扫描件 PDF（用于验证赛题 3.2 扫描件 OCR 用例）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf


def pdf_to_scanned_pdf(src: Path, dst: Path, dpi: int = 160) -> None:
    src_doc = pymupdf.open(src)
    out_doc = pymupdf.open()
    for page in src_doc:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        out_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        out_page.insert_image(out_page.rect, stream=pix.tobytes("jpg", jpg_quality=92))
    out_doc.save(dst, deflate=True)
    out_doc.close()
    src_doc.close()
    print(f"已生成纯图像扫描 PDF: {dst} ({dst.stat().st_size} bytes)")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    src_pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data/official_pdf/1706.03762v1.pdf"
    dst_pdf = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "data/official_pdf/1706.03762v1_img.pdf"
    pdf_to_scanned_pdf(src_pdf, dst_pdf)
