"""模块五核心服务：局域引用拓扑图谱构建、图论基石/边缘节点挖掘、自动综述与 Future Work 前沿去伪。"""

from __future__ import annotations

import logging
import re

import networkx as nx
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Block, CitationGraph, Paper, Reference, Section
from app.llm import client as llm
from app.scout import service as scout_svc

log = logging.getLogger(__name__)


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (s or "").lower()).strip()


def build_citation_graph(
    db: Session,
    user_id: int,
    *,
    paper_ids: list[int] | None = None,
    name: str = "局域引用图谱",
) -> CitationGraph:
    """解析批量文献之间的引用关系，用图论算法计算中心性与基石/边缘角色。"""
    pq = db.query(Paper).filter(Paper.owner_id == user_id, Paper.status == "ready")
    if paper_ids:
        pq = pq.filter(Paper.id.in_(paper_ids))
    papers = pq.order_by(Paper.year.asc(), Paper.id.asc()).all()
    if not papers:
        raise ValueError("没有可用于构建图谱的已解析论文")

    # 建索引方便快速匹配：arxiv_id / doi / 规范化标题
    by_arxiv = {p.arxiv_id: p.id for p in papers if p.arxiv_id}
    by_doi = {p.doi.lower(): p.id for p in papers if p.doi}
    norm_titles = {p.id: _norm_title(p.title) for p in papers if p.title}

    G = nx.DiGraph()
    for p in papers:
        G.add_node(
            p.id,
            title=p.title or p.filename,
            year=p.year or 0,
            venue=p.venue or "",
            arxiv_id=p.arxiv_id or "",
        )

    edges: list[dict] = []
    seen_edges: set[tuple[int, int]] = set()
    ref_records = db.query(Reference).filter(Reference.paper_id.in_([p.id for p in papers])).all()

    for ref in ref_records:
        src_id = ref.paper_id
        src_year = next((p.year for p in papers if p.id == src_id), None)
        dst_id: int | None = None
        if ref.arxiv_id and ref.arxiv_id in by_arxiv:
            dst_id = by_arxiv[ref.arxiv_id]
        elif ref.doi and ref.doi.lower() in by_doi:
            dst_id = by_doi[ref.doi.lower()]
        else:
            rt = _norm_title(ref.title)
            raw_norm = _norm_title(ref.raw_text)
            for cand in papers:
                if cand.id == src_id:
                    continue
                # 1) 明确指向不同的 arXiv 编号或不可能的时间倒流（早期参考文献不可能引用多年后的新论文），直接跳过
                if ref.arxiv_id and cand.arxiv_id and ref.arxiv_id != cand.arxiv_id:
                    continue
                if ref.year and cand.year and ref.year < cand.year - 1:
                    continue
                if src_year and cand.year and cand.year > src_year + 1:
                    continue

                ct = norm_titles.get(cand.id, "")
                if len(ct) < 12:
                    continue
                # 2) 原文参考文献中包含完整目标论文标题（最可靠，兼容 authors/title 断句差异）
                if len(ct) >= 18 and f" {ct} " in f" {raw_norm} ":
                    dst_id = cand.id
                    break
                # 3) 对提取后的 ref.title 做词序相似度匹配，并要求长度接近，杜绝短标题如 "Layer normalization" 误命中长标题
                if len(rt) >= 12 and min(len(ct), len(rt)) / max(len(ct), len(rt)) >= 0.6:
                    if fuzz.token_sort_ratio(ct, rt) >= 84:
                        dst_id = cand.id
                        break
        if dst_id and dst_id != src_id and (src_id, dst_id) not in seen_edges:
            seen_edges.add((src_id, dst_id))
            G.add_edge(src_id, dst_id, kind="cites")
            edges.append({"source": src_id, "target": dst_id, "kind": "cites"})

    # 图论指标：PageRank、入度、出度、介数中心性、社区划分
    try:
        pr = nx.pagerank(G, alpha=0.85) if len(G.nodes) > 0 else {}
    except Exception:  # noqa: BLE001
        pr = {n: 1.0 / max(1, len(G.nodes)) for n in G.nodes}
    btw = nx.betweenness_centrality(G) if len(G.nodes) >= 3 else {n: 0.0 for n in G.nodes}

    ug = G.to_undirected()
    comm_map: dict[int, int] = {n: 0 for n in G.nodes}
    try:
        if ug.number_of_edges() > 0:
            comms = list(nx.community.greedy_modularity_communities(ug))
            for cid, comp in enumerate(comms):
                for n in comp:
                    comm_map[n] = cid
    except Exception:  # noqa: BLE001
        pass

    # 识别基石论文（高 PageRank / 高被本批文献引用）与边缘衍生论文（仅引用他人或孤立末梢）
    pr_sorted = sorted(pr.values(), reverse=True)
    pr_cutoff = pr_sorted[min(len(pr_sorted) - 1, max(0, len(pr_sorted) // 4))] if pr_sorted else 0.0

    nodes = []
    keystone_ids: list[int] = []
    derivative_ids: list[int] = []
    for p in papers:
        ind = G.in_degree(p.id)
        outd = G.out_degree(p.id)
        raw_pr = pr.get(p.id, 0.0)
        p_rank = round(raw_pr, 4)
        b_cent = round(btw.get(p.id, 0.0), 4)

        if ind >= 2 or (ind >= 1 and raw_pr + 1e-9 >= pr_cutoff):
            role = "keystone"
            keystone_ids.append(p.id)
        elif b_cent > 0.05:
            role = "bridge"
        elif outd >= 1 and ind == 0:
            role = "derivative"
            derivative_ids.append(p.id)
        else:
            role = "peripheral"
            derivative_ids.append(p.id)

        nodes.append(
            {
                "id": p.id,
                "title": p.title or p.filename,
                "year": p.year,
                "venue": p.venue,
                "arxiv_id": p.arxiv_id,
                "in_degree": ind,
                "out_degree": outd,
                "pagerank": p_rank,
                "betweenness": b_cent,
                "community": comm_map.get(p.id, 0),
                "role": role,
            }
        )

    data = {
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "density": round(nx.density(G), 4) if len(nodes) > 1 else 0.0,
            "keystone_ids": keystone_ids,
            "derivative_ids": derivative_ids,
        },
    }

    graph = CitationGraph(
        user_id=user_id,
        name=name,
        paper_ids=[p.id for p in papers],
        data=data,
    )
    db.add(graph)
    db.commit()
    db.refresh(graph)
    return graph


def generate_survey(db: Session, graph: CitationGraph) -> str:
    """基于引用拓扑图谱与基石/边缘节点脉络生成有引用出处的简短领域综述。"""
    papers = db.query(Paper).filter(Paper.id.in_(graph.paper_ids)).order_by(Paper.year.asc()).all()
    nodes_by_id = {n["id"]: n for n in (graph.data.get("nodes") or [])}
    edges = graph.data.get("edges") or []
    paper_titles = {p.id: (p.title or p.filename) for p in papers}

    paper_briefs = []
    for p in papers[:25]:
        n = nodes_by_id.get(p.id, {})
        role = n.get("role", "paper")
        paper_briefs.append(
            f"[P{p.id}] ({p.year or '?'}, 角色={role}, 入度={n.get('in_degree', 0)}, PR={n.get('pagerank', 0)}) "
            f"《{p.title or p.filename}》\n摘要: {(p.abstract or '')[:350]}"
        )
    edge_lines = [
        f"- 《{paper_titles.get(e['source'], e['source'])}》 -> 引用 -> 《{paper_titles.get(e['target'], e['target'])}》"
        for e in edges[:40]
    ]

    prompt = (
        "你是学术综述专家。请严格基于以下局部文献集合及引用拓扑关系，撰写一篇结构严谨、简洁有力的领域综述（600-900字）。\n"
        "要求：\n"
        "1. 清晰梳理演进脉络：先阐明图论算法挖掘出的「基石论文（keystone）」的核心贡献，再说明后续「桥梁/衍生论文（bridge/derivative）」的扩展路线；\n"
        "2. 每一条事实或观点后面必须标注来源 `[P论文ID]`（如 `[P1]`），禁止编造列表中不存在的论文或指标；\n"
        "3. 分三段：研究主线与基石突破、技术演进与方法对比、当前局限。\n\n"
        f"文献列表：\n{chr(10).join(paper_briefs)}\n\n"
        f"引用边关系：\n{chr(10).join(edge_lines) or '（当前批内直接引用边较少，按年份与摘要演进梳理）'}"
    )
    survey = llm.complete(prompt, model=settings.llm_model_strong, temperature=0.2)
    graph.survey = survey
    db.commit()
    return survey


def normalize_future_work(raw: dict | None) -> dict:
    """规范化 LLM 返回的 Future Work JSON，保证列表与 supporting_papers 字段类型一致。"""
    data = raw if isinstance(raw, dict) else {}
    resolved = data.get("resolved_ideas")
    resolved_list = [x for x in resolved if isinstance(x, dict)] if isinstance(resolved, list) else []

    open_dirs = data.get("open_directions")
    normalized_open: list[dict] = []
    if isinstance(open_dirs, list):
        for item in open_dirs:
            if not isinstance(item, dict):
                continue
            sp = item.get("supporting_papers")
            if isinstance(sp, list):
                sp_list = [str(s).strip() for s in sp if str(s).strip()]
            elif isinstance(sp, str) and sp.strip():
                sp_list = [sp.strip()]
            else:
                sp_list = []
            normalized_open.append({**item, "supporting_papers": sp_list})

    return {"resolved_ideas": resolved_list, "open_directions": normalized_open}


def explore_future_work(db: Session, graph: CitationGraph) -> dict:
    """提取多篇论文 Future Work，结合时间线与后续进展自动排除已解决的旧 idea，提炼高价值方向。"""
    papers = db.query(Paper).filter(Paper.id.in_(graph.paper_ids)).order_by(Paper.year.asc()).all()

    fw_extracts: list[dict] = []
    for p in papers[:20]:
        # 提取 Conclusion / Future Work / Limitation 相关段落
        secs = db.query(Section).filter(Section.paper_id == p.id).all()
        target_sec_ids = [
            s.id
            for s in secs
            if re.search(r"(conclusion|future|limitation|discussion)", s.title, re.I)
        ]
        bq = db.query(Block).filter(Block.paper_id == p.id, Block.type == "text")
        if target_sec_ids:
            blks = bq.filter(Block.section_id.in_(target_sec_ids)).order_by(Block.order.asc()).limit(8).all()
        else:
            # 兜底取文末最后几个正文块
            blks = bq.order_by(Block.order.desc()).limit(6).all()
            blks = list(reversed(blks))

        snippet = "\n".join(b.content for b in blks)[:1200]
        if not snippet.strip():
            snippet = (p.abstract or "")[:500]
        fw_extracts.append(
            {
                "paper_id": p.id,
                "title": p.title or p.filename,
                "year": p.year or 0,
                "excerpt": snippet,
            }
        )

    # 补充近期外部前沿进展作为排除已解决旧 idea 的证据（取库中较晚论文标题 + 搜索前沿）
    later_papers = [
        f"({p.year or '?'}) {p.title}: {(p.abstract or '')[:200]}"
        for p in sorted(papers, key=lambda x: x.year or 0, reverse=True)[:8]
    ]
    if papers:
        topic_seed = (papers[-1].title or "large language model")[:60]
        try:
            ext = scout_svc.search_external_papers(topic_seed, limit=5)
            for h in ext:
                later_papers.append(f"({h.get('year') or '?'}) {h['title']}: {(h.get('abstract') or '')[:180]}")
        except Exception:  # noqa: BLE001
            pass

    fw_text = "\n\n".join(
        f"[P{x['paper_id']} | {x['year'] or '?'}] 《{x['title']}》\n结尾/FutureWork摘录：{x['excerpt'][:650]}"
        for x in fw_extracts
    )
    later_text = "\n".join(later_papers[:14])

    prompt = (
        "你是前沿科研趋势分析专家。下面给出了多篇论文按时间线排列的结尾与 Future Work 摘录，以及后续时间线的代表性进展。\n"
        "请完成两件事：\n"
        "1. 从早期或各篇论文中识别出已经被后续工作解决/攻克的旧 Future Work（`resolved_ideas`），必须明确指出是哪篇后续论文（及年份）在何种程度上解决了它；\n"
        "2. 剔除已解决的旧方向后，提炼出 3-5 个至今仍未被充分解决、真正有探索价值的前沿方向（`open_directions`），给出切入思路与出处依据。\n\n"
        f"论文 Future Work 摘录：\n{fw_text[:7500]}\n\n"
        f"后续时间线与最新进展参考：\n{later_text[:3000]}\n\n"
        "严格返回 JSON：\n"
        "{\n"
        '  "resolved_ideas": [{"idea": "旧想法", "origin_paper": "提出论文与年份", "resolved_by": "后续解决论文与依据"}],\n'
        '  "open_directions": [{"direction": "前沿方向标题", "rationale": "为什么仍有价值/核心切入点", "supporting_papers": ["P1", "P2"]}]\n'
        "}"
    )
    try:
        raw_res = llm.chat_json(prompt, model=settings.llm_model_strong, temperature=0.2)
    except Exception:  # noqa: BLE001
        raw_res = {}

    res = normalize_future_work(raw_res)
    graph.future_work = res
    db.commit()
    return res
