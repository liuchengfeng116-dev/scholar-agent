"""安全 Python 沙箱执行器：AST 静态安全检查 + 独立子进程 + 超时与路径隔离。"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

_FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "http", "urllib", "requests", "httpx",
    "shutil", "pathlib", "ctypes", "importlib", "pickle", "multiprocessing",
}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open"}


def validate_code_safety(code: str) -> None:
    """AST 静态检查，禁止危险系统/网络/文件任意操作。"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    raise ValueError(f"沙箱禁止导入模块: {root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_MODULES:
                raise ValueError(f"沙箱禁止导入模块: {root}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                raise ValueError(f"沙箱禁止调用函数: {node.func.id}")


def run_chart_script(
    script_body: str,
    *,
    work_dir: Path,
    csv_path: Path | None = None,
    out_png: Path,
    timeout_sec: int = 18,
) -> tuple[bool, str]:
    """在受限子进程中执行安全的 Matplotlib 绘图脚本。"""
    validate_code_safety(script_body)
    work_dir.mkdir(parents=True, exist_ok=True)
    runner_path = work_dir / "_sandbox_run.py"

    # 仅向用户脚本注入只读 CSV 路径与输出 PNG 路径变量
    preamble = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        "plt.rcParams['axes.unicode_minus'] = False\n"
        "plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']\n"
        f"CSV_PATH = {repr(str(csv_path)) if csv_path else 'None'}\n"
        f"OUT_PNG = {repr(str(out_png))}\n"
    )
    runner_path.write_text(preamble + "\n" + script_body, encoding="utf-8")

    safe_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": str(work_dir),
        "TMP": str(work_dir),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, str(runner_path)],
        cwd=str(work_dir),
        env=safe_env,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    ok = proc.returncode == 0 and out_png.exists() and out_png.stat().st_size > 0
    msg = (proc.stderr or proc.stdout or "").strip()
    return ok, msg
