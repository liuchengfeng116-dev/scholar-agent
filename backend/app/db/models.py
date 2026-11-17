"""ORM 数据模型。

每个可展示的解析元素（章节、块、参考文献、分块）都保存 page + bbox，
作为前端“原文定位锚点”，是全系统 Source Tracing 的基础。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / reviewer / admin
    # 研究兴趣画像：{"keywords": [...], "categories": [...]}
    interests: Mapped[dict] = mapped_column(JSON, default=dict)


class Paper(TimestampMixin, Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)  # [{"name","affiliations":[...],"email"}]
    affiliations: Mapped[list] = mapped_column(JSON, default=list)
    abstract: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    arxiv_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    version: Mapped[str] = mapped_column(String(8), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pub_date: Mapped[str] = mapped_column(String(32), default="")
    venue: Mapped[str] = mapped_column(String(256), default="")
    doi: Mapped[str] = mapped_column(String(128), default="")
    github_url: Mapped[str] = mapped_column(String(256), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")

    filename: Mapped[str] = mapped_column(String(256), default="")
    file_path: Mapped[str] = mapped_column(String(512), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    has_text_layer: Mapped[bool] = mapped_column(default=True)
    doc_type: Mapped[str] = mapped_column(String(16), default="paper")  # paper / book
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload / arxiv / fetch

    # uploaded -> parsing -> ready / failed ; duplicate_pending 等待用户去重决策
    status: Mapped[str] = mapped_column(String(24), default="uploaded")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    status_msg: Mapped[str] = mapped_column(String(512), default="")
    # 文本指纹（MinHash 签名）与摘要向量，用于语义去重
    fingerprint: Mapped[list] = mapped_column(JSON, default=list)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)  # 解析统计：表格/公式/图片数量等

    sections: Mapped[list["Section"]] = relationship(cascade="all, delete-orphan")
    references: Mapped[list["Reference"]] = relationship(cascade="all, delete-orphan")


class DuplicateRecord(TimestampMixin, Base):
    """语义去重记录：new_paper 与 existing_paper 内容高度重合。"""

    __tablename__ = "duplicate_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    new_paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    existing_paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    text_similarity: Mapped[float] = mapped_column(Float, default=0.0)  # MinHash Jaccard
    semantic_similarity: Mapped[float] = mapped_column(Float, default=0.0)  # 向量余弦
    suggestion: Mapped[str] = mapped_column(String(16), default="keep_existing")
    suggestion_reason: Mapped[str] = mapped_column(Text, default="")
    diff_summary: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(16), default="pending")  # pending/overwrite/keep_both/discard_new


class Section(Base):
    """标题树节点（一/二/三级）。"""

    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    number: Mapped[str] = mapped_column(String(16), default="")  # 如 "3.2.1"
    title: Mapped[str] = mapped_column(String(512))
    page: Mapped[int] = mapped_column(Integer, default=1)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    order: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="body")  # body / references / appendix


class Block(Base):
    """细粒度内容块：文本段落、表格、图片、公式、图注等。"""

    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(16))  # text/table/figure/formula/caption/title/abstract
    page: Mapped[int] = mapped_column(Integer)
    bbox: Mapped[list] = mapped_column(JSON, default=list)  # [x0,y0,x1,y1] PDF 坐标
    order: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    latex: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str] = mapped_column(String(512), default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class Reference(Base):
    """参考文献条目。"""

    __tablename__ = "references"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)  # 编号 [n]
    raw_text: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(512), default="")
    authors: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(256), default="")
    doi: Mapped[str] = mapped_column(String(128), default="")
    arxiv_id: Mapped[str] = mapped_column(String(32), default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    bbox: Mapped[list] = mapped_column(JSON, default=list)
    # 正文中引用该条目的位置：[{"page","bbox","context"}]
    cite_contexts: Mapped[list] = mapped_column(JSON, default=list)
    matched_paper_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)  # 引用含金量评估


class Chunk(Base):
    """RAG 检索分块（向量存 Chroma，id 与此表一致）。"""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    section_path: Mapped[str] = mapped_column(String(512), default="")
    type: Mapped[str] = mapped_column(String(16), default="text")
    page: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    anchors: Mapped[list] = mapped_column(JSON, default=list)  # [{"page","bbox"}]
    text: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)


class Translation(Base):
    __tablename__ = "translations"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    block_id: Mapped[int] = mapped_column(Integer, index=True)
    lang: Mapped[str] = mapped_column(String(8), default="zh")
    text: Mapped[str] = mapped_column(Text)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(256), default="新对话")
    paper_ids: Mapped[list] = mapped_column(JSON, default=list)  # 对话限定的文献范围
    summary: Mapped[str] = mapped_column(Text, default="")  # 短期记忆压缩摘要


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    trace: Mapped[list] = mapped_column(JSON, default=list)  # Agent 执行轨迹


class Memory(TimestampMixin, Base):
    """长期记忆：用户研究方向、偏好、关键事实。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # topic / preference / fact
    content: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    arxiv_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512))
    authors: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[str] = mapped_column(String(128), default="")
    published: Mapped[str] = mapped_column(String(32), default="")
    github_url: Mapped[str] = mapped_column(String(256), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[int] = mapped_column(Integer, default=0)  # 1 赞 / -1 踩 / 0 无


class ArxivPaper(Base):
    """Sickle/OAI-PMH 抓取到的 arXiv 元数据缓存（全体用户共享）。"""

    __tablename__ = "arxiv_papers"

    arxiv_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    authors: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[str] = mapped_column(String(128), default="")
    comments: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[str] = mapped_column(String(32), default="", index=True)
    github_url: Mapped[str] = mapped_column(String(256), default="")


class CitationGraph(TimestampMixin, Base):
    __tablename__ = "citation_graphs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    paper_ids: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # nodes / edges / metrics
    survey: Mapped[str] = mapped_column(Text, default="")
    future_work: Mapped[dict] = mapped_column(JSON, default=dict)


class Task(TimestampMixin, Base):
    """后台长任务（解析、翻译、批量下载、图谱构建）。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(512), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
