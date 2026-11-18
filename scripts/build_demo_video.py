"""生成传智杯参赛作品《ScholarAgent 智研助手》完整实机演示视频（1080p MP4，配普通话语音与同步字幕）。

输出文件：
    docs/ScholarAgent_演示视频.mp4

规格验证：
    - 分辨率：1920 × 1080 (1080p Full HD)
    - 帧率：20 fps，编码 H.264 (yuv420p) + AAC (192kbps)
    - 时长：严格控制在 5–8 分钟（目标约 6 分 15 秒）
    - 内容：完整覆盖第一幕至第六幕（项目介绍、架构亮点、模块一至模块六真实界面交互演示）
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = ROOT / "data" / "video_build"
FRAMES_DIR = WORK_DIR / "shots"
AUDIO_DIR = WORK_DIR / "audio_v2"
OUT_VIDEO = ROOT / "docs" / "ScholarAgent_演示视频.mp4"
BASE_URL = os.environ.get("SCHOLAR_BASE_URL", "http://127.0.0.1:5173")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

W, H = 1920, 1080
FPS = 20
VOICE = "zh-CN-YunxiNeural"
VOICE_RATE = "+16%"


@dataclass
class SegmentSpec:
    idx: int
    act_num: int
    act_title: str
    title: str
    narration: str
    cursor_a: tuple[int, int] = (960, 540)
    cursor_b: tuple[int, int] = (960, 540)
    box_a: tuple[int, int, int, int] | None = None
    box_b: tuple[int, int, int, int] | None = None
    overlay_arch: bool = False
    overlay_finale: bool = False
    shot_a: Path | None = None
    shot_b: Path | None = None
    audio_path: Path | None = None
    padded_audio_path: Path | None = None
    speech_dur: float = 0.0
    total_dur: float = 0.0
    clauses: list[tuple[str, float, float]] = field(default_factory=list)


SEGMENTS: list[SegmentSpec] = [
    # ==================== 第一幕：项目介绍与架构亮点 ====================
    SegmentSpec(
        idx=1,
        act_num=1,
        act_title="第一幕 · 项目介绍与全栈架构亮点",
        title="参赛作品介绍与登录工作台",
        narration="各位评委老师好！我是来自重庆科技大学的李嘉成。今天为您演示我独立开发的参赛作品：ScholarAgent 智研全流程科研助手。",
        cursor_a=(960, 460),
        cursor_b=(960, 565),
        box_a=(680, 250, 560, 430),
        box_b=(680, 250, 560, 430),
    ),
    SegmentSpec(
        idx=2,
        act_num=1,
        act_title="第一幕 · 项目介绍与全栈架构亮点",
        title="解决科研文献过载与大模型幻觉痛点",
        narration="针对海量文献过载、跨语言阅读困难以及通用大模型严重的幻觉痛点，本系统突破简单 API 套壳模式，构建了白盒化可溯源的科研多智能体工作台。",
        cursor_a=(420, 32),
        cursor_b=(1550, 32),
        box_a=(240, 6, 1660, 54),
        box_b=(255, 72, 1640, 120),
    ),
    SegmentSpec(
        idx=3,
        act_num=1,
        act_title="第一幕 · 项目介绍与全栈架构亮点",
        title="六大核心 Agent 能力与分层架构",
        narration="底层架构深度集成了 Task Planning 任务规划、短长期混合记忆、11 项受控工具调用、多智能体协同，以及像素级源头溯源与安全护栏。",
        cursor_a=(960, 520),
        cursor_b=(1180, 560),
        box_a=(280, 110, 1560, 860),
        box_b=(280, 110, 1560, 860),
        overlay_arch=True,
    ),
    SegmentSpec(
        idx=4,
        act_num=1,
        act_title="第一幕 · 项目介绍与全栈架构亮点",
        title="六大核心业务模块全景导览",
        narration="围绕科研全生命周期，左侧导航栏依次提供文献解析、双语阅读、抗幻觉问答、检索推荐、引用图谱与写作 Copilot 六大核心模块。下面进入实机演示。",
        cursor_a=(115, 160),
        cursor_b=(115, 380),
        box_a=(8, 65, 232, 420),
        box_b=(8, 65, 232, 420),
    ),
    # ==================== 第二幕：模块一 · 文献管理、语义查重与多模态解析 ====================
    SegmentSpec(
        idx=5,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="多排版风格真实文献库与百页教材",
        narration="首先是模块一：文献管理与多模态解析。知识库已导入 IEEE、ACM、Springer、Nature 等多种排版风格论文，以及 Transformer 最新 v7 版和 541 页深度学习教材。",
        cursor_a=(680, 420),
        cursor_b=(820, 680),
        box_a=(255, 270, 1640, 680),
        box_b=(255, 270, 1640, 680),
    ),
    SegmentSpec(
        idx=6,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="上传 v1 旧版本触发内容级语义查重预警",
        narration="现在我们上传较旧的 v1 版 PDF。系统并非比对文件名，而是通过 5-gram MinHash 指纹与向量余弦相似度，瞬间检测出正文高度重合，主动弹出查重预警。",
        cursor_a=(1050, 155),
        cursor_b=(880, 320),
        box_a=(255, 80, 1640, 150),
        box_b=(440, 140, 1040, 740),
    ),
    SegmentSpec(
        idx=7,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="智能版本演进建议与修订差异自动总结",
        narration="弹窗智能识别出库中 v7 比 v1 更新、建议保留现有版本，并由 Agent 自动总结出两版的核心修订差异，如英法翻译 BLEU 分数由 41.0 修正为 41.8。",
        cursor_a=(920, 560),
        cursor_b=(540, 910),
        box_a=(460, 360, 1000, 360),
        box_b=(440, 880, 420, 60),
    ),
    SegmentSpec(
        idx=8,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="双栏版面恢复、元数据提取与三级章节树",
        narration="点开结构化详情抽屉，系统通过双栏坐标聚类与阅读顺序恢复，精准提取标题、作者机构、年份与摘要，并完整重建一至三级章节目录树。",
        cursor_a=(1610, 390),
        cursor_b=(1380, 480),
        box_a=(1020, 60, 890, 980),
        box_b=(1040, 290, 850, 680),
    ),
    SegmentSpec(
        idx=9,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="结构化表格还原与 LaTeX 数学公式渲染",
        narration="切换到表格与公式页，论文中的复杂三线表被无损还原为 Markdown 表格，数学公式则结合视觉多模态模型自动转化为标准 LaTeX 表达式并实时渲染。",
        cursor_a=(1220, 125),
        cursor_b=(1310, 125),
        box_a=(1030, 100, 870, 880),
        box_b=(1030, 100, 870, 880),
    ),
    SegmentSpec(
        idx=10,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="高清模型插图裁剪与参考文献结构化解析",
        narration="在图表与参考文献页，系统自动裁剪出高清模型架构插图与图注，并将文末 40 条参考文献全部结构化拆解，建立起正文引用语境的双向索引。",
        cursor_a=(1410, 125),
        cursor_b=(1520, 125),
        box_a=(1030, 100, 870, 880),
        box_b=(1030, 100, 870, 880),
    ),
    SegmentSpec(
        idx=11,
        act_num=2,
        act_title="第二幕 · 模块一：语义去重、版本比对与多模态解析",
        title="扫描件多模态 OCR 与 541 页长篇教材分批解析",
        narration="面对无文本层的扫描件，系统自动调用视觉多模态模型逐页 OCR 识别；面对 541 页的深度学习教材，则通过流式分批解析稳定抽取出 224 个章节与 981 个公式。",
        cursor_a=(1610, 460),
        cursor_b=(1420, 520),
        box_a=(1020, 60, 890, 980),
        box_b=(1040, 260, 850, 720),
    ),
    # ==================== 第三幕：模块二 · 抗幻觉知识问答、源头溯源与多文推理 ====================
    SegmentSpec(
        idx=12,
        act_num=3,
        act_title="第三幕 · 模块二：抗幻觉知识问答、像素级溯源与多文推理",
        title="多维度细节事实问答与混合检索架构",
        narration="接下来是模块二：抗幻觉知识问答。当我们针对单篇论文同时询问作者机构、Adam 优化器超参数、训练 GPU 型号数量与 BLEU 分数时，系统启动多路混合检索。",
        cursor_a=(115, 180),
        cursor_b=(880, 320),
        box_a=(250, 70, 300, 960),
        box_b=(560, 70, 880, 960),
    ),
    SegmentSpec(
        idx=13,
        act_num=3,
        act_title="第三幕 · 模块二：抗幻觉知识问答、像素级溯源与多文推理",
        title="白盒化 Multi-Agent 协同执行轨迹面板",
        narration="右侧执行轨迹面板将内部推理全过程白盒化：先加载短长期记忆，由 Task Planner 拆解子查询，再通过 Function Calling 调用混合检索，最后由 Critic 核查事实一致性。",
        cursor_a=(1660, 260),
        cursor_b=(1660, 560),
        box_a=(1450, 70, 455, 960),
        box_b=(1450, 70, 455, 960),
    ),
    SegmentSpec(
        idx=14,
        act_num=3,
        act_title="第三幕 · 模块二：抗幻觉知识问答、像素级溯源与多文推理",
        title="精准数值回答与可点击原文引用锚点",
        narration="回答精准给出了 beta1 等于 0.9、beta2 等于 0.98、8 张 P100 GPU 以及 28.4 和 41.8 的 BLEU 分数，每个结论后都附带可点击的蓝色原文锚点。",
        cursor_a=(920, 420),
        cursor_b=(880, 780),
        box_a=(575, 220, 850, 460),
        box_b=(575, 680, 850, 240),
    ),
    SegmentSpec(
        idx=15,
        act_num=3,
        act_title="第三幕 · 模块二：抗幻觉知识问答、像素级溯源与多文推理",
        title="点击引用锚点跨模块直达 PDF 页码并像素级高亮",
        narration="点击回答中的蓝色锚点 E2，画面立即跨模块跳转到阅读器第 7 页，并在原生双栏 PDF 上用黄色高亮框精确定位出 Optimizer 原文段落，实现像素级溯源。",
        cursor_a=(720, 740),
        cursor_b=(760, 820),
        box_a=(580, 680, 420, 120),
        box_b=(595, 785, 515, 70),
    ),
    SegmentSpec(
        idx=16,
        act_num=3,
        act_title="第三幕 · 模块二：抗幻觉知识问答、像素级溯源与多文推理",
        title="跨文献联合推理与演进脉络对比表格",
        narration="在跨文献联合推理中，要求对比 BERT、GPT-1 至 GPT-3 与原始 Transformer 的架构演进、参数规模及 LayerNorm 位置调整时，系统执行多文均衡检索并输出结构化对比表。",
        cursor_a=(180, 155),
        cursor_b=(960, 480),
        box_a=(115, 110, 225, 110),
        box_b=(560, 250, 990, 640),
    ),
    SegmentSpec(
        idx=17,
        act_num=3,
        act_title="第三幕 · 模块二：抗幻觉知识问答、像素级溯源与多文推理",
        title="Guardrails 抗幻觉拒答机制与 100% 评测准确率",
        narration="当提问涉及文献未提及的虚假概念如动态稀疏量子路由时，Guardrails 护栏果断触发拒答声明，绝不编造参数，在 11 道官方基准评测题上取得百分之百准确率。",
        cursor_a=(180, 120),
        cursor_b=(960, 420),
        box_a=(115, 100, 225, 100),
        box_b=(560, 180, 990, 420),
    ),
    # ==================== 第四幕：模块三 · 版式感知阅读、双向引用跳转与中英同步滚动 ====================
    SegmentSpec(
        idx=18,
        act_num=4,
        act_title="第四幕 · 模块三：版式感知阅读、双向引用与同步双语翻译",
        title="三级标题树导航与原生 PDF.js 高保真渲染",
        narration="模块三是版式感知阅读与双向联动翻译。左侧提供一至三级章节树快速跳页，中间基于 PDF.js 原生高保真渲染双栏版式、数学公式与模型架构图。",
        cursor_a=(180, 255),
        cursor_b=(760, 480),
        box_a=(120, 120, 220, 880),
        box_b=(440, 120, 740, 880),
    ),
    SegmentSpec(
        idx=19,
        act_num=4,
        act_title="第四幕 · 模块三：版式感知阅读、双向引用与同步双语翻译",
        title="鼠标划词即时学术翻译与专业术语表对齐",
        narration="阅读过程中，鼠标划选任意英文段落并点击顶部划词翻译按钮，即可呼出即时学术译文卡片，内置领域术语表自动保证专业术语全篇统一。",
        cursor_a=(730, 82),
        cursor_b=(960, 165),
        box_a=(675, 65, 220, 38),
        box_b=(120, 115, 1780, 95),
    ),
    SegmentSpec(
        idx=20,
        act_num=4,
        act_title="第四幕 · 模块三：版式感知阅读、双向引用与同步双语翻译",
        title="中英双栏对照自动同步滚动与译文导出",
        narration="右侧双栏支持左原文、右译文按段落比例自动同步滚动，所有数学公式与代码严格保留英文原貌，并支持一键导出 Markdown 或双语对照网页。",
        cursor_a=(1560, 520),
        cursor_b=(1820, 82),
        box_a=(1240, 180, 660, 820),
        box_b=(1680, 62, 220, 42),
    ),
    SegmentSpec(
        idx=21,
        act_num=4,
        act_title="第四幕 · 模块三：版式感知阅读、双向引用与同步双语翻译",
        title="点击正文引用标记直达文末参考文献条目",
        narration="同时，正文中的蓝色引用标记与文末参考文献建立了双向锚点：点击引用标记或切换到双向引用面板，即可直达对应的文末参考文献完整条目。",
        cursor_a=(1480, 140),
        cursor_b=(1560, 360),
        box_a=(1390, 120, 180, 42),
        box_b=(1240, 175, 660, 820),
    ),
    SegmentSpec(
        idx=22,
        act_num=4,
        act_title="第四幕 · 模块三：版式感知阅读、双向引用与同步双语翻译",
        title="点击参考文献一键反向跳回正文引用位置",
        narration="反过来，点击任意参考文献条目下方的绿色『跳回正文引用处』标签，阅读器又能瞬间反向跳回正文对应页码并高亮引用段落，实现无损双向往返。",
        cursor_a=(1480, 310),
        cursor_b=(760, 480),
        box_a=(1250, 220, 640, 240),
        box_b=(440, 120, 740, 880),
    ),
    # ==================== 第五幕：模块四 & 模块五 · arXiv 追踪推荐、含金量下钻与引用图谱 ====================
    SegmentSpec(
        idx=23,
        act_num=5,
        act_title="第五幕 · 模块四与模块五：引用含金量下钻与引用拓扑图谱",
        title="Sickle 协议 arXiv 动态追踪与开源代码易复现加权推荐",
        narration="进入模块四：检索下钻与推荐。系统通过 Sickle 协议追踪 arXiv 最新论文，融合本地向量相似度与协同过滤，并对含开源 GitHub 代码的易复现论文优先加权推荐。",
        cursor_a=(55, 220),
        cursor_b=(1620, 340),
        box_a=(120, 140, 1780, 860),
        box_b=(135, 230, 1750, 260),
    ),
    SegmentSpec(
        idx=24,
        act_num=5,
        act_title="第五幕 · 模块四与模块五：引用含金量下钻与引用拓扑图谱",
        title="参考文献引用含金量评估与应付式引用识别",
        narration="切换到智能下钻与引用含金量筛选页，点击评估含金量。系统根据正文引用频次、章节位置与语境语义，自动区分绿色的核心基石引用与带删除线的应付式引用。",
        cursor_a=(280, 82),
        cursor_b=(805, 240),
        box_a=(120, 210, 1780, 780),
        box_b=(135, 270, 1750, 680),
    ),
    SegmentSpec(
        idx=25,
        act_num=5,
        act_title="第五幕 · 模块四与模块五：引用含金量下钻与引用拓扑图谱",
        title="自动剔除应付式引用并批量下钻抓取核心文献",
        narration="开启剔除应付式引用开关并点击下钻下载，系统会自动过滤背景凑数文献，仅抓取高含金量核心文献入库；上方还支持按标题或 arXiv ID 全网检索一键入库。",
        cursor_a=(895, 240),
        cursor_b=(945, 240),
        box_a=(630, 215, 760, 52),
        box_b=(120, 120, 1780, 90),
    ),
    SegmentSpec(
        idx=26,
        act_num=5,
        act_title="第五幕 · 模块四与模块五：引用含金量下钻与引用拓扑图谱",
        title="模块五：局域引用拓扑图谱构建与 PageRank 基石论文挖掘",
        narration="切换到模块五：引用图谱与综述。系统自动解析批量文献间的引用关系，构建力导向引用拓扑图，利用 PageRank 与中心性算法挖掘出金色基石论文与蓝色衍生论文。",
        cursor_a=(55, 260),
        cursor_b=(560, 480),
        box_a=(120, 125, 1010, 860),
        box_b=(1140, 125, 760, 860),
    ),
    SegmentSpec(
        idx=27,
        act_num=5,
        act_title="第五幕 · 模块四与模块五：引用含金量下钻与引用拓扑图谱",
        title="基于引用拓扑脉络一键生成带出处领域综述",
        narration="切换到右侧自动领域综述面板，Agent 沿着从基石论文到衍生分支的演进脉络，一键生成句句标明论文编号出处的结构化领域文献综述。",
        cursor_a=(1360, 150),
        cursor_b=(1520, 480),
        box_a=(1140, 125, 760, 860),
        box_b=(1140, 125, 760, 860),
    ),
    SegmentSpec(
        idx=28,
        act_num=5,
        act_title="第五幕 · 模块四与模块五：引用含金量下钻与引用拓扑图谱",
        title="Future Work 时间线交叉验证与开放前沿去伪",
        narration="切换到 Future Work 前沿去伪面板，系统结合发表时间线与后续论文突破进行交叉核验，自动排除已被后续研究解决的旧设想，提炼出真正开放的科研前沿方向。",
        cursor_a=(1480, 150),
        cursor_b=(1520, 520),
        box_a=(1140, 125, 760, 860),
        box_b=(1140, 125, 760, 860),
    ),
    # ==================== 第六幕：模块六 · 无幻觉写作框架、架构拓扑图、三维雷达图与总结 ====================
    SegmentSpec(
        idx=29,
        act_num=6,
        act_title="第六幕 · 模块六：无幻觉写作框架、架构图与三维雷达图",
        title="无幻觉论文框架生成与多源实名参考文献校验",
        narration="最后是模块六：写作与数据 Copilot。输入研究想法后，系统生成涵盖摘要、导言、相关工作与方法论的完整论文框架，底部所有参考文献均经 Crossref 与本地库实名核验，确保百分百零幻觉。",
        cursor_a=(55, 305),
        cursor_b=(1060, 720),
        box_a=(120, 120, 1780, 880),
        box_b=(135, 500, 1750, 480),
    ),
    SegmentSpec(
        idx=30,
        act_num=6,
        act_title="第六幕 · 模块六：无幻觉写作框架、架构图与三维雷达图",
        title="多格式架构拓扑图脚本生成与实时矢量预览",
        narration="在架构拓扑图模块，系统支持一键生成 Mermaid、Matplotlib、TikZ 与 Graphviz 四种拓扑图脚本，既可在平台内实时渲染预览，也可复制源码直接用于 Overleaf 论文。",
        cursor_a=(310, 82),
        cursor_b=(1380, 540),
        box_a=(120, 120, 1780, 880),
        box_b=(880, 220, 1000, 760),
    ),
    SegmentSpec(
        idx=31,
        act_num=6,
        act_title="第六幕 · 模块六：无幻觉写作框架、架构图与三维雷达图",
        title="AST 安全沙箱三维多层雷达图、双语图注与三线表",
        narration="在数据可视化模块，上传实验 CSV 数据后，后端受限 AST 安全沙箱一键渲染出精美的多模型三维雷达图，并基于真实统计结果自动撰写中英双语图注与可复制的 LaTeX 三线表。",
        cursor_a=(480, 82),
        cursor_b=(1380, 520),
        box_a=(880, 210, 1000, 520),
        box_b=(880, 540, 1000, 450),
    ),
    SegmentSpec(
        idx=32,
        act_num=6,
        act_title="第六幕 · 模块六：无幻觉写作框架、架构图与三维雷达图",
        title="学术中译英润色与全流程科研闭环总结",
        narration="配合保留公式不变的学术中译英润色功能，ScholarAgent 真正实现了从文献导入、精读问答、领域洞察到论文写作的全流程科研闭环。演示完毕，谢谢各位评委老师！",
        cursor_a=(650, 82),
        cursor_b=(960, 540),
        box_a=(120, 120, 1780, 880),
        box_b=(180, 110, 1560, 820),
        overlay_finale=True,
    ),
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def probe_duration(media_path: Path) -> float:
    proc = subprocess.run(
        [FFMPEG, "-i", str(media_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        return 8.0
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def split_clauses(text: str, speech_dur: float) -> list[tuple[str, float, float]]:
    raw_parts = re.split(r"(?<=[。！？；，：])", text.strip())
    merged: list[str] = []
    buf = ""
    for p in raw_parts:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) <= 28:
            buf += p
        else:
            if buf:
                merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)
    if not merged:
        merged = [text.strip()]

    total_chars = sum(max(1, len(c)) for c in merged)
    clauses: list[tuple[str, float, float]] = []
    t = 0.0
    for i, c in enumerate(merged):
        dur = speech_dur * (max(1, len(c)) / total_chars)
        end_t = speech_dur if i == len(merged) - 1 else (t + dur)
        clauses.append((c.rstrip("，；："), t, end_t))
        t = end_t
    return clauses


async def _synthesize_tts_segments() -> None:
    """并发调用 edge-tts 为 32 个分镜生成原始语音 MP3。"""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(4)

    async def _synth_one(seg: SegmentSpec) -> None:
        mp3 = AUDIO_DIR / f"seg_{seg.idx:02d}.mp3"
        seg.audio_path = mp3
        if not mp3.exists() or mp3.stat().st_size < 1000:
            async with sem:
                comm = edge_tts.Communicate(seg.narration, VOICE, rate=VOICE_RATE)
                await comm.save(str(mp3))
        seg.speech_dur = probe_duration(mp3)

    await asyncio.gather(*[_synth_one(s) for s in SEGMENTS])


def prepare_all_audio() -> None:
    """合成 32 段普通话配音，并校准停顿与语速使成片总长落在约 375 秒（6 分 15 秒）。"""
    asyncio.run(_synthesize_tts_segments())

    raw_speech_total = sum(s.speech_dur for s in SEGMENTS)
    target_speech_total = 352.0
    atempo = max(0.92, min(1.25, raw_speech_total / target_speech_total))

    for seg in SEGMENTS:
        adj_speech = seg.speech_dur / atempo
        seg.speech_dur = adj_speech
        raw_dur = adj_speech + 0.72
        frames = max(round(raw_dur * FPS), math.ceil(adj_speech * FPS) + 6)
        seg.total_dur = frames / FPS
        seg.clauses = split_clauses(seg.narration, seg.speech_dur)

        padded = AUDIO_DIR / f"seg_{seg.idx:02d}_pad.wav"
        seg.padded_audio_path = padded
        subprocess.run(
            [
                FFMPEG,
                "-y",
                "-i",
                str(seg.audio_path),
                "-af",
                f"atempo={atempo:.4f},apad=whole_dur={seg.total_dur:.3f}",
                "-t",
                f"{seg.total_dur:.3f}",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(padded),
            ],
            capture_output=True,
            check=True,
        )

    final_total = sum(s.total_dur for s in SEGMENTS)
    print(
        f"[TTS] 32 段语音准备就绪: 原始语音 {raw_speech_total:.1f}s (atempo={atempo:.2f}) -> "
        f"校准后总视频时长 {final_total:.1f}s ({final_total / 60:.2f} 分钟)"
    )


def capture_missing_or_updated_shots(username: str, password: str) -> None:
    """复用已成功截取的前序高清截图，并补全/更新阅读器、含金量评估、Future Work 与 Copilot 画面。"""
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    for seg in SEGMENTS:
        seg.shot_a = FRAMES_DIR / f"seg_{seg.idx:02d}_a.png"
        seg.shot_b = FRAMES_DIR / f"seg_{seg.idx:02d}_b.png"

    def snap(page: Page, seg_idx: int, sub: str) -> Path:
        out = FRAMES_DIR / f"seg_{seg_idx:02d}_{sub}.png"
        page.screenshot(path=str(out))
        return out

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1.0)
        page = ctx.new_page()

        page.goto(BASE_URL, wait_until="networkidle")
        page.get_by_placeholder("请输入用户名").fill(username)
        page.get_by_placeholder("请输入密码").fill(password)
        page.get_by_role("button", name="登录进入工作台").click()
        page.get_by_text("智研全流程科研助手").wait_for(timeout=20000)
        page.wait_for_timeout(1200)

        # ---------- 补强 Seg 19, 21, 22（模块三：经文献库跳转至 Attention Is All You Need，演示划词翻译 + 双向引用跳转 + 跳回正文） ----------
        sb = page.get_by_placeholder("搜索标题 / arXiv ID / 文件名")
        sb.fill("Attention Is All You Need")
        page.wait_for_timeout(600)
        row_att = page.locator("tr", has_text="Attention Is All You Need").first
        row_att.get_by_role("button", name="阅读/翻译").click()
        try:
            page.get_by_text("/ 15").wait_for(timeout=15000)
            page.wait_for_timeout(1500)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(3000)

        page_input = page.locator(".ant-input-number-input").first
        if page_input.count() > 0:
            page_input.fill("2")
            page_input.press("Enter")
            page.wait_for_timeout(2200)

        page.evaluate(
            """() => {
                const els = Array.from(document.querySelectorAll('div, span'));
                const target = els.find(el => el.innerText && el.innerText.includes('Provided proper attribution') && el.innerText.length < 280);
                if (target) {
                    const range = document.createRange();
                    range.selectNodeContents(target);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                }
            }"""
        )
        page.wait_for_timeout(600)
        btn_snip = page.get_by_role("button", name=re.compile("划词翻译"))
        if btn_snip.count() > 0 and not btn_snip.first.is_disabled():
            btn_snip.first.click()
            try:
                page.locator(".ant-popover-inner-content").first.wait_for(timeout=20000)
                page.wait_for_timeout(800)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(2500)
            snap(page, 19, "b")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        tab_cite = page.locator(".ant-tabs-tab", has_text="双向引用跳转")
        if tab_cite.count() > 0:
            tab_cite.first.click()
            try:
                page.get_by_text(re.compile(r"跳回正文引用处")).first.wait_for(timeout=10000)
                page.wait_for_timeout(1200)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1200)
            snap(page, 21, "b")
            snap(page, 22, "a")
            back_tag = page.locator(".ant-tag", has_text=re.compile(r"跳回正文引用处")).first
            if back_tag.count() > 0:
                back_tag.click()
                try:
                    page.get_by_text("3", exact=True).first.wait_for(timeout=5000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(2500)
                snap(page, 22, "b")

        # ---------- 补强 Seg 24, 25（模块四：选择 Attention Is All You Need 并等待含金量评估表格行渲染完成） ----------
        page.get_by_role("menuitem", name="检索下钻与推荐").click()
        page.wait_for_timeout(1500)
        tab_drill = page.locator(".ant-tabs-tab", has_text="智能下钻与引用含金量筛选")
        if tab_drill.count() > 0:
            tab_drill.first.click()
            page.wait_for_timeout(800)
        sel_box = page.locator(".ant-select").first
        if sel_box.count() > 0:
            sel_box.click()
            page.wait_for_timeout(400)
            # 滚动虚拟列表到底部以显露 Attention Is All You Need（id=1）
            page.evaluate(
                """() => {
                    const holder = document.querySelector('.rc-virtual-list-holder');
                    if (holder) holder.scrollTop = holder.scrollHeight;
                }"""
            )
            page.wait_for_timeout(300)
            opt_att = page.locator(".ant-select-item-option", has_text="Attention Is All You Need").first
            if opt_att.count() > 0:
                opt_att.click()
                page.wait_for_timeout(500)
        snap(page, 24, "a")
        eval_btn = page.get_by_role("button", name=re.compile("评估含金量"))
        if eval_btn.count() > 0:
            eval_btn.first.click()
            try:
                page.locator("tbody.ant-table-tbody tr.ant-table-row").first.wait_for(timeout=60000)
                page.wait_for_timeout(1000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(3000)
        snap(page, 24, "b")
        snap(page, 25, "a")
        snap(page, 25, "b")

        # ---------- 补强 Seg 28（模块五：Future Work 前沿去伪标签页） ----------
        page.get_by_role("menuitem", name="引用图谱与综述").click()
        page.wait_for_timeout(2500)
        tab_fw = page.locator(".ant-tabs-tab", has_text="Future Work 前沿去伪")
        if tab_fw.count() > 0:
            tab_fw.first.click()
            try:
                page.get_by_text("提炼出的高价值开放未来方向").wait_for(timeout=10000)
                page.wait_for_timeout(1000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1200)
        snap(page, 28, "b")

        # ---------- 采集 Seg 29, 30, 31, 32（模块六：写作与数据 Copilot 四个子模块） ----------
        page.get_by_role("menuitem", name="写作与数据 Copilot").click()
        page.wait_for_timeout(1500)
        idea_box = page.get_by_placeholder(re.compile("输入您的科研 Idea")).first
        idea_box.fill("设计一种结合引用图谱拓扑检索与 Critic 逐句事实核查的抗幻觉科研多智能体架构")
        page.wait_for_timeout(400)
        snap(page, 29, "a")
        btn_outline = page.get_by_role("button", name=re.compile("生成无幻觉论文框架"))
        if btn_outline.count() > 0:
            btn_outline.first.click()
            try:
                page.get_by_text(re.compile("已校验")).first.wait_for(timeout=60000)
                page.wait_for_timeout(1000)
                page.get_by_text(re.compile("已校验")).first.scroll_into_view_if_needed()
                page.wait_for_timeout(600)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(3000)
        snap(page, 29, "b")

        tab_diag = page.locator(".ant-tabs-tab", has_text="架构拓扑图")
        if tab_diag.count() > 0:
            tab_diag.first.click()
            page.wait_for_timeout(800)
        diag_box = page.get_by_placeholder(re.compile("描述需要绘制的模型")).first
        diag_box.fill("输入PDF -> 多模态版面解析 -> 混合检索(BM25+向量) -> Planner任务规划 -> Critic核查 -> 带[E#]溯源锚点输出")
        page.wait_for_timeout(400)
        snap(page, 30, "a")
        btn_diag = page.get_by_role("button", name=re.compile("生成架构拓扑图"))
        if btn_diag.count() > 0:
            btn_diag.first.click()
            try:
                page.get_by_text("可视化预览").wait_for(timeout=45000)
                page.wait_for_timeout(1800)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(3000)
        snap(page, 30, "b")

        tab_chart = page.locator(".ant-tabs-tab", has_text="数据可视化")
        if tab_chart.count() > 0:
            tab_chart.first.click()
            page.wait_for_timeout(800)
        snap(page, 31, "a")
        btn_chart = page.get_by_role("button", name=re.compile("从下方 CSV 生成图表与图注"))
        if btn_chart.count() > 0:
            btn_chart.first.click()
            try:
                page.get_by_text("安全沙箱渲染结果").wait_for(timeout=45000)
                page.wait_for_timeout(1500)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(3000)
        snap(page, 31, "b")

        tab_tr = page.locator(".ant-tabs-tab", has_text="学术中译英")
        if tab_tr.count() > 0:
            tab_tr.first.click()
            page.wait_for_timeout(800)
        tr_box = page.get_by_placeholder(re.compile("粘贴中文摘要或论文章节")).first
        tr_box.fill("针对大语言模型在长文档科学问答中的事实幻觉问题，本文提出结合版面感知解析、混合检索与逐句核查的多智能体框架。在复杂度为 $O(n^2)$ 的基准评测中取得 100% 准确率。")
        page.wait_for_timeout(400)
        btn_tr = page.get_by_role("button", name=re.compile("一键学术英译"))
        if btn_tr.count() > 0:
            btn_tr.first.click()
            try:
                page.get_by_role("button", name="复制英文").wait_for(timeout=45000)
                page.wait_for_timeout(1000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(3000)
        snap(page, 32, "a")
        snap(page, 32, "b")

        browser.close()

    # 校验全部 64 张截图完整性
    for seg in SEGMENTS:
        assert seg.shot_a and seg.shot_a.exists() and seg.shot_a.stat().st_size > 15000, f"Missing/blank {seg.shot_a}"
        assert seg.shot_b and seg.shot_b.exists() and seg.shot_b.stat().st_size > 15000, f"Missing/blank {seg.shot_b}"
    print("[Playwright] 全部 32 个分镜共 64 张 1080p 高清实机画面校验通过！")


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    bold_path = next((p for p in candidates if Path(p).exists()), candidates[-1])
    reg_path = r"C:\Windows\Fonts\msyh.ttc" if Path(r"C:\Windows\Fonts\msyh.ttc").exists() else bold_path
    return {
        "sub": ImageFont.truetype(bold_path, 30),
        "badge": ImageFont.truetype(bold_path, 20),
        "title": ImageFont.truetype(bold_path, 22),
        "hero_xl": ImageFont.truetype(bold_path, 46),
        "hero_lg": ImageFont.truetype(bold_path, 28),
        "hero_md": ImageFont.truetype(reg_path, 22),
    }


def make_arch_overlay_image(base_img: Image.Image, fonts: dict[str, ImageFont.FreeTypeFont]) -> Image.Image:
    canvas = base_img.copy().convert("RGBA")
    dim = Image.new("RGBA", (W, H), (10, 18, 36, 175))
    canvas = Image.alpha_composite(canvas, dim)

    arch_path = ROOT / "docs" / "arch_diagram.png"
    if arch_path.exists():
        arch = Image.open(arch_path).convert("RGBA")
        arch.thumbnail((1480, 780), Image.Resampling.LANCZOS)
        aw, ah = arch.size
        card = Image.new("RGBA", (aw + 48, ah + 96), (255, 255, 255, 250))
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((0, 0, aw + 47, ah + 95), radius=18, outline=(37, 99, 235, 255), width=4)
        draw.text((28, 18), "ScholarAgent 智研助手 · 全栈分层多智能体系统架构图", font=fonts["hero_lg"], fill=(15, 23, 42, 255))
        card.paste(arch, (24, 68), arch)
        cx = (W - card.width) // 2
        cy = (H - card.height) // 2 - 20
        canvas.paste(card, (cx, cy), card)
    return canvas.convert("RGB")


def make_finale_overlay_image(base_img: Image.Image, fonts: dict[str, ImageFont.FreeTypeFont]) -> Image.Image:
    canvas = base_img.copy().convert("RGBA")
    dim = Image.new("RGBA", (W, H), (11, 22, 44, 215))
    canvas = Image.alpha_composite(canvas, dim)
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((180, 110, W - 180, H - 150), radius=24, fill=(17, 30, 58, 245), outline=(56, 189, 248, 255), width=3)
    draw.text((240, 155), "ScholarAgent 智研助手 · 全流程科研多智能体系统", font=fonts["hero_xl"], fill=(255, 255, 255, 255))
    draw.text((240, 222), "传智杯 · AI 大模型创新应用挑战赛  |  参赛选手：李嘉成（重庆科技大学 · 个人赛）", font=fonts["hero_lg"], fill=(125, 211, 252, 255))

    cards = [
        ("六大核心 Agent 能力", "Task Planning 多步规划 · 短长期混合 Memory · 11 项受控 Function Calling · Multi-Agent 协同 · 像素级 Source Tracing · Guardrails 护栏"),
        ("模块一 & 模块三：多模态解析与双语精读", "5-gram MinHash + 向量语义查重与版本比对 · 双栏/扫描件 OCR/541 页书籍流式解析 · 原生 PDF.js 双向引用跳转与中英同步滚动"),
        ("模块二：抗幻觉问答与多文联合推理", "章节前缀 + BM25 + 稠密向量混合检索 · 跨文献均衡采样对比 · Critic 逐句核查 · 11/11 (100%) 官方基准评测准确率"),
        ("模块四 ~ 模块六：检索推荐、引用图谱与写作 Copilot", "Sickle arXiv 追踪与 GitHub 开源易复现加权 · 应付式引用剔除 · PageRank 基石论文挖掘与前沿去伪 · Crossref 实名校验写作与 3D 雷达图沙箱"),
    ]
    y = 295
    for head, body in cards:
        draw.rounded_rectangle((240, y, W - 240, y + 118), radius=14, fill=(30, 48, 86, 235), outline=(96, 165, 250, 200), width=2)
        draw.text((270, y + 18), f"✦  {head}", font=fonts["hero_lg"], fill=(250, 204, 21, 255))
        draw.text((270, y + 64), body, font=fonts["hero_md"], fill=(226, 232, 240, 255))
        y += 136

    draw.text((240, 860), "开源仓库：https://github.com/liuchengfeng116-dev/scholar-agent      感谢各位评委老师审阅！", font=fonts["hero_md"], fill=(148, 163, 184, 255))
    return canvas.convert("RGB")


def compose_styled_frame(
    bg_rgb: Image.Image,
    seg: SegmentSpec,
    clause_text: str,
    global_progress: float,
    box: tuple[int, int, int, int] | None,
    cursor_xy: tuple[int, int],
    click_ripple: float,
    pulse_bright: bool,
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> bytes:
    img = bg_rgb.copy().convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 1. 高亮聚焦框
    if box is not None:
        bx, by, bw, bh = box
        bx2, by2 = min(W - 6, bx + bw), min(H - 92, by + bh)
        color = (245, 158, 11, 235) if pulse_bright else (37, 99, 235, 210)
        fill_c = (245, 158, 11, 22) if pulse_bright else (37, 99, 235, 16)
        draw.rounded_rectangle((bx, by, bx2, by2), radius=12, fill=fill_c, outline=color, width=4)

    # 2. 顶部章节胶囊与全局进度条
    draw.rectangle((0, 0, W, 5), fill=(30, 41, 59, 200))
    prog_w = max(8, int(W * min(1.0, max(0.0, global_progress))))
    draw.rectangle((0, 0, prog_w, 5), fill=(56, 189, 248, 255))

    pill_text = f"{seg.act_title}   |   {seg.idx:02d}/32 · {seg.title}"
    tw = int(draw.textlength(pill_text, font=fonts["badge"]))
    px1 = (W - (tw + 44)) // 2
    px2 = px1 + tw + 44
    draw.rounded_rectangle((px1, 10, px2, 48), radius=19, fill=(15, 23, 42, 220), outline=(56, 189, 248, 200), width=2)
    draw.text((px1 + 22, 16), pill_text, font=fonts["badge"], fill=(248, 250, 252, 255))

    # 3. 鼠标光标与点击扩散波纹
    cx, cy = cursor_xy
    if 0.0 < click_ripple < 1.0:
        r_rad = int(12 + 38 * click_ripple)
        alpha = int(220 * (1.0 - click_ripple))
        draw.ellipse((cx - r_rad, cy - r_rad, cx + r_rad, cy + r_rad), outline=(250, 204, 21, alpha), width=4)

    draw.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=(250, 204, 21, 95))
    arrow = [
        (cx, cy),
        (cx, cy + 26),
        (cx + 7, cy + 20),
        (cx + 13, cy + 31),
        (cx + 18, cy + 28),
        (cx + 12, cy + 17),
        (cx + 20, cy + 17),
    ]
    draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(15, 23, 42, 255))

    # 4. 底部半透明毛玻璃字幕栏
    if clause_text:
        sub_w = int(draw.textlength(clause_text, font=fonts["sub"]))
        pad_x = 36
        bar_w = min(W - 80, sub_w + pad_x * 2)
        bx1 = (W - bar_w) // 2
        bx2 = bx1 + bar_w
        by1, by2 = H - 76, H - 14
        draw.rounded_rectangle((bx1, by1, bx2, by2), radius=16, fill=(15, 23, 42, 228), outline=(71, 85, 105, 190), width=2)
        tx = (W - sub_w) // 2
        draw.text((tx + 2, by1 + 14), clause_text, font=fonts["sub"], fill=(0, 0, 0, 220))
        draw.text((tx, by1 + 12), clause_text, font=fonts["sub"], fill=(255, 255, 255, 255))

    out = Image.alpha_composite(img, overlay).convert("RGB")
    return out.tobytes()


def encode_full_video() -> None:
    OUT_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    fonts = _load_fonts()

    concat_list = WORK_DIR / "audio_concat.txt"
    concat_lines = [f"file '{seg.padded_audio_path.as_posix()}'" for seg in SEGMENTS if seg.padded_audio_path]
    concat_list.write_text("\n".join(concat_lines), encoding="utf-8")
    merged_audio = WORK_DIR / "merged_audio.m4a"
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(merged_audio),
        ],
        capture_output=True,
        check=True,
    )

    total_video_dur = sum(s.total_dur for s in SEGMENTS)
    total_frames_all = round(total_video_dur * FPS)
    print(f"[Encode] 开始渲染合成 1080p 视频: 总帧数 {total_frames_all} ({total_video_dur:.1f}s @ {FPS}fps)...")

    cmd = [
        FFMPEG,
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-i",
        str(merged_audio),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        str(OUT_VIDEO),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None

    written_frames = 0
    prev_cursor = (960, 540)

    for seg in SEGMENTS:
        img_a = Image.open(seg.shot_a).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        img_b = Image.open(seg.shot_b).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        if seg.overlay_arch:
            img_b = make_arch_overlay_image(img_b, fonts)
        if seg.overlay_finale:
            img_b = make_finale_overlay_image(img_b, fonts)

        seg_frames = round(seg.total_dur * FPS)
        mid_frame = int(seg_frames * 0.46)
        trans_frames = 8

        steady_cache: dict[tuple[str, int, bool], bytes] = {}

        for f in range(seg_frames):
            t_sec = f / FPS
            global_prog = (written_frames + f) / max(1, total_frames_all)

            clause_idx = -1
            clause_str = ""
            for ci, (ctext, c_start, c_end) in enumerate(seg.clauses):
                if c_start <= t_sec <= c_end + 0.15:
                    clause_idx = ci
                    clause_str = ctext
                    break

            if f < trans_frames:
                u = f / max(1, trans_frames - 1)
                ease = 1.0 - (1.0 - u) ** 3
                cx = int(prev_cursor[0] + (seg.cursor_a[0] - prev_cursor[0]) * ease)
                cy = int(prev_cursor[1] + (seg.cursor_a[1] - prev_cursor[1]) * ease)
                buf = compose_styled_frame(
                    img_a,
                    seg,
                    clause_str,
                    global_prog,
                    seg.box_a,
                    (cx, cy),
                    click_ripple=u,
                    pulse_bright=True,
                    fonts=fonts,
                )
            elif f < mid_frame:
                pulse = ((f // 10) % 2) == 0
                cache_key = ("A", clause_idx, pulse)
                if cache_key not in steady_cache:
                    steady_cache[cache_key] = compose_styled_frame(
                        img_a,
                        seg,
                        clause_str,
                        global_prog,
                        seg.box_a,
                        seg.cursor_a,
                        click_ripple=0.0,
                        pulse_bright=pulse,
                        fonts=fonts,
                    )
                buf = steady_cache[cache_key]
            elif f < mid_frame + trans_frames:
                u = (f - mid_frame) / max(1, trans_frames - 1)
                ease = 1.0 - (1.0 - u) ** 3
                blended = Image.blend(img_a, img_b, ease)
                cx = int(seg.cursor_a[0] + (seg.cursor_b[0] - seg.cursor_a[0]) * ease)
                cy = int(seg.cursor_a[1] + (seg.cursor_b[1] - seg.cursor_a[1]) * ease)
                buf = compose_styled_frame(
                    blended,
                    seg,
                    clause_str,
                    global_prog,
                    seg.box_b,
                    (cx, cy),
                    click_ripple=u,
                    pulse_bright=True,
                    fonts=fonts,
                )
            else:
                pulse = ((f // 10) % 2) == 0
                cache_key = ("B", clause_idx, pulse)
                if cache_key not in steady_cache:
                    steady_cache[cache_key] = compose_styled_frame(
                        img_b,
                        seg,
                        clause_str,
                        global_prog,
                        seg.box_b,
                        seg.cursor_b,
                        click_ripple=0.0,
                        pulse_bright=pulse,
                        fonts=fonts,
                    )
                buf = steady_cache[cache_key]

            proc.stdin.write(buf)

        written_frames += seg_frames
        prev_cursor = seg.cursor_b

    proc.stdin.close()
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"FFmpeg 编码失败，退出码: {ret}")

    final_dur = probe_duration(OUT_VIDEO)
    size_mb = OUT_VIDEO.stat().st_size / (1024 * 1024)
    print(f"[Done] 演示视频生成成功: {OUT_VIDEO} | 时长: {final_dur:.1f}s ({final_dur/60:.2f} 分钟) | 大小: {size_mb:.2f} MB")


def main() -> None:
    env = _load_env()
    user = env.get("REVIEWER_USERNAME", "reviewer")
    pwd = env.get("REVIEWER_PASSWORD", "")
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    prepare_all_audio()
    capture_missing_or_updated_shots(user, pwd)
    encode_full_video()


if __name__ == "__main__":
    main()
