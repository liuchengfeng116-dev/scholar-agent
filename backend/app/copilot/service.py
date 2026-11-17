"""模块六核心服务：无幻觉论文框架、架构拓扑图脚本、CSV 数据可视化/三维雷达图与学术翻译。"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.copilot.sandbox import run_chart_script
from app.core.config import settings
from app.db.models import Paper
from app.llm import client as llm
from app.scout import service as scout_svc

log = logging.getLogger(__name__)


def _assets_dir() -> Path:
    d = settings.data_path / "copilot"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _verify_with_crossref(query: str, limit: int = 5) -> list[dict]:
    """实时向 Crossref 检索真实出版物并提取标准 DOI 元数据。"""
    out: list[dict] = []
    try:
        headers = {"User-Agent": f"ScholarAgent/0.1 (mailto:{settings.crossref_mailto or 'demo@example.com'})"}
        r = httpx.get(
            "https://api.crossref.org/works",
            params={"query": query, "rows": limit},
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            return []
        for it in (r.json().get("message") or {}).get("items") or []:
            title = " ".join(it.get("title") or []).strip()
            if not title:
                continue
            authors = ", ".join(
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (it.get("author") or [])[:5]
            )
            year = None
            issued = (it.get("issued") or {}).get("date-parts") or [[]]
            if issued and issued[0]:
                year = issued[0][0]
            venue = " ".join(it.get("container-title") or [])[:120]
            doi = it.get("DOI") or ""
            out.append(
                {
                    "title": title,
                    "authors": authors or "Unknown",
                    "year": year,
                    "venue": venue or "Crossref Indexed",
                    "doi": doi,
                    "arxiv_id": "",
                    "url": f"https://doi.org/{doi}" if doi else "",
                    "verification_source": "Crossref",
                    "abstract": re.sub(r"<[^>]+>", "", it.get("abstract") or "")[:400],
                }
            )
    except Exception:  # noqa: BLE001
        log.debug("Crossref 检索失败")
    return out


# ----------------------------------------------------------------------------
# 1. 无幻觉论文框架生成（真实文献校验池 + 严格引用过滤）
# ----------------------------------------------------------------------------
def generate_outline(
    db: Session,
    user_id: int,
    *,
    idea: str,
    paper_ids: list[int] | None = None,
    lang: str = "en",
) -> dict:
    """根据研究 idea 生成 Abstract / Introduction / Related Work 及 100% 校验无幻觉 References。"""
    # 收集真实可信文献池：用户库 + Crossref + Semantic Scholar / OpenAlex / arXiv
    pool: list[dict] = []
    seen: set[str] = set()

    def _add_ref(item: dict) -> None:
        t = (item.get("title") or "").strip()
        if not t or len(t) < 6:
            return
        key = re.sub(r"[^a-z0-9]+", "", t.lower())[:60]
        if key in seen:
            return
        seen.add(key)
        pool.append(item)

    pq = db.query(Paper).filter(Paper.owner_id == user_id, Paper.status == "ready")
    if paper_ids:
        pq = pq.filter(Paper.id.in_(paper_ids))
    for p in pq.limit(8).all():
        authors = ", ".join(
            a.get("name", "") if isinstance(a, dict) else str(a) for a in (p.authors or [])[:5]
        )
        _add_ref(
            {
                "title": p.title or p.filename,
                "authors": authors or "Local Paper",
                "year": p.year,
                "venue": p.venue or ("arXiv" if p.arxiv_id else "Local Library"),
                "doi": p.doi or "",
                "arxiv_id": p.arxiv_id or "",
                "url": f"https://arxiv.org/abs/{p.arxiv_id}" if p.arxiv_id else "",
                "verification_source": "LocalLibrary",
                "abstract": (p.abstract or "")[:350],
            }
        )

    # 提取英文学术检索词以确保 Crossref / Semantic Scholar 高命中
    try:
        kw_query = llm.complete(
            f"将下面研究想法提炼为 4-6 个英文学术检索关键词（只输出空格分隔的英文词）：{idea[:400]}",
            model=settings.llm_model_fast,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001
        kw_query = idea[:80]

    for ext in scout_svc.search_external_papers(kw_query, limit=8):
        if ext.get("doi") or ext.get("arxiv_id") or ext.get("citations", 0) > 0:
            _add_ref(
                {
                    "title": ext["title"],
                    "authors": ext.get("authors", ""),
                    "year": ext.get("year"),
                    "venue": ext.get("venue", "") or ext.get("source", ""),
                    "doi": ext.get("doi", ""),
                    "arxiv_id": ext.get("arxiv_id", ""),
                    "url": f"https://arxiv.org/abs/{ext['arxiv_id']}"
                    if ext.get("arxiv_id")
                    else (f"https://doi.org/{ext['doi']}" if ext.get("doi") else ""),
                    "verification_source": ext.get("source", "SemanticScholar"),
                    "abstract": (ext.get("abstract") or "")[:350],
                }
            )
    for cr in _verify_with_crossref(kw_query, limit=6):
        _add_ref(cr)

    pool = pool[:15]
    indexed_refs = {f"R{i + 1}": item for i, item in enumerate(pool)}
    ref_prompt_list = "\n".join(
        f"[{rid}] {r['authors']} ({r['year'] or '?'}). {r['title']}. {r['venue']}."
        + (f" Abstract: {r['abstract'][:200]}" if r.get("abstract") else "")
        for rid, r in indexed_refs.items()
    )

    out_lang = "规范学术英文" if lang == "en" else "严谨学术中文"
    prompt = (
        f"你是顶会审稿人兼论文写作 Copilot。请根据用户的研究想法，使用{out_lang}输出结构完整的论文核心框架。\n"
        "【防幻觉硬性约束】\n"
        "1. 正文（尤其是 Related Work 与 Introduction）只能引用下方真实文献池中的编号 `[R1]`、`[R2]` 等；\n"
        "2. 严禁虚构任何未出现在列表中的论文标题、作者或指标；\n"
        "3. 框架包含 Title、Abstract、Introduction（含研究动机与核心贡献点）、Related Work（按技术脉络分组评述并引用 `[R#]`）、Proposed Method Outline。\n\n"
        f"研究想法（Idea）：\n{idea}\n\n"
        f"已校验真实参考文献池：\n{ref_prompt_list or '（暂无外部文献命中）'}\n\n"
        "返回 JSON：\n"
        "{\n"
        '  "title": "拟定论文标题",\n'
        '  "abstract": "摘要正文",\n'
        '  "introduction": "引言正文（Markdown）",\n'
        '  "related_work": "相关工作正文（Markdown，带 [R#] 引用）",\n'
        '  "method_outline": "方法章节提纲（Markdown）",\n'
        '  "cited_keys": ["R1", "R2"]\n'
        "}"
    )
    res = llm.chat_json(prompt, model=settings.llm_model_strong, temperature=0.3)

    # 后置校验：从正文中抽取真实使用的 [R#]，丢弃所有不存在池中的伪造编号
    combined_body = f"{res.get('abstract', '')}\n{res.get('introduction', '')}\n{res.get('related_work', '')}"
    used_keys: list[str] = []
    for k in re.findall(r"\[(R\d+)\]", combined_body):
        if k in indexed_refs and k not in used_keys:
            used_keys.append(k)
    if not used_keys and indexed_refs:
        used_keys = list(indexed_refs.keys())[:5]

    verified_refs = [{"key": k, "verified": True, **indexed_refs[k]} for k in used_keys]
    return {
        "title": res.get("title", ""),
        "abstract": res.get("abstract", ""),
        "introduction": res.get("introduction", ""),
        "related_work": res.get("related_work", ""),
        "method_outline": res.get("method_outline", ""),
        "references": verified_refs,
        "pool_size": len(pool),
    }


# ----------------------------------------------------------------------------
# 2. 架构拓扑图脚本生成（TikZ / Graphviz / Mermaid / Matplotlib）
# ----------------------------------------------------------------------------
def generate_diagram(idea: str, fmt: str = "mermaid") -> dict:
    """生成架构拓扑图脚本（支持 tikz / graphviz / mermaid / matplotlib），并在适用时渲染预览图。"""
    fmt = fmt.lower()
    if fmt not in ("tikz", "graphviz", "mermaid", "matplotlib"):
        raise ValueError("fmt 必须为 tikz / graphviz / mermaid / matplotlib")

    format_rules = {
        "mermaid": "输出可直接渲染的 Mermaid `flowchart LR` 或 `flowchart TD` 代码，节点标签如含括号/特殊字符必须用双引号包裹，如 A[\"Input (x)\"]。",
        "tikz": "输出完整可编译的 LaTeX TikZ 源码（含 \\documentclass{standalone}、\\usepackage{tikz} 与样式定义），结构清晰美观。",
        "graphviz": "输出美观的 Graphviz DOT 源码（digraph G { rankdir=LR; node [shape=box, style=\"rounded,filled\", fillcolor=\"#EEF2FF\"]; ... }）。",
        "matplotlib": (
            "输出一段干净的 Python Matplotlib 脚本，仅使用 `plt` / `matplotlib.patches.FancyBboxPatch` 绘制论文级架构拓扑图，"
            "最后必须调用 `plt.savefig(OUT_PNG, dpi=180, bbox_inches='tight')` 与 `plt.close()`。"
            "禁止导入 os/sys/subprocess/requests 等系统或网络模块，不要自行定义 OUT_PNG（运行环境已提供）。"
        ),
    }

    prompt = (
        f"请为以下科研系统/模型构想绘制一张专业、清晰的架构拓扑图，目标格式为 `{fmt}`。\n"
        f"格式规范：{format_rules[fmt]}\n"
        f"研究构想：{idea}\n\n"
        '返回 JSON：{"code": "纯脚本代码（不含Markdown围栏）", "caption": "一句话图注说明"}'
    )
    res = llm.chat_json(prompt, model=settings.llm_model, temperature=0.2)
    code = (res.get("code") or "").strip()
    code = re.sub(r"^```[a-zA-Z]*\s*", "", code)
    code = re.sub(r"\s*```$", "", code)

    preview_url = ""
    render_error = ""
    if fmt == "matplotlib" and code:
        fname = f"diag_{uuid.uuid4().hex[:10]}.png"
        out_png = _assets_dir() / fname
        try:
            ok, msg = run_chart_script(code, work_dir=_assets_dir(), out_png=out_png)
            if ok:
                preview_url = f"/api/copilot/assets/{fname}"
            else:
                render_error = msg[:300]
        except Exception as e:  # noqa: BLE001
            render_error = str(e)

    return {
        "format": fmt,
        "code": code,
        "caption": res.get("caption", ""),
        "preview_url": preview_url,
        "render_error": render_error,
    }


# ----------------------------------------------------------------------------
# 3. CSV 数据可视化（表格/折线图/柱状图/三维雷达图）与学术图注提炼
# ----------------------------------------------------------------------------
def _build_default_chart_code(df: pd.DataFrame, chart_type: str, title: str) -> str:
    """高可靠性确定性 Matplotlib 绘图模板（表格 / 折线 / 柱状 / 三维雷达图）。"""
    num_cols = list(df.select_dtypes(include="number").columns)
    cat_cols = [c for c in df.columns if c not in num_cols]
    label_col = cat_cols[0] if cat_cols else df.columns[0]
    metrics = num_cols[:6] if num_cols else [df.columns[-1]]

    if chart_type == "table":
        return f"""
