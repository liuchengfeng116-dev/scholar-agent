"""技术文档构建脚本：docs/technical_report.md -> docs/ScholarAgent_技术文档.pdf

功能：
1. 生成高清系统总体架构图 docs/arch_diagram.png（文字换行适配，无边界重叠）；
2. 将 docs/technical_report.md 转为学术排版 HTML（自动封面、可点击目录、图表图注与页眉页脚）；
3. 驱动本机 Microsoft Edge (Playwright) 打印为带子集化字体、<=30 页的轻量 A4 PDF。

用法：.venv\\Scripts\\python.exe scripts\\build_tech_doc.py
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown
import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pymupdf
from playwright.sync_api import sync_playwright

matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SRC_MD = DOCS / "technical_report.md"
OUT_HTML = DOCS / "technical_report.html"
OUT_PDF = DOCS / "ScholarAgent_技术文档.pdf"
MAX_PAGES = 30

CSS = """
@page { size: A4; }
html { font-size: 10.5pt; }
body { font-family: "Microsoft YaHei", "PingFang SC", "SimSun", sans-serif; color: #1f2937; line-height: 1.7; }
code, pre { font-family: Consolas, "Cascadia Mono", monospace; }
.cover { height: 247mm; display: flex; flex-direction: column; justify-content: center; text-align: center; page-break-after: always; }
.cover .tag { color: #1e3a8a; font-size: 13pt; letter-spacing: 2px; margin-bottom: 14mm; }
.cover h1 { font-size: 30pt; color: #0f172a; margin: 0 0 6mm; border: none; }
.cover .sub { font-size: 14pt; color: #334155; margin-bottom: 30mm; }
.cover table { margin: 0 auto; border: none; font-size: 12pt; width: auto; }
.cover td { border: none; padding: 2.5mm 6mm; text-align: left; }
.cover td:first-child { color: #64748b; text-align: right; white-space: nowrap; }
.toc { page-break-after: always; }
.toc h2 { border: none; }
.toc ul { list-style: none; padding-left: 0; }
.toc li { margin: 1.6mm 0; }
.toc li.l3 { padding-left: 8mm; font-size: 9.8pt; color: #475569; }
.toc a { color: inherit; text-decoration: none; }
h1 { font-size: 18pt; color: #0f172a; }
h2 { font-size: 15pt; color: #1e3a8a; border-bottom: 2px solid #1e3a8a; padding-bottom: 1.5mm; margin-top: 0; page-break-before: always; }
h2.nobreak { page-break-before: auto; }
h3 { font-size: 12pt; color: #0f172a; margin: 5mm 0 2mm; page-break-after: avoid; }
h4 { font-size: 10.8pt; color: #334155; margin: 3.5mm 0 1.5mm; page-break-after: avoid; }
p { margin: 1.6mm 0; text-align: justify; }
ul, ol { margin: 1.5mm 0; padding-left: 6mm; }
li { margin: 0.8mm 0; }
table { border-collapse: collapse; width: 100%; margin: 3mm 0; font-size: 8.8pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #e0e7ff; color: #1e3a8a; font-weight: 600; white-space: nowrap; }
th, td { border: 1px solid #cbd5e1; padding: 1.3mm 1.8mm; vertical-align: top; }
td code { font-size: 8.2pt; word-break: break-word; }
tbody tr:nth-child(even) td { background: #f8fafc; }
code { background: #f1f5f9; padding: 0 1mm; border-radius: 2px; font-size: 9pt; color: #0f172a; }
pre { background: #0f172a; color: #e2e8f0; padding: 3mm; border-radius: 4px; font-size: 8.4pt; line-height: 1.5; white-space: pre-wrap; page-break-inside: avoid; }
pre code { background: none; color: inherit; padding: 0; }
blockquote { margin: 3mm 0; padding: 2mm 4mm; background: #eff6ff; border-left: 4px solid #3b82f6; color: #1e3a8a; }
img { display: block; max-width: 96%; max-height: 112mm; width: auto; height: auto; margin: 3mm auto 1mm; border: 1px solid #cbd5e1; border-radius: 3px; page-break-inside: avoid; }
.caption { text-align: center; font-size: 9pt; color: #475569; margin-bottom: 3mm; }
strong { color: #0f172a; }
"""


def draw_arch_diagram(out_png: Path) -> None:
    """绘制清晰无重叠的系统五层总体架构图。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(10.6, 5.8), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 62)
    ax.axis("off")

    def box(x: float, y: float, w: float, h: float, title: str, sub: str = "", *, fc: str, ec: str) -> None:
        patch = mpatches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.5,rounding_size=1.2", facecolor=fc, edgecolor=ec, linewidth=1.5
        )
        ax.add_patch(patch)
        n_lines = sub.count("\n") + 1 if sub else 0
        title_y = y + h * (0.72 if n_lines >= 2 else (0.64 if n_lines == 1 else 0.5))
        ax.text(x + w / 2, title_y, title, ha="center", va="center", fontsize=9.6, fontweight="bold", color="#0F172A")
        if sub:
            sub_y = y + h * (0.34 if n_lines >= 2 else 0.30)
            ax.text(x + w / 2, sub_y, sub, ha="center", va="center", fontsize=7.6, color="#334155", linespacing=1.35)

    box(
        3,
        49,
        94,
        10,
        "前端展现与交互层（React 18 + Vite + TypeScript + Ant Design 5 + PDF.js + ECharts + KaTeX + Mermaid）",
        "文献库与去重弹窗  |  PDF.js 版式阅读 / [11] 双向跳转 / 双语同步滚动  |  SSE 流式问答与轨迹\n"
        "引用含金量下钻与 Sickle 推荐  |  ECharts 引用图谱与综述  |  无幻觉写作与 AST 沙箱可视化",
        fc="#DBEAFE",
        ec="#1D4ED8",
    )

    box(3, 36, 29, 9.5, "FastAPI 网关与鉴权层", "REST + SSE 流式推送 / JWT 多用户隔离\nAPScheduler 定时调度 + 持久化 Task 队列", fc="#F1F5F9", ec="#334155")
    box(34.5, 36, 32.5, 9.5, "Multi-Agent 中央编排器", "Task Planner 意图识别与多步规划\n工具空回动态重规划 + @tool 证据追踪", fc="#EDE9FE", ec="#6D28D9")
    box(69.5, 36, 27.5, 9.5, "Memory 记忆与画像层", "短期 6 轮滑窗 + 超长滚动摘要压缩\n长期科研主题 / 事实 / 点赞偏好权重", fc="#FCE7F3", ec="#BE185D")

    agents = [
        (3, "Parser Agent", "版面/OCR/公式\n三模参考文献/去重"),
        (22.2, "Retriever Agent", "BM25+向量+RRF\n多文均衡召回"),
        (41.4, "Scout Agent", "含金量评分筛选\nSickle / GitHub加权"),
        (60.6, "Graph Agent", "NetworkX PageRank\n综述/FutureWork去伪"),
        (79.8, "Writer Agent", "Crossref实名校验\nAST沙箱三维雷达图"),
    ]
    for x, name, sub in agents:
        box(x, 20.5, 17.2, 11.5, name, sub, fc="#ECFDF5", ec="#059669")

    box(
        3,
        3.5,
        45.5,
        12.5,
        "抗幻觉与安全防护层（Critic + Guardrails + Sandbox）",
        "物理锚点 (page, bbox) 强制绑定  |  正则数值硬比对\n"
        "Critic 逐句可信度审查  |  无证据拒答 & AST 语法树审计",
        fc="#FEF3C7",
        ec="#D97706",
    )
    box(
        51.5,
        3.5,
        45.5,
        12.5,
        "通义千问异构模型簇 & 混合存储底座",
        "qwen-plus / qwen-max / qwen-flash / qwen3-vl-plus / text-embedding-v3\n"
        "SQLite WAL + Chroma HNSW + Rank-BM25 + arXiv/S2/OpenAlex/Crossref",
        fc="#E0F2FE",
        ec="#0284C7",
    )

    ax.set_title("ScholarAgent（智研助手）全栈 Multi-Agent 系统总体架构图", fontsize=11.5, fontweight="bold", pad=6)
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)


def build_html() -> str:
    md_text = SRC_MD.read_text(encoding="utf-8")
    lines = md_text.splitlines()
    title = lines[0].lstrip("# ").strip()
    meta: list[tuple[str, str]] = []
    body_start = 1
    for i, line in enumerate(lines[1:], start=1):
        m = re.match(r"^\*\*(.+?)\*\*[：:]\s*(.+?)\s*$", line)
        if m:
            meta.append((m.group(1), m.group(2)))
            body_start = i + 1
        elif line.strip() in ("", "---"):
            body_start = i + 1
            if meta and line.strip() == "---":
                break
        else:
            break
    body_md = "\n".join(lines[body_start:])

    html_body = markdown.markdown(body_md, extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    html_body = re.sub(r"<hr\s*/?>\s*(?=<h2\b)", "", html_body)
    html_body = re.sub(r"<p><em>(图\s*\d+[^<]*)</em></p>", r'<p class="caption">\1</p>', html_body)

    toc: list[tuple[int, str, str]] = []
    counter = 0

    def _anchor(m: re.Match) -> str:
        nonlocal counter
        counter += 1
        level, text = int(m.group(1)), m.group(2)
        sid = f"sec-{counter}"
        toc.append((level, re.sub(r"<.*?>", "", text), sid))
        return f'<h{level} id="{sid}">{text}</h{level}>'

    html_body = re.sub(r"<h([23])>(.*?)</h\1>", _anchor, html_body)
    html_body = html_body.replace('<h2 id="sec-1">', '<h2 class="nobreak" id="sec-1">', 1)

    toc_html = "".join(
        f'<li class="l{lv}"><a href="#{sid}">{text}</a></li>' for lv, text, sid in toc
    )
    meta_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in meta)
    cover = (
        '<div class="cover">'
        '<div class="tag">传智杯 · AI 大模型创新应用挑战赛 · 参赛作品技术文档</div>'
        f"<h1>{title.split('——')[0].strip()}</h1>"
        '<div class="sub">基于大模型与 Multi-Agent 架构的全流程科研助手</div>'
        f"<table>{meta_rows}</table></div>"
    )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>{CSS}</style></head><body>"
        f"{cover}<div class='toc'><h2 class='nobreak'>目录</h2><ul>{toc_html}</ul></div>"
        f"{html_body}</body></html>"
    )


def render_pdf(html: str) -> int:
    OUT_HTML.write_text(html, encoding="utf-8")
    try:
        footer = (
            '<div style="width:100%;font-size:8px;color:#94a3b8;padding:0 18mm;'
            'display:flex;justify-content:space-between;font-family:Microsoft YaHei;">'
            "<span>ScholarAgent · 智研助手 —— 技术文档</span>"
            '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.goto(OUT_HTML.as_uri(), wait_until="networkidle")
            page.pdf(
                path=str(OUT_PDF),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=footer,
                margin={"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"},
            )
            browser.close()
    finally:
        OUT_HTML.unlink(missing_ok=True)
    with pymupdf.open(OUT_PDF) as doc:
        pages = doc.page_count
        doc.set_metadata(
            {"title": "ScholarAgent 智研助手 技术文档", "author": "ScholarAgent", "subject": "传智杯 AI 大模型创新应用挑战赛"}
        )
        doc.saveIncr()
    return pages


def main() -> None:
    draw_arch_diagram(DOCS / "arch_diagram.png")
    pages = render_pdf(build_html())
    size_mb = OUT_PDF.stat().st_size / 1024 / 1024
    status = "OK" if pages <= MAX_PAGES else f"超出 {MAX_PAGES} 页上限!"
    print(f"{OUT_PDF.name}: {pages} 页, {size_mb:.2f} MB  [{status}]")


if __name__ == "__main__":
    main()
