"""学术论文结构化抽取：标题/作者机构/摘要、1-3级标题树、参考文献拆分与正文双向引用关联。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.llm import client as llm
from app.parsing.layout import ParsedBlock

log = logging.getLogger(__name__)

_NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,2})\s+([A-Z\u4e00-\u9fff][^\n]{1,90})$")
_SPECIAL_HEADING_RE = re.compile(
    r"^(Abstract|Introduction|Background|Related Work|Conclusion|Conclusions|"
    r"Acknowledgements?|References|Bibliography|Appendix(?:\s+[A-Z0-9])?.*|"
    r"Attention Visualizations)\b",
    re.I,
)
_REF_ENTRY_RE = re.compile(r"^\[(\d+)\]\s*(.+)$")
_CITE_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[,–\-]\s*\d+)*)\]")
_YEAR_RE = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;]+)")
_ARXIV_RE = re.compile(r"arXiv:(\d{4}\.\d{4,5})", re.I)


@dataclass
class ExtractedMetadata:
    title: str = ""
    authors: list[dict] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""


@dataclass
class SectionNode:
    order: int
    level: int
    number: str
    title: str
    page: int
    y: float
    kind: str = "body"  # body | references | appendix
    parent_order: int | None = None


@dataclass
class ParsedReference:
    idx: int
    raw_text: str
    title: str = ""
    authors: str = ""
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    page: int = 0
    bbox: list[float] = field(default_factory=list)
    cite_contexts: list[dict] = field(default_factory=list)


def extract_metadata(blocks: list[ParsedBlock], fallback_filename: str = "") -> ExtractedMetadata:
    """从前 2 页块抽取标题、作者与机构、摘要、年份、会议等。"""
    front = [b for b in blocks if b.page <= 2 and b.type in ("text", "caption")]
    front_text = "\n".join(b.content for b in front[:24])[:3500]
    if not front_text.strip():
        return ExtractedMetadata(title=fallback_filename)

    prompt = (
        "请从以下学术论文/书籍首页文本中精准提取结构化元数据。只依据原文内容，未出现字段留空。\n"
        f"首页文本：\n{front_text}\n\n"
        "返回合法 JSON：\n"
        "{\n"
        '  "title": "论文或书籍完整标题",\n'
        '  "authors": [{"name": "作者姓名", "affiliations": ["所属机构"], "email": "邮箱或空"}],\n'
        '  "affiliations": ["去重后的所有机构名称"],\n'
        '  "abstract": "完整摘要正文（若无则空字符串）",\n'
        '  "keywords": ["关键词"],\n'
        '  "year": 2017,\n'
        '  "venue": "发表会议/期刊（如 NeurIPS 2017 / arXiv，没有则空）"\n'
        "}"
    )
    try:
        d = llm.chat_json(prompt, model=settings.llm_model_fast, temperature=0.0)
        yr = d.get("year")
        return ExtractedMetadata(
            title=(d.get("title") or fallback_filename).strip(),
            authors=d.get("authors") or [],
            affiliations=d.get("affiliations") or [],
            abstract=(d.get("abstract") or "").strip(),
            keywords=d.get("keywords") or [],
            year=int(yr) if isinstance(yr, int) or (isinstance(yr, str) and yr.isdigit()) else None,
            venue=(d.get("venue") or "").strip(),
        )
    except Exception:  # noqa: BLE001
        log.warning("首页元数据 LLM 抽取失败，使用启发式回退")
        p1_candidates = [
            b for b in front
            if b.page == 1
            and len(b.content) < 220
            and not b.content.lower().startswith(("provided proper attribution", "arxiv:"))
        ]
        best = max(p1_candidates, key=lambda b: b.font_size, default=front[0])
        return ExtractedMetadata(title=best.content[:200])


def _classify_kind(title: str, seen_refs: bool) -> str:
    low = title.lower()
    if "reference" in low or "bibliography" in low:
        return "references"
    if "appendix" in low or "visualization" in low or seen_refs:
        return "appendix"
    return "body"


def build_section_tree(
    blocks: list[ParsedBlock],
    pdf_toc: list[list],
    *,
    is_long_doc: bool = False,
) -> list[SectionNode]:
    """融合 PDF 书签目录与正文标题行，构建一/二/三级标题树（含 References 与 Appendix）。"""
    sections: list[SectionNode] = []
    seen_titles: set[str] = set()
    seen_refs = False

    # 1. 优先吸收 PDF 原生 TOC（对 541 页书籍和规范论文最准确）
    for item in pdf_toc:
        lvl, title, page = int(item[0]), str(item[1]).strip(), int(item[2])
        if lvl > 3 or not title:
            continue
        m = _NUM_HEADING_RE.match(title)
        num = m.group(1) if m else ""
        kind = _classify_kind(title, seen_refs)
        if kind == "references":
            seen_refs = True
        norm_key = re.sub(r"^\d+(?:\.\d+)*\s*", "", title).lower()
        seen_titles.add(norm_key)
        sections.append(
            SectionNode(order=len(sections), level=lvl, number=num, title=title, page=max(1, page), y=0.0, kind=kind)
        )

    # 2. 从正文块补充 PDF TOC 常缺失的 References / Appendix 以及无书签论文的所有章节
    if not is_long_doc or not sections:
        for b in blocks:
            if b.type != "text":
                continue
            txt = b.content.strip()
            if len(txt) > 100:
                continue
            md_lvl = b.extra.get("md_heading_level", 0)
            m = _NUM_HEADING_RE.match(txt)
            sp = _SPECIAL_HEADING_RE.match(txt)

            lvl = 0
            num = ""
            if md_lvl in (1, 2, 3):
                lvl = md_lvl
                num = m.group(1) if m else ""
            elif m and (b.is_bold or b.font_size >= 10.5 or not sections):
                num = m.group(1)
                lvl = min(3, num.count(".") + 1)
            elif sp and (b.is_bold or len(txt.split()) <= 4):
                lvl = 1

            if lvl == 0:
                continue
            norm_key = re.sub(r"^\d+(?:\.\d+)*\s*", "", txt).lower()
            if norm_key in seen_titles:
                # 若是 TOC 里没有的 References / Appendix 仍应补上
                continue
            seen_titles.add(norm_key)
            kind = _classify_kind(txt, seen_refs)
            if kind == "references":
                seen_refs = True
            sections.append(
                SectionNode(
                    order=len(sections),
                    level=lvl,
                    number=num,
                    title=txt,
                    page=b.page,
                    y=b.bbox[1] if b.bbox else 0.0,
                    kind=kind,
                )
            )
    else:
        # 长书籍也补充检查是否有书签外显的 References / Appendix
        for b in blocks:
            if b.type == "text" and len(b.content) < 40 and b.is_bold:
                if re.match(r"^(References|Bibliography|Appendix)\b", b.content.strip(), re.I):
                    norm_key = b.content.strip().lower()
                    if norm_key not in seen_titles:
                        seen_titles.add(norm_key)
                        sections.append(
                            SectionNode(
                                order=len(sections),
                                level=1,
                                number="",
                                title=b.content.strip(),
                                page=b.page,
                                y=b.bbox[1] if b.bbox else 0.0,
                                kind=_classify_kind(b.content.strip(), seen_refs),
                            )
                        )

    # 按页码+纵坐标排序并重建父子关系
    sections.sort(key=lambda s: (s.page, s.y, s.order))
    stack: list[SectionNode] = []
    for i, sec in enumerate(sections):
        sec.order = i
        while stack and stack[-1].level >= sec.level:
            stack.pop()
        sec.parent_order = stack[-1].order if stack else None
        stack.append(sec)
    return sections


def assign_block_sections(blocks: list[ParsedBlock], sections: list[SectionNode]) -> list[int | None]:
    """为每个块匹配所属章节（返回 sections 列表索引）。"""
    if not sections:
        return [None] * len(blocks)
    out: list[int | None] = []
    sec_idx = 0
    for b in blocks:
        by = b.bbox[1] if b.bbox else 0.0
        while sec_idx + 1 < len(sections):
            nxt = sections[sec_idx + 1]
            if (b.page > nxt.page) or (b.page == nxt.page and by + 4.0 >= nxt.y):
                sec_idx += 1
            else:
                break
        cur = sections[sec_idx]
        if b.page > cur.page or (b.page == cur.page and by + 4.0 >= cur.y):
            out.append(sec_idx)
        else:
            out.append(None)
    return out


def _parse_single_reference(idx: int, raw: str, page: int, bbox: list[float]) -> ParsedReference:
    ym = _YEAR_RE.search(raw)
    dm = _DOI_RE.search(raw)
    am = _ARXIV_RE.search(raw)
    # 常见英文参考文献格式: "Author1, Author2. Title of paper. Venue, 2017."
    parts = [p.strip() for p in re.split(r"\.\s+", raw, maxsplit=2) if p.strip()]
    authors = parts[0] if len(parts) >= 1 else ""
    title = parts[1] if len(parts) >= 2 else raw[:180]
    venue = parts[2][:180] if len(parts) >= 3 else ""
    return ParsedReference(
        idx=idx,
        raw_text=raw,
        title=title[:400],
        authors=authors[:300],
        year=int(ym.group(1)) if ym else None,
        venue=venue,
        doi=dm.group(1).rstrip(".") if dm else "",
        arxiv_id=am.group(1) if am else "",
        page=page,
        bbox=bbox,
    )


def parse_references_and_links(
    blocks: list[ParsedBlock],
    sections: list[SectionNode],
    block_sec_idxs: list[int | None],
) -> list[ParsedReference]:
    """拆分 References 条目，并反向扫描正文 `[11]` 建立双向跳转位置锚点。"""
    ref_blocks: list[ParsedBlock] = []
    for b, si in zip(blocks, block_sec_idxs, strict=False):
        if si is not None and sections[si].kind == "references":
            ref_blocks.append(b)
    if not ref_blocks:
        # 兜底：若未划分出 references 节，扫描全文末尾带 `[1]` 开头的块
        ref_blocks = [b for b in blocks if _REF_ENTRY_RE.match(b.content.strip())]

    # 将可能拆在多个块里的 [1]...[2]... 重新按编号拼接
    entries: list[tuple[int, str, int, list[float]]] = []
    cur_idx: int | None = None
    cur_buf: list[str] = []
    cur_page = 0
    cur_bbox: list[float] = []

    def _flush() -> None:
        if cur_idx is not None and cur_buf:
            entries.append((cur_idx, " ".join(cur_buf).strip(), cur_page, cur_bbox))

    for b in ref_blocks:
        # 按 `[n] ` 在同一块内可能出现多条的情况切分
        text = b.content.strip()
        if re.match(r"^(References|Bibliography)\b", text, re.I) and len(text) < 30:
            continue
        # 在块内按 ` [数字] ` 拆分
        chunks = re.split(r"(?=(?:^|\s)\[\d+\]\s+)", text)
        for ch in chunks:
            ch = ch.strip()
            if not ch:
                continue
            m = _REF_ENTRY_RE.match(ch)
            if m:
                _flush()
                cur_idx = int(m.group(1))
                cur_buf = [m.group(2)]
                cur_page = b.page
                cur_bbox = b.bbox
            elif cur_idx is not None:
                cur_buf.append(ch)

    _flush()

    refs = [_parse_single_reference(i, raw, pg, bb) for i, raw, pg, bb in entries]
    by_idx = {r.idx: r for r in refs}

    # 扫描正文块里的引用标记 [11] / [11, 12] / [11-13]
    for b, si in zip(blocks, block_sec_idxs, strict=False):
        if si is not None and sections[si].kind == "references":
            continue
        if b.type not in ("text", "caption"):
            continue
        for m in _CITE_BRACKET_RE.finditer(b.content):
            span_txt = m.group(1)
            nums: list[int] = []
            for part in span_txt.split(","):
                part = part.strip()
                if "-" in part or "–" in part:
                    bounds = re.split(r"[-–]", part)
                    if len(bounds) == 2 and bounds[0].strip().isdigit() and bounds[1].strip().isdigit():
                        a, z = int(bounds[0]), int(bounds[1])
                        if 0 < a <= z and (z - a) <= 15:
                            nums.extend(range(a, z + 1))
                elif part.isdigit():
                    nums.append(int(part))
            for n in nums:
                if n in by_idx and len(by_idx[n].cite_contexts) < 12:
                    s_pos = max(0, m.start() - 50)
                    e_pos = min(len(b.content), m.end() + 50)
                    by_idx[n].cite_contexts.append(
                        {
                            "page": b.page,
                            "bbox": b.bbox,
                            "snippet": b.content[s_pos:e_pos],
                        }
                    )
    return refs