df = pd.read_csv(CSV_PATH).head(12)
fig, ax = plt.subplots(figsize=(max(7, len(df.columns)*1.4), max(2.8, len(df)*0.45 + 1.2)))
ax.axis('off')
tbl = ax.table(cellText=df.values, colLabels=df.columns, loc='center', cellLoc='center')
tbl.auto_set_font_size(False)
tbl.set_fontsize(9.5)
tbl.scale(1.0, 1.45)
for (r, c), cell in tbl.get_celld().items():
    cell.set_edgecolor('#CBD5E1')
    if r == 0:
        cell.set_facecolor('#1E3A8A')
        cell.set_text_props(color='white', weight='bold')
    elif r % 2 == 1:
        cell.set_facecolor('#F8FAFC')
plt.title({repr(title)}, fontsize=12, fontweight='bold', pad=12)
plt.savefig(OUT_PNG, dpi=200, bbox_inches='tight')
plt.close()
"""

    if chart_type == "line":
        return f"""
df = pd.read_csv(CSV_PATH)
x = np.arange(len(df))
fig, ax = plt.subplots(figsize=(8.2, 4.8))
markers = ['o', 's', '^', 'D', 'v', '*']
for idx, col in enumerate({repr(metrics)}):
    ax.plot(x, pd.to_numeric(df[col], errors='coerce'), marker=markers[idx % len(markers)], linewidth=2.1, label=str(col))
