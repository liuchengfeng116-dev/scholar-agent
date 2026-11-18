"""自动截取系统各模块真实运行截图（用于技术文档与 README）。

用法（需先启动后端 8000 与前端 5173）：
    .venv\\Scripts\\python.exe scripts\\capture_screenshots.py [--only library,reader]

依赖：pip install playwright（直接复用本机 Microsoft Edge，无需下载浏览器内核）。
评审账号从 .env 中的 REVIEWER_USERNAME / REVIEWER_PASSWORD 读取，不在代码中硬编码。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screenshots"
BASE_URL = os.environ.get("SCHOLAR_BASE_URL", "http://127.0.0.1:5173")

MENUS = {
    "library": "文献库与解析",
    "reader": "阅读与双向翻译",
    "chat": "抗幻觉 Agent 问答",
    "scout": "检索下钻与推荐",
    "graph": "引用图谱与综述",
    "copilot": "写作与数据 Copilot",
}


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def login(page: Page, username: str, password: str) -> None:
    page.goto(BASE_URL, wait_until="networkidle")
    page.get_by_placeholder("请输入用户名").fill(username)
    page.get_by_placeholder("请输入密码").fill(password)
    page.get_by_role("button", name="登录进入工作台").click()
    page.get_by_text("智研全流程科研助手").wait_for(timeout=20000)


def open_menu(page: Page, key: str, wait_ms: int = 2500) -> None:
    page.get_by_role("menuitem", name=MENUS[key]).click()
    page.wait_for_timeout(wait_ms)


def prepare_view(page: Page, key: str) -> None:
    """在每个模块中触发核心真实视图状态，避免截到空白初始页。"""
    if key == "library":
        # 打开文献库，展示多篇真实论文及结构化统计
        page.wait_for_timeout(1500)
    elif key == "reader":
        # 回到文献库搜索 Attention Is All You Need（11篇文献分页在第2页）并点击“阅读/翻译”
        page.get_by_role("menuitem", name=MENUS["library"]).click()
        page.wait_for_timeout(800)
        sb = page.get_by_placeholder("搜索标题 / arXiv ID / 文件名")
        sb.fill("Attention Is All You Need")
        page.wait_for_timeout(500)
        row = page.locator("tr", has_text="Attention Is All You Need").first
        row.get_by_role("button", name="阅读/翻译").click()
        page.wait_for_timeout(4000)
    elif key == "chat":
        # 点击左侧已预置的历史对话，展示真实问答、引用锚点与右侧 Agent 协同执行轨迹
        conv_item = page.locator(".ant-list-item").first
        if conv_item.count() > 0:
            conv_item.click()
            page.wait_for_timeout(2000)
    elif key == "scout":
        page.wait_for_timeout(2000)
    elif key == "graph":
        # 等待 ECharts 力导向引用拓扑渲染稳定
        page.wait_for_timeout(3000)
    elif key == "copilot":
        # 切换到“CSV 数据可视化与三维雷达图”并生成三维雷达图 + 双语 Figure Caption + LaTeX 表格
        tab = page.get_by_text("CSV 数据可视化与三维雷达图")
        if tab.count() > 0:
            tab.first.click()
            page.wait_for_timeout(600)
            btn = page.get_by_role("button", name="从下方 CSV 生成图表与图注")
            if btn.count() > 0:
                btn.click()
                page.get_by_text("安全沙箱渲染结果").wait_for(timeout=30000)
                page.wait_for_timeout(1500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=",".join(MENUS))
    args = parser.parse_args()
    targets = [t for t in args.only.split(",") if t in MENUS]

    env = _load_env()
    user = env.get("REVIEWER_USERNAME", "reviewer")
    pwd = env.get("REVIEWER_PASSWORD", "")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5)
        page = ctx.new_page()
        login(page, user, pwd)
        for key in targets:
            open_menu(page, key, wait_ms=2000)
            prepare_view(page, key)
            out = OUT_DIR / f"{key}.png"
            page.screenshot(path=str(out))
            print("saved", out.relative_to(ROOT))
        browser.close()


if __name__ == "__main__":
    main()
