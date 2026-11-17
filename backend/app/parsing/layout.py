"""矢量 PDF 版面感知解析：双栏阅读顺序恢复、表格提取、图片裁剪与公式识别。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.core.config import settings
from app.llm import client as llm

log = logging.getLogger(__name__)

_MATH_CHARS = set("∑∏∫√∈∉⊂⊃∪∩∀∃∂∇≈≠≤≥×·±∞αβγδεζηθλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩℝℤℕ")
_EQ_NUM_RE = re.compile(r"\(\d+(?:\.\d+)?\)\s*$")
_FIG_CAPTION_RE = re.compile(r"^(?:Figure|Fig\.?)\s*\d+\s*[:.]", re.I)
_TBL_CAPTION_RE = re.compile(r"^Table\s*\d+\s*[:.]", re.I)


@dataclass
class ParsedBlock:
    type: str  # text | table | figure | formula | caption | heading
    page: int
    bbox: list[float]
    content: str
    latex: str = ""
    html: str = ""
    image_path: str = ""
    font_size: float = 10.0
    is_bold: bool = False
    extra: dict = field(default_factory=dict)


def _rect_overlap(a: list[float], b: list[float], thresh: float = 0.45) -> bool:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return (inter / area_a) >= thresh


def _clean_text(s: str) -> str:
    # 合并连字符断词（如 "representa-\ntion" -> "representation"）并规范化空白
    s = re.sub(r"-\s*\n\s*", "", s)
    s = re.sub(r"\s*\n\s*", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _extract_tables(
    page: pymupdf.Page, page_no: int, *, fast_mode: bool = False
) -> tuple[list[ParsedBlock], list[list[float]]]:
    """抽取表格并转为 Markdown + HTML；fast_mode 下仅对含 Table 字样的页面执行耗时网格线解析。"""
    if fast_mode:
        page_txt = page.get_text("text")
        if "Table" not in page_txt and "表" not in page_txt:
            return [], []
        if len(page.get_drawings()) > 120:
            return [], []
    blocks: list[ParsedBlock] = []
    bboxes: list[list[float]] = []
    try:
        tf = page.find_tables()
    except Exception:  # noqa: BLE001
        return [], []

    for tbl in tf.tables:
        rows = tbl.extract() or []
        if len(rows) < 2:
            continue
        norm_rows = [[(_clean_text(c or "")) for c in row] for row in rows]
        if not any(any(cell for cell in r) for r in norm_rows):
            continue
        ncol = max(len(r) for r in norm_rows)
        norm_rows = [r + [""] * (ncol - len(r)) for r in norm_rows]

        # Markdown
        header = "| " + " | ".join(norm_rows[0]) + " |"
        sep = "| " + " | ".join(["---"] * ncol) + " |"
        body = ["| " + " | ".join(r) + " |" for r in norm_rows[1:]]
        md = "\n".join([header, sep, *body])

        # HTML
        ths = "".join(f"<th>{c}</th>" for c in norm_rows[0])
        trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in norm_rows[1:])
        html = f"<table><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>"

        bbox = [round(x, 1) for x in tbl.bbox]
        bboxes.append(bbox)
        blocks.append(ParsedBlock(type="table", page=page_no, bbox=bbox, content=md, html=html))
    return blocks, bboxes


def _extract_figures(
    page: pymupdf.Page,
    page_no: int,
    assets_dir: Path | None,
    caption_blocks: list[tuple[list[float], str]],
) -> list[ParsedBlock]:
    """抽取内嵌位图或带图注的矢量图区域，保存裁剪图供结构化预览。"""
    if not assets_dir:
        return []
    figs: list[ParsedBlock] = []
    seen_rects: list[list[float]] = []

    try:
        img_infos = page.get_image_info(xrefs=True)
    except Exception:  # noqa: BLE001
        img_infos = []

    idx = 0
    for info in img_infos:
        bbox = [round(float(x), 1) for x in info.get("bbox", [0, 0, 0, 0])]
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w < 45 or h < 45:  # 跳过小图标/装饰线
            continue
        if any(_rect_overlap(bbox, sr, 0.7) for sr in seen_rects):
            continue
        seen_rects.append(bbox)
        idx += 1
        fname = f"fig_p{page_no}_{idx}.png"
        try:
            clip = pymupdf.Rect(bbox) & page.rect
            pix = page.get_pixmap(dpi=150, clip=clip, alpha=False)
            pix.save(str(assets_dir / fname))
            # 查找紧邻下方的 Figure 图注
            cap = ""
            for cb, ctxt in caption_blocks:
                if _FIG_CAPTION_RE.match(ctxt) and cb[1] >= bbox[1] - 10 and cb[1] <= bbox[3] + 90:
                    cap = ctxt
                    break
            figs.append(
                ParsedBlock(
                    type="figure",
                    page=page_no,
                    bbox=bbox,
                    content=cap or f"Figure (Page {page_no})",
                    image_path=fname,
                )
            )
        except Exception:  # noqa: BLE001
            continue

    # 补充：只有矢量图注但无位图（如 Transformer 中的架构图往往是 PDF 矢量绘制！）
    for cb, ctxt in caption_blocks:
        if not _FIG_CAPTION_RE.match(ctxt):
            continue
        if any(f.content == ctxt for f in figs):
            continue
        # 取图注上方区域作为矢量图裁剪区（页面上半部或图注上方 260pt 内）
        fig_y0 = max(48.0, cb[1] - 250.0)
        fig_bbox = [round(min(cb[0], 72.0), 1), round(fig_y0, 1), round(max(cb[2], page.rect.width - 72.0), 1), round(cb[1] - 4.0, 1)]
        if fig_bbox[3] - fig_bbox[1] < 60:
            continue
        idx += 1
        fname = f"fig_p{page_no}_{idx}.png"
        try:
            clip = pymupdf.Rect(fig_bbox) & page.rect
            pix = page.get_pixmap(dpi=150, clip=clip, alpha=False)
            pix.save(str(assets_dir / fname))
            figs.append(
                ParsedBlock(
                    type="figure",
                    page=page_no,
                    bbox=fig_bbox,
                    content=ctxt,
                    image_path=fname,
                )
            )
        except Exception:  # noqa: BLE001
            continue

    return figs


def _is_formula_block(text: str, spans: list[dict], bbox: list[float], page_w: float) -> bool:
    """启发式识别独立展示公式块。"""
    t = text.strip()
    if len(t) < 4 or len(t) > 360:
        return False
    # 以序号结尾且含等号/数学符号，如 "Attention(Q,K,V) = ... (1)"
    if _EQ_NUM_RE.search(t) and ("=" in t or any(c in _MATH_CHARS for c in t)):
        return True
    # 数学符号密度高且较居中/短行
    math_hits = sum(1 for c in t if c in _MATH_CHARS or c in "=∑√∈")
    math_fonts = any("CM" in (sp.get("font") or "") or "Math" in (sp.get("font") or "") for sp in spans)
    cx = (bbox[0] + bbox[2]) / 2
    centered = abs(cx - page_w / 2) < page_w * 0.18
    return (math_hits >= 3 or (math_fonts and "=" in t)) and (centered or len(t) < 120)


def _sort_reading_order(blocks: list[dict], page_w: float) -> list[dict]:
    """恢复单栏/双栏学术论文阅读顺序。"""
    if len(blocks) <= 1:
        return blocks
    mid = page_w * 0.5
    # 判断是否双栏：左右两栏各自都有窄文本块
    left_only = [b for b in blocks if b["bbox"][2] < mid + 18]
    right_only = [b for b in blocks if b["bbox"][0] > mid - 18]
    is_two_col = len(left_only) >= 2 and len(right_only) >= 2
    if not is_two_col:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1)))

    # 混合版面（如第1页：通栏标题+作者 -> 左栏 -> 右栏）按“通栏带”分段排序
    def _is_wide(b: dict) -> bool:
        return b["bbox"][0] < mid - 30 and b["bbox"][2] > mid + 30

    bands: list[list[dict]] = []
    cur_band: list[dict] = []
    for b in sorted(blocks, key=lambda x: x["bbox"][1]):
        if _is_wide(b):
            if cur_band:
                bands.append(cur_band)
                cur_band = []
            bands.append([b])
        else:
            cur_band.append(b)
    if cur_band:
        bands.append(cur_band)

    ordered: list[dict] = []
    for band in bands:
        if len(band) == 1:
            ordered.extend(band)
        else:
            left = sorted([b for b in band if (b["bbox"][0] + b["bbox"][2]) / 2 <= mid], key=lambda x: x["bbox"][1])
            right = sorted([b for b in band if (b["bbox"][0] + b["bbox"][2]) / 2 > mid], key=lambda x: x["bbox"][1])
            ordered.extend(left)
            ordered.extend(right)
    return ordered


def parse_vector_page(
    page: pymupdf.Page,
    page_no: int,
    *,
    assets_dir: Path | None = None,
    fast_mode: bool = False,
) -> list[ParsedBlock]:
    """解析单个矢量 PDF 页面，返回按阅读顺序排列的结构化块。"""
    pw, ph = page.rect.width, page.rect.height
    table_blocks, table_bboxes = _extract_tables(page, page_no, fast_mode=fast_mode)

    raw_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    text_items: list[dict] = []
    caption_candidates: list[tuple[list[float], str]] = []

    for blk in raw_dict.get("blocks", []):
        if blk.get("type") != 0:
            continue
        bbox = [round(float(x), 1) for x in blk["bbox"]]
        # 过滤左侧竖排 arXiv 印记、页眉与页脚页码
        if bbox[2] < 40 and (bbox[3] - bbox[1]) > 80:
            continue
        if bbox[3] < 42 or bbox[1] > ph - 40:
            # 仅过滤纯页码或极短页眉
            lines_txt = "".join(sp["text"] for ln in blk.get("lines", []) for sp in ln.get("spans", [])).strip()
            if len(lines_txt) < 60:
                continue

        if any(_rect_overlap(bbox, tb, 0.5) for tb in table_bboxes):
            continue

        spans: list[dict] = []
        line_texts: list[str] = []
        for ln in blk.get("lines", []):
            lt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            if lt.strip():
                line_texts.append(lt)
            spans.extend(ln.get("spans", []))

        if not line_texts:
            continue

        # 若单个文本块内包含独立小节标题或逐条 [n] 参考文献，按行切分成独立逻辑块
        split_groups: list[list[str]] = []
        cur_grp: list[str] = []
        for lt in line_texts:
            s_lt = lt.strip()
            is_boundary = bool(
                re.match(r"^(?:\[\d+\]\s+|References\b|Bibliography\b|Appendix\b|\d+(?:\.\d+){0,2}\s+[A-Z])", s_lt, re.I)
            )
            if is_boundary and cur_grp:
                split_groups.append(cur_grp)
                cur_grp = [lt]
            else:
                cur_grp.append(lt)
        if cur_grp:
            split_groups.append(cur_grp)

        sizes = [sp.get("size", 10.0) for sp in spans if sp.get("text", "").strip()]
        avg_size = round(sum(sizes) / max(1, len(sizes)), 1)
        bold = any((sp.get("flags", 0) & 16) or "Bold" in (sp.get("font") or "") for sp in spans)

        n_grp = max(1, len(split_groups))
        bh = max(1.0, bbox[3] - bbox[1])
        for gi, grp in enumerate(split_groups):
            raw_txt = "\n".join(grp).strip()
            if not raw_txt:
                continue
            clean = _clean_text(raw_txt)
            if not clean:
                continue
            sub_bbox = (
                bbox
                if n_grp == 1
                else [
                    bbox[0],
                    round(bbox[1] + (gi / n_grp) * bh, 1),
                    bbox[2],
                    round(bbox[1] + ((gi + 1) / n_grp) * bh, 1),
                ]
            )
            if _FIG_CAPTION_RE.match(clean) or _TBL_CAPTION_RE.match(clean):
                caption_candidates.append((sub_bbox, clean))
                btype = "caption"
            elif _is_formula_block(raw_txt, spans, sub_bbox, pw):
                btype = "formula"
            else:
                btype = "text"

            text_items.append(
                {
                    "type": btype,
                    "bbox": sub_bbox,
                    "raw": raw_txt,
                    "clean": clean,
                    "font_size": avg_size,
                    "bold": bold,
                }
            )

    ordered = _sort_reading_order(text_items, pw)
    result: list[ParsedBlock] = []
    for item in ordered:
        btype = item["type"]
        content = item["raw"] if btype == "formula" else item["clean"]
        result.append(
            ParsedBlock(
                type=btype,
                page=page_no,
                bbox=item["bbox"],
                content=content,
                latex=content if btype == "formula" else "",
                font_size=item["font_size"],
                is_bold=item["bold"],
            )
        )

    fig_blocks = _extract_figures(page, page_no, assets_dir, caption_candidates) if not fast_mode else []
    combined = result + table_blocks + fig_blocks
    combined.sort(key=lambda b: (b.bbox[1] if b.bbox else 0.0, b.bbox[0] if b.bbox else 0.0))
    return combined


def enrich_formulas_with_vlm(
    doc: pymupdf.Document,
    blocks: list[ParsedBlock],
    *,
    max_formulas: int = 12,
) -> None:
    """对论文中的关键独立公式块裁剪图像并调用 VLM 还原标准 LaTeX。"""
    targets = [b for b in blocks if b.type == "formula"][:max_formulas]
    for blk in targets:
        page = doc[blk.page - 1]
        pad = 6.0
        clip = pymupdf.Rect(
            max(0.0, blk.bbox[0] - pad),
            max(0.0, blk.bbox[1] - pad),
            min(page.rect.width, blk.bbox[2] + pad),
            min(page.rect.height, blk.bbox[3] + pad),
        )
        if clip.width < 20 or clip.height < 10:
            continue
        try:
            pix = page.get_pixmap(dpi=180, clip=clip, alpha=False)
            latex = llm.vision(
                pix.tobytes("png"),
                "请将图片中的数学公式精准转为标准 LaTeX 代码，只输出公式本身（用 $$...$$ 包裹），不要额外解释。",
                model=settings.ocr_model,
                max_tokens=512,
            )
            if latex and ("=" in latex or "\\" in latex):
                blk.latex = latex.strip()
        except Exception:  # noqa: BLE001
            log.debug("公式 VLM 增强跳过 p.%d", blk.page)