ax.set_xticks(x)
ax.set_xticklabels([str(v)[:18] for v in df[{repr(label_col)}]], rotation=20, ha='right')
ax.set_title({repr(title)}, fontsize=12.5, fontweight='bold')
ax.grid(True, linestyle='--', alpha=0.4)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.legend(frameon=False)
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=200, bbox_inches='tight')
plt.close()
"""

    if chart_type == "bar":
        return f"""
df = pd.read_csv(CSV_PATH).head(10)
x = np.arange(len(df))
metrics = {repr(metrics)}
w = 0.78 / max(1, len(metrics))
fig, ax = plt.subplots(figsize=(8.5, 4.8))
for idx, col in enumerate(metrics):
    vals = pd.to_numeric(df[col], errors='coerce').fillna(0)
    bars = ax.bar(x + (idx - (len(metrics)-1)/2)*w, vals, width=w, label=str(col), alpha=0.9)
    for b in bars:
        h = b.get_height()
        ax.text(b.get_x() + b.get_width()/2, h, f'{{h:.1f}}', ha='center', va='bottom', fontsize=8)
ax.set_xticks(x)
ax.set_xticklabels([str(v)[:18] for v in df[{repr(label_col)}]], rotation=20, ha='right')
ax.set_title({repr(title)}, fontsize=12.5, fontweight='bold')
ax.grid(axis='y', linestyle='--', alpha=0.35)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.legend(frameon=False)
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=200, bbox_inches='tight')
plt.close()
"""

    # 三维雷达图 (radar3d): 将多指标归一化后沿 3D 空间不同 z 层绘制多面体雷达包络与柱脊
    return f"""
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
df = pd.read_csv(CSV_PATH).head(6)
metrics = {repr(metrics if len(metrics) >= 3 else list(df.columns[1:]))}
if len(metrics) < 3:
    metrics = list(df.columns)[:3]
