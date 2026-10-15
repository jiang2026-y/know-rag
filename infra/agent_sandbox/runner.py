"""Python 代码分析沙箱入口，仅允许静态语法分析或完整 pytest 测试。

定义 TaskTimeoutError、main、run_analysis_with_timeout、analyze_python_sources、run_tests、
copy_workspace 和 _source_files：分别负责超时控制、分派受限操作、限制分析时长、分析 Python
语法、运行测试、复制只读工作区和枚举授权源码。
"""

from __future__ import annotations

import ast
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path


SOURCE_WORKSPACE = Path("/workspace")
RUNTIME_WORKSPACE = Path("/tmp/workspace")
IGNORED_DIRECTORIES = {".git", ".venv", ".pytest_cache", "__pycache__"}
TASK_TIMEOUT_SECONDS = 90


class TaskTimeoutError(RuntimeError):
    """沙箱操作超过允许时长时抛出。"""


def main() -> int:
    """仅接受 analyze 或 test 操作，拒绝向容器传入任意命令。"""
    if len(sys.argv) != 2 or sys.argv[1] not in {"analyze", "test"}:
        print("仅允许操作：analyze 或 test", file=sys.stderr)
        return 2
    if not SOURCE_WORKSPACE.is_dir():
        print("未挂载授权的只读工作区", file=sys.stderr)
        return 2
    if sys.argv[1] == "analyze":
        return run_analysis_with_timeout()
    return run_tests()


def run_analysis_with_timeout() -> int:
    """使用 SIGALRM 限制静态分析时长，避免大量文件长期占用容器。"""
    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TASK_TIMEOUT_SECONDS)
    try:
        return analyze_python_sources()
    except TaskTimeoutError:
        print(f"分析超过 {TASK_TIMEOUT_SECONDS} 秒，已终止", file=sys.stderr)
        return 124
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def analyze_python_sources() -> int:
    """使用 AST 解析所有 Python 源文件，不导入或执行待分析代码。"""
    invalid_files: list[str] = []
    checked_count = 0
    for source_file in _source_files(SOURCE_WORKSPACE):
        checked_count += 1
        try:
            ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        except (OSError, SyntaxError) as exc:
            invalid_files.append(f"{source_file.relative_to(SOURCE_WORKSPACE)}: {exc}")

    print(f"已完成 Python 静态语法分析：{checked_count} 个文件")
    if not invalid_files:
        return 0
    print("以下文件无法通过语法解析：", file=sys.stderr)
    print("\n".join(invalid_files), file=sys.stderr)
    return 1


def run_tests() -> int:
    """将只读源码复制到临时目录后运行 pytest，避免测试写入原始工作区。"""
    copy_workspace()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(RUNTIME_WORKSPACE / "src")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=RUNTIME_WORKSPACE,
            env=environment,
            check=False,
            timeout=TASK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"测试超过 {TASK_TIMEOUT_SECONDS} 秒，已终止", file=sys.stderr)
        return 124
    return completed.returncode


def copy_workspace() -> None:
    """复制任务授权文件到临时可写目录，并排除依赖、缓存和版本库元数据。"""
    if RUNTIME_WORKSPACE.exists():
        shutil.rmtree(RUNTIME_WORKSPACE)
    shutil.copytree(
        SOURCE_WORKSPACE,
        RUNTIME_WORKSPACE,
        ignore=shutil.ignore_patterns(*IGNORED_DIRECTORIES),
    )


def _source_files(root: Path) -> list[Path]:
    """返回授权工作区内的 Python 文件，并跳过缓存和虚拟环境目录。"""
    return [
        path
        for path in root.rglob("*.py")
        if not any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts)
    ]


def _timeout_handler(_: int, __: object) -> None:
    """由 Linux 容器的 SIGALRM 中断超过时限的静态分析任务。"""
    raise TaskTimeoutError


if __name__ == "__main__":
    raise SystemExit(main())
