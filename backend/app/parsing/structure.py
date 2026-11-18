"""学术论文结构化抽取：标题/作者机构/摘要、1-3级标题树、参考文献拆分与正文双向引用关联。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.llm import client as llm
from app.parsing.layout import ParsedBlock

log = logging.getLogger(__name__)

_NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+([A-Z\u4e00-\u9fff][^\n]{1,90})$")
_APP_HEADING_RE = re.compile(r"^([A-H](?:\.\d+){0,2})\.?\s+([A-Z][^\n]{1,75})$")
_SPECIAL_HEADING_RE = re.compile(
    r"^(Abstract|Introduction|Background|Related Work|Conclusion|Conclusions|"
    r"Acknowledgements?|References|Bibliography|Attention Visualizations(?:\b.*)?|"
    r"Appendix(?:\b.*)?)$",
    re.I,
)
_REF_LABEL_RE = re.compile(
    r"^\[(\d{1,4}|[A-Za-z][A-Za-z0-9\+\-\*\s]{0,12}\d{2}[a-z]?)\]\s+(.+)$"
)
_CITE_BRACKET_RE = re.compile(r"\[([A-Za-z0-9\+\-\*,\s–]{1,40})\]")
_AUTH_YEAR_PAREN_RE = re.compile(
    r"\(([A-Z][A-Za-z\-']{1,24})(?:\s+et\s+al\.|\s*(?:&|and)\s*[A-Z][A-Za-z\-']+)?,?\s*((?:19|20)\d{2}[a-z]?)\)"
)
_AUTH_YEAR_TEXT_RE = re.compile(
    r"\b([A-Z][A-Za-z\-']{1,24})\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+)\s*\(((?:19|20)\d{2}[a-z]?)\)"
)
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
    block_order: int = 0


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
    cite_key: str = ""


def _arxiv_id_to_year(arxiv_id: str) -> int | None:
    m = re.match(r"^(\d{2})\d{2}\.\d{4,5}", (arxiv_id or "").strip())
    if not m:
        return None
    yy = int(m.group(1))
    return (1900 + yy) if yy >= 90 else (2000 + yy)


def _normalize_heading_text(txt: str) -> str:
    """折叠 IEEE/Nature 排版中 Small Caps 抽取产生的首字母空格（如 'R EFERENCES' -> 'REFERENCES'）。"""
    s = " ".join((txt or "").split())
    return re.sub(r"\b([A-Z])\s+([A-Z]{2,})\b", r"\1\2", s)


def extract_metadata(
    blocks: list[ParsedBlock],
    fallback_filename: str = "",
    *,
    arxiv_id: str = "",
    meta_year: int | None = None,
) -> ExtractedMetadata:
    """从前 2 页块抽取标题、作者与机构、摘要、年份、会议等。"""
    front = [b for b in blocks if b.page <= 2 and b.type in ("text", "caption")]
    front_text = "\n".join(b.content for b in front[:24])[:3500]
    arxiv_yr = _arxiv_id_to_year(arxiv_id)
    if not front_text.strip():
        return ExtractedMetadata(title=fallback_filename, year=arxiv_yr or meta_year)

    prompt = (
        "请从以下学术论文/书籍首页文本中精准提取结构化元数据。只依据原文内容，未出现字段留空。\n"
        "注意：year 必须是**本文自身**的发表或版权年份（4位整数），绝不要填入正文中引用其他论文的年份（如 Vaswani et al., 2017）；若首页未明确给出本文年份请填 null。\n"
        f"首页文本：\n{front_text}\n\n"
        "返回合法 JSON：\n"
        "{\n"
        '  "title": "论文或书籍完整标题",\n'
        '  "authors": [{"name": "作者姓名", "affiliations": ["所属机构"], "email": "邮箱或空"}],\n'
        '  "affiliations": ["去重后的所有机构名称"],\n'
        '  "abstract": "完整摘要正文（若无则空字符串）",\n'
        '  "keywords": ["关键词"],\n'
        '  "year": null,\n'
        '  "venue": "发表会议/期刊（如 NeurIPS 2017 / arXiv，没有则空）"\n'
        "}"
    )
    try:
        d = llm.chat_json(prompt, model=settings.llm_model_fast, temperature=0.0)
        yr = d.get("year")
        year_val = int(yr) if isinstance(yr, int) or (isinstance(yr, str) and yr.isdigit()) else None
        # 若抽到的年份早于 arXiv 编号年份（常为引言首句引用早年论文的年份误抽），以 arXiv 年份为准
        if arxiv_yr and (year_val is None or year_val < arxiv_yr or year_val > arxiv_yr + 5):
            year_val = arxiv_yr
        if year_val is None:
            year_val = meta_year
        return ExtractedMetadata(
            title=(d.get("title") or fallback_filename).strip(),
            authors=d.get("authors") or [],
            affiliations=d.get("affiliations") or [],
            abstract=(d.get("abstract") or "").strip(),
            keywords=d.get("keywords") or [],
            year=year_val,
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
        return ExtractedMetadata(title=best.content[:200], year=arxiv_yr or meta_year)


def _classify_kind(title: str, seen_refs: bool) -> str:
    low = _normalize_heading_text(title).lower()
    if "reference" in low or "bibliography" in low:
        return "references"
    if re.match(r"^(abstract|introduction|related work|background)\b", low):
        return "body"
    if "appendix" in low or "visualization" in low or seen_refs:
        return "appendix"
    return "body"


def _locate_block_order_for_toc(page: int, title: str, blocks: list[ParsedBlock]) -> int:
    clean_title = _normalize_heading_text(title)
    key_full = re.sub(r"\W+", "", clean_title.lower())[:24]
    key_no_num = re.sub(r"\W+", "", re.sub(r"^(?:\d+(?:\.\d+)*|[A-H])\.?\s*", "", clean_title).lower())[:22]
    page_first_idx: int | None = None
    fallback_idx: int | None = None
    for idx, b in enumerate(blocks):
        if b.page == page:
            if page_first_idx is None:
                page_first_idx = idx
            b_norm = _normalize_heading_text(b.content)
            bkey = re.sub(r"\W+", "", b_norm.lower())
            if len(b_norm) <= len(clean_title) + 35:
                if (key_full and bkey.startswith(key_full[: min(16, len(key_full))])) or (
                    key_no_num and len(key_no_num) >= 5 and key_no_num in bkey
                ):
                    return idx
            elif fallback_idx is None and key_full and key_full in bkey:
                fallback_idx = idx
        elif b.page > page:
            break
    if fallback_idx is not None:
        return fallback_idx
    if page_first_idx is not None:
        return page_first_idx
    for idx, b in enumerate(blocks):
        if b.page > page:
            return idx
    return len(blocks)


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
        lvl, title, page = int(item[0]), _normalize_heading_text(str(item[1])), int(item[2])
        if lvl > 3 or not title:
            continue
        m = _NUM_HEADING_RE.match(title)
        num = m.group(1) if m else ""
        kind = _classify_kind(title, seen_refs)
        if kind == "references":
            seen_refs = True
        norm_key = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", title).lower()
        seen_titles.add(norm_key)
        pg = max(1, page)
        bo = _locate_block_order_for_toc(pg, title, blocks)
        sections.append(
            SectionNode(
                order=len(sections),
                level=lvl,
                number=num,
                title=title,
                page=pg,
                y=blocks[bo].bbox[1] if bo < len(blocks) and blocks[bo].bbox else 0.0,
                kind=kind,
                block_order=bo,
            )
        )

    # 统计前 4 页是否为目录页（连续多行以 "页码数字" 结尾）
    toc_pages: set[int] = set()
    page_trailing_counts: dict[int, int] = {}
    for b in blocks:
        if b.page <= 4 and re.search(r"(?:\.{2,}|\s)\d{1,3}$", b.content.strip()):
            page_trailing_counts[b.page] = page_trailing_counts.get(b.page, 0) + 1
    for pg, cnt in page_trailing_counts.items():
        if cnt >= 4:
            toc_pages.add(pg)

    # 2. 从正文块补充 PDF TOC 常缺失的 References / Appendix 以及无书签论文的所有章节
    if not is_long_doc or not sections:
        for bi, b in enumerate(blocks):
            if b.type != "text" or b.page in toc_pages:
                continue
            txt = _normalize_heading_text(b.content.strip())
            if not txt or len(txt) > 100:
                continue
            md_lvl = b.extra.get("md_heading_level", 0)
            m = _NUM_HEADING_RE.match(txt)
            sp = _SPECIAL_HEADING_RE.match(txt)
            ap = _APP_HEADING_RE.match(txt) if seen_refs else None

            lvl = 0
            num = ""
            if md_lvl in (1, 2, 3):
                lvl = md_lvl
                num = m.group(1) if m else ""
            elif m and (b.is_bold or b.font_size >= 10.5 or not sections):
                # 过滤像 "1 Introduction 3" 这样自带页码的孤立目录行
                if not (b.page <= 3 and re.search(r"\s\d{1,3}$", txt)):
                    num = m.group(1)
                    lvl = min(3, num.count(".") + 1)
            elif ap and (b.is_bold or b.font_size >= 10.8) and len(txt.split()) <= 10 and not _YEAR_RE.search(txt):
                num = ap.group(1)
                lvl = min(3, num.count(".") + 1)
            elif sp and ((b.is_bold and len(txt.split()) <= 10) or len(txt.split()) <= 3):
                lvl = 1

            if lvl == 0:
                continue
            norm_key = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", txt).lower()
            if norm_key in seen_titles:
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
                    block_order=bi,
                )
            )
    else:
        # 长书籍也补充检查是否有书签外显的 References / Appendix
        for bi, b in enumerate(blocks):
            if b.type == "text" and len(b.content) < 40 and b.is_bold:
                txt = _normalize_heading_text(b.content.strip())
                if re.match(r"^(References|Bibliography|Appendix)\b", txt, re.I):
                    norm_key = txt.lower()
                    if norm_key not in seen_titles:
                        seen_titles.add(norm_key)
                        kind = _classify_kind(txt, seen_refs)
                        if kind == "references":
                            seen_refs = True
                        sections.append(
                            SectionNode(
                                order=len(sections),
                                level=1,
                                number="",
                                title=txt,
                                page=b.page,
                                y=b.bbox[1] if b.bbox else 0.0,
                                kind=kind,
                                block_order=bi,
                            )
                        )

    # 按阅读顺序 (page, block_order, order) 排序——绝不用纯 y 坐标排序，避免破坏双栏左右顺序
    sections.sort(key=lambda s: (s.page, s.block_order, s.order))
    seen_refs = False
    stack: list[SectionNode] = []
    for i, sec in enumerate(sections):
        sec.kind = _classify_kind(sec.title, seen_refs)
        if sec.kind == "references":
            seen_refs = True
        sec.order = i
        while stack and stack[-1].level >= sec.level:
            stack.pop()
        sec.parent_order = stack[-1].order if stack else None
        stack.append(sec)
    return sections


def assign_block_sections(blocks: list[ParsedBlock], sections: list[SectionNode]) -> list[int | None]:
    """为每个块匹配所属章节（基于阅读流序号单调推进，完全兼容单双栏阅读顺序）。"""
    if not sections:
        return [None] * len(blocks)
    out: list[int | None] = []
    sec_idx = 0
    for bi, b in enumerate(blocks):
        while sec_idx + 1 < len(sections):
            nxt = sections[sec_idx + 1]
            if (b.page > nxt.page) or (b.page == nxt.page and bi >= nxt.block_order):
                sec_idx += 1
            else:
                break
        cur = sections[sec_idx]
        if (b.page > cur.page) or (b.page == cur.page and bi >= cur.block_order):
            out.append(sec_idx)
        else:
            out.append(None)
    return out


def _split_author_title_venue(raw: str) -> tuple[str, str, str]:
    """智能拆分参考文献的 authors / title / venue，兼容 'A. Vaswani'、'Ba, J. L.' 与 IEEE 引号标题格式。"""
    # 情况 0: IEEE/Nature 逗号+引号标题格式：K. Simonyan and A. Zisserman, “Very deep ...,” Venue, 2014.
    qm = re.search(r'[“"]([^"”]{10,260})[”"]', raw)
    if qm and qm.start() >= 4:
        authors = raw[: qm.start()].strip().rstrip(",.")
        title = qm.group(1).strip().rstrip(",.")
        venue = raw[qm.end() :].strip().lstrip(",. ")[:180]
        return authors, title, venue

    # 情况 1: "Authors. 2018. Title. Venue" 或 "Authors (2018). Title. Venue"
    ym = re.search(r"(?:\.\s+|\s*\()(?:19|20)\d{2}[a-z]?\)?\.\s+", raw)
    if ym and 6 <= ym.start() <= len(raw) - 10:
        authors = raw[: ym.start()].strip().rstrip(".")
        rest = raw[ym.end() :].strip()
        segs = [s.strip() for s in re.split(r"(?<!\b[A-Z])(?<!\bet al)\.\s+", rest, maxsplit=1) if s.strip()]
        title = segs[0] if segs else rest[:180]
        venue = segs[1][:180] if len(segs) > 1 else ""
        return authors, title, venue

    # 情况 2: 不在单个大写缩写（如 A. Vaswani）或 et al. 后切句号
    parts = [p.strip() for p in re.split(r"(?<!\b[A-Z])(?<!\bet al)\.\s+", raw, maxsplit=3) if p.strip()]
    if len(parts) >= 2:
        # 处理 "Ba, J. L., Kiros, J. R., and Hinton, G. E. Layer normalization" 情况：
        # 单字母缩写 E. 后直接紧跟多词英文标题，未在前步切开
        m_tail = re.match(
            r"^(.*?\b[A-Z][A-Za-z\-']+,\s*(?:[A-Z]\.\s*){1,4})([A-Z][A-Za-z0-9\"“:\-]{2,}\s+.+)$",
            parts[0],
        )
        if m_tail:
            authors = m_tail.group(1).strip()
            title = m_tail.group(2).strip()
            venue = ". ".join(parts[1:])[:180]
            return authors, title, venue
        authors = parts[0]
        title = parts[1]
        venue = ". ".join(parts[2:])[:180] if len(parts) >= 3 else ""
        return authors, title, venue

    return "", raw[:180], ""


def _parse_single_reference(
    idx: int,
    raw: str,
    page: int,
    bbox: list[float],
    cite_key: str = "",
) -> ParsedReference:
    ym = _YEAR_RE.search(raw)
    dm = _DOI_RE.search(raw)
    am = _ARXIV_RE.search(raw)
    authors, title, venue = _split_author_title_venue(raw)
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
        cite_key=cite_key,
    )


def _join_ref_chunks(buf: list[str]) -> str:
    out = ""
    for part in buf:
        p = part.strip()
        if not p:
            continue
        if not out:
            out = p
        elif out.endswith("-") and p[:1].islower():
            out = out[:-1] + p
        else:
            out = f"{out} {p}"
    return out


def _split_unnumbered_hanging_indent(ref_blocks: list[ParsedBlock]) -> list[tuple[int, str, str, int, list[float]]]:
    """解析无编号 Author-Year 悬挂缩进格式（BERT / GPT-2 / Pre-LN / ACL / ICML / NeurIPS）。"""
    pw = max((b.extra.get("page_w", 0.0) for b in ref_blocks), default=612.0) or 612.0
    mid = pw * 0.5

    # 跳过页眉与纯页码行
    filtered: list[ParsedBlock] = []
    for b in ref_blocks:
        txt = b.content.strip()
        if not txt:
            continue
        if re.match(r"^(?:R\s*eferences|Bibliography)\b", txt, re.I) and len(txt) < 30:
            continue
        if b.bbox and b.bbox[1] < 60 and len(txt) < 85:
            continue
        if txt.isdigit() and len(txt) <= 3:
            continue
        filtered.append(b)

    if not filtered:
        return []

    left_xs = [float(b.extra.get("x0", b.bbox[0])) for b in filtered if (b.bbox[0] + b.bbox[2]) / 2 <= mid + 10]
    right_xs = [float(b.extra.get("x0", b.bbox[0])) for b in filtered if (b.bbox[0] + b.bbox[2]) / 2 > mid + 10]
    base_left = min(left_xs) if left_xs else 0.0
    base_right = min(right_xs) if right_xs else mid

    entries: list[tuple[int, str, str, int, list[float]]] = []
    cur_buf: list[str] = []
    cur_page = 0
    cur_bbox: list[float] = []

    def _flush() -> None:
        raw = _join_ref_chunks(cur_buf)
        if raw and len(raw) >= 18:
            entries.append((len(entries) + 1, "", raw, cur_page, cur_bbox))

    for b in filtered:
        txt = b.content.strip()
        bx0 = float(b.extra.get("x0", b.bbox[0]))
        cx = (b.bbox[0] + b.bbox[2]) / 2 if b.bbox else bx0
        base = base_right if (right_xs and cx > mid + 10) else base_left
        # 顶格且首字符为作者姓氏大写字母（排除年份/页码/小写单词续行）
        is_flush = bx0 <= base + 3.2
        looks_continuation = bool(re.match(r"^(?:[a-z]|(?:19|20)\d{2}\.|in\s|pages?\s|arXiv)", txt))
        if is_flush and not looks_continuation:
            _flush()
            cur_buf = [txt]
            cur_page = b.page
            cur_bbox = b.bbox
        else:
            if not cur_buf:
                cur_page = b.page
                cur_bbox = b.bbox
            cur_buf.append(txt)
    _flush()
    return entries


def parse_references_and_links(
    blocks: list[ParsedBlock],
    sections: list[SectionNode],
    block_sec_idxs: list[int | None],
) -> list[ParsedReference]:
    """拆分 References 条目（支持 [1] / [ADG+16] / 无编号悬挂缩进三种学术格式），并建立正文双向锚点。"""
    ref_blocks: list[ParsedBlock] = []
    for b, si in zip(blocks, block_sec_idxs, strict=False):
        if si is not None and sections[si].kind == "references":
            ref_blocks.append(b)
    if not ref_blocks:
        ref_blocks = [b for b in blocks if _REF_LABEL_RE.match(b.content.strip())]

    # 1) 优先按带标签格式 [1] 或 [ADG+16] 解析
    entries: list[tuple[int, str, str, int, list[float]]] = []
    cur_idx: int | None = None
    cur_key = ""
    cur_buf: list[str] = []
    cur_page = 0
    cur_bbox: list[float] = []

    def _flush() -> None:
        if cur_idx is not None and cur_buf:
            entries.append((cur_idx, cur_key, _join_ref_chunks(cur_buf), cur_page, cur_bbox))

    for b in ref_blocks:
        text = b.content.strip()
        if re.match(r"^(?:R\s*eferences|Bibliography)\b", text, re.I) and len(text) < 30:
            continue
        if b.bbox and b.bbox[1] < 60 and len(text) < 85 and not _REF_LABEL_RE.match(text):
            continue
        chunks = re.split(
            r"(?=(?:^|\s)\[(?:\d{1,4}|[A-Za-z][A-Za-z0-9\+\-\*\s]{0,12}\d{2}[a-z]?)\]\s+)",
            text,
        )
        for ch in chunks:
            ch = ch.strip()
            if not ch:
                continue
            m = _REF_LABEL_RE.match(ch)
            if m:
                _flush()
                raw_tag = re.sub(r"\s+", "", m.group(1))
                cur_idx = int(raw_tag) if raw_tag.isdigit() else (len(entries) + 1)
                cur_key = raw_tag
                cur_buf = [m.group(2)]
                cur_page = b.page
                cur_bbox = b.bbox
            elif cur_idx is not None:
                cur_buf.append(ch)
    _flush()

    # 2) 若几乎没有方括号编号条目，启用无编号悬挂缩进解析器（BERT / GPT-2 / Pre-LN 等）
    if len(entries) < 3 and len(ref_blocks) >= 3:
        unnumbered = _split_unnumbered_hanging_indent(ref_blocks)
        if len(unnumbered) > len(entries):
            entries = unnumbered

    refs = [
        _parse_single_reference(i, raw, pg, bb, cite_key=ck)
        for i, ck, raw, pg, bb in entries
    ]
    by_idx = {r.idx: r for r in refs}
    by_key = {r.cite_key.lower(): r for r in refs if r.cite_key}

    # Author-Year 反向索引：surname_lower -> list[ParsedReference]
    by_surname: dict[str, list[ParsedReference]] = {}
    for r in refs:
        head = (r.authors or r.raw_text)[:80]
        # 同时索引首个姓氏（"Ba, J. L." -> "ba"）与首句末尾姓氏（"Jimmy Lei Ba" -> "ba"）
        first_part = re.split(r"[,&]|\band\b", head, maxsplit=1)[0].strip()
        words = re.findall(r"[A-Za-z\-']{2,}", first_part)
        for w in ({words[0].lower(), words[-1].lower()} if words else set()):
            by_surname.setdefault(w, []).append(r)

    # 3) 扫描正文块里的引用标记（[11] / [ADG+16] / Author et al., 2018）建立双向锚点
    for b, si in zip(blocks, block_sec_idxs, strict=False):
        if si is not None and sections[si].kind == "references":
            continue
        if b.type not in ("text", "caption"):
            continue
        matched_refs: list[tuple[ParsedReference, int, int]] = []

        for m in _CITE_BRACKET_RE.finditer(b.content):
            span_txt = m.group(1)
            for part in span_txt.split(","):
                part_clean = re.sub(r"\s+", "", part)
                if not part_clean:
                    continue
                if re.match(r"^\d+[-–]\d+$", part_clean):
                    a_s, z_s = re.split(r"[-–]", part_clean)
                    a, z = int(a_s), int(z_s)
                    if 0 < a <= z and (z - a) <= 15:
                        for n in range(a, z + 1):
                            if n in by_idx:
                                matched_refs.append((by_idx[n], m.start(), m.end()))
                elif part_clean.isdigit():
                    n = int(part_clean)
                    if n in by_idx:
                        matched_refs.append((by_idx[n], m.start(), m.end()))
                elif part_clean.lower() in by_key:
                    matched_refs.append((by_key[part_clean.lower()], m.start(), m.end()))

        if by_surname:
            for pat in (_AUTH_YEAR_PAREN_RE, _AUTH_YEAR_TEXT_RE):
                for m in pat.finditer(b.content):
                    surname = m.group(1).lower()
                    yr_digits = int(m.group(2)[:4])
                    for cand in by_surname.get(surname, []):
                        if cand.year is None or abs(cand.year - yr_digits) <= 1:
                            matched_refs.append((cand, m.start(), m.end()))
                            break

        for target_ref, ms, me in matched_refs:
            if len(target_ref.cite_contexts) >= 12:
                continue
            s_pos = max(0, ms - 50)
            e_pos = min(len(b.content), me + 50)
            if any(c.get("page") == b.page and c.get("snippet") == b.content[s_pos:e_pos] for c in target_ref.cite_contexts):
                continue
            target_ref.cite_contexts.append(
                {"page": b.page, "bbox": b.bbox, "snippet": b.content[s_pos:e_pos]}
            )
    return refs