labels = [str(v)[:16] for v in df[{repr(label_col)}]]
vals_mat = df[metrics].apply(pd.to_numeric, errors='coerce').fillna(0).values.astype(float)
col_max = np.where(vals_mat.max(axis=0) > 0, vals_mat.max(axis=0), 1.0)
norm = vals_mat / col_max

N = len(metrics)
angles = np.linspace(0, 2*np.pi, N, endpoint=False)
fig = plt.figure(figsize=(8.6, 6.4))
ax = fig.add_subplot(111, projection='3d')
colors = plt.cm.tab10(np.linspace(0, 1, len(labels)))

# 绘制参考圆环网格（底层 z=0）
theta_ring = np.linspace(0, 2*np.pi, 80)
for rad in (0.33, 0.66, 1.0):
    ax.plot(rad*np.cos(theta_ring), rad*np.sin(theta_ring), 0, color='#CBD5E1', linestyle='--', linewidth=0.8)
for ang, mname in zip(angles, metrics):
    ax.plot([0, 1.05*np.cos(ang)], [0, 1.05*np.sin(ang)], [0, 0], color='#94A3B8', linewidth=0.9)
    ax.text(1.18*np.cos(ang), 1.18*np.sin(ang), 0, str(mname), ha='center', va='center', fontsize=9, fontweight='bold')

for row_idx, (lbl, row_vals) in enumerate(zip(labels, norm)):
    z_lvl = row_idx * 0.45
    xs = row_vals * np.cos(angles)
    ys = row_vals * np.sin(angles)
    zs = np.full(N, z_lvl)
    verts = [list(zip(xs, ys, zs))]
    poly = Poly3DCollection(verts, alpha=0.28, facecolor=colors[row_idx], edgecolor=colors[row_idx], linewidth=2.0)
    ax.add_collection3d(poly)
    ax.plot(np.append(xs, xs[0]), np.append(ys, ys[0]), np.full(N+1, z_lvl), color=colors[row_idx], linewidth=2.0, label=lbl)
    ax.scatter(xs, ys, zs, color=colors[row_idx], s=28)

ax.set_xlim(-1.25, 1.25)
ax.set_ylim(-1.25, 1.25)
ax.set_zlim(-0.1, max(0.8, len(labels)*0.45))
ax.set_zticks([i*0.45 for i in range(len(labels))])
ax.set_zticklabels(labels, fontsize=8)
ax.view_init(elev=28, azim=40)
ax.set_title({repr(title)}, fontsize=12.5, fontweight='bold', pad=14)
ax.legend(loc='upper left', fontsize=8, frameon=False)
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=200, bbox_inches='tight')
plt.close()
"""


def _df_to_latex(df: pd.DataFrame) -> str:
    """无额外依赖的 booktabs LaTeX 表格生成。"""
    sub = df.head(15)
    cols = [str(c).replace("_", "\\_") for c in sub.columns]
    align = "l" + "r" * (len(cols) - 1)
    lines = [
        "\\begin{table}[htbp]",
        "\\centering",
        f"\\begin{{tabular}}{{{align}}}",
        "\\toprule",
        " & ".join(cols) + " \\\\",
        "\\midrule",
    ]
    for _, row in sub.iterrows():
        cells = []
        for val in row.values:
            if isinstance(val, float):
                cells.append(f"{val:.3f}".rstrip("0").rstrip("."))
            else:
                cells.append(str(val).replace("_", "\\_"))
        lines.append(" & ".join(cells) + " \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}", "\\end{table}"])
    return "\n".join(lines)


def visualize_csv(
    csv_bytes: bytes,
    *,
    chart_type: str = "bar",
    title: str = "Experimental Results Comparison",
    instruction: str = "",
) -> dict:
    """解析 CSV，生成学术图表/表格，并提炼基于真实统计数据的 Figure Caption。"""
    if chart_type not in ("table", "line", "bar", "radar3d"):
        raise ValueError("chart_type 必须为 table / line / bar / radar3d")

    work_dir = _assets_dir()
    uid = uuid.uuid4().hex[:10]
    csv_path = work_dir / f"data_{uid}.csv"
    csv_path.write_bytes(csv_bytes)
    out_png = work_dir / f"chart_{uid}.png"

    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError("CSV 数据为空")

    code = _build_default_chart_code(df, chart_type, title)
    ok, msg = run_chart_script(code, work_dir=work_dir, csv_path=csv_path, out_png=out_png)
    if not ok:
        raise RuntimeError(f"图表渲染失败: {msg}")

    # 计算真实统计摘要供 LLM 提炼严谨学术图注（避免编造数字）
    summary_lines = [
        f"Rows={len(df)}, Columns={list(df.columns)}",
        f"Data Preview:\n{df.head(15).to_string(index=False)}",
    ]
    num_df = df.select_dtypes(include="number")
    if not num_df.empty:
        summary_lines.append(f"Summary Stats:\n{num_df.describe().round(3).to_string()}")

    cap_prompt = (
        "你是顶会学术论文图表撰稿人。请严格根据下方真实 CSV 数据统计结果，提炼符合学术规范的 Figure Caption（中英双语各一段）"
        "以及 2-3 条核心数据趋势洞察。禁止编造任何数据集中不存在的数值。\n"
        + (f"补充关注点：{instruction}\n" if instruction else "")
        + "\n".join(summary_lines)[:3500]
        + '\n\n返回 JSON：{"caption_en": "Figure X: ...", "caption_zh": "图 X：...", "insights": ["洞察1", "洞察2"]}'
    )
    try:
        cap = llm.chat_json(cap_prompt, model=settings.llm_model_fast, temperature=0.1)
    except Exception:  # noqa: BLE001
        cap = {"caption_en": f"Figure: {title}.", "caption_zh": f"图：{title}。", "insights": []}

    # 同时输出 HTML 表格与 LaTeX 表格片段，方便用户直接粘进论文
    latex_table = _df_to_latex(df)
    html_table = df.head(20).to_html(index=False, classes="scholar-csv-table", border=0)

    return {
        "chart_type": chart_type,
        "image_url": f"/api/copilot/assets/{out_png.name}",
        "code": code.strip(),
        "caption_en": cap.get("caption_en", ""),
        "caption_zh": cap.get("caption_zh", ""),
        "insights": cap.get("insights") or [],
        "latex_table": latex_table,
        "html_table": html_table,
        "columns": list(df.columns),
        "row_count": len(df),
    }


# ----------------------------------------------------------------------------
# 4. 学术中译英（一键译为符合顶会规范的学术英文）
# ----------------------------------------------------------------------------
def academic_translate(text: str, *, style: str = "IEEE / NeurIPS") -> dict:
    txt = text.strip()
    if not txt:
        raise ValueError("待翻译内容不能为空")
    prompt = (
        f"你是资深科研论文润色编辑（擅长 {style} 顶会期刊英文风格）。\n"
        "请将以下中文论文摘要或正文段落精准翻译为地道、严谨、简洁的学术英文：\n"
        "1. 保留所有 LaTeX 公式（$...$ / $$...$$）与技术符号不变；\n"
        "2. 避免中式英语直译，使用恰当的学术动词与被动/客观测评表达；\n"
        "3. 附带 3-5 个关键学术表达的润色说明。\n\n"
        f"中文原文：\n{txt[:4500]}\n\n"
        '返回 JSON：{"english": "完整英文译文", "notes": ["表达优化说明1", "说明2"]}'
    )
    res = llm.chat_json(prompt, model=settings.llm_model_strong, temperature=0.2)
    return {
        "source": txt,
        "english": res.get("english", ""),
        "notes": res.get("notes") or [],
    }
