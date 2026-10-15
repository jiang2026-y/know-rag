"""受控沙箱调度器：创建白名单临时工作区并启动受限 Docker 任务。

定义 stage_project_workspace、build_sandbox_command、run_sandbox 和 main：
分别负责复制授权 Python 文件、构造固定 Docker 命令、在临时目录中执行任务和解析受限命令行参数。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "infra" / "agent_sandbox" / "compose.yaml"
ALLOWED_DIRECTORIES = ("src", "tests")
ALLOWED_FILES = (
    "pyproject.toml",
    "infra/agent_sandbox/sandbox_dispatcher.py",
)
ALLOWED_OPERATIONS = ("analyze", "test")


def stage_project_workspace(source_root: Path, destination: Path) -> None:
    """仅复制白名单中的 Python 源码、测试代码和项目测试配置到临时工作区。"""
    source_root = source_root.resolve(strict=True)
    destination.mkdir(parents=True, exist_ok=True)

    for filename in ALLOWED_FILES:
        source_file = source_root / filename
        if source_file.is_symlink():
            raise ValueError(f"缺少或拒绝复制授权文件：{filename}")
        safe_file = _safe_source_path(source_root, source_file)
        if not safe_file.is_file():
            raise ValueError(f"缺少或拒绝复制授权文件：{filename}")
        target_file = destination / filename
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_file, target_file)

    for directory_name in ALLOWED_DIRECTORIES:
        source_directory = source_root / directory_name
        if source_directory.is_symlink():
            raise ValueError(f"缺少或拒绝复制授权目录：{directory_name}")
        safe_directory = _safe_source_path(source_root, source_directory)
        if not safe_directory.is_dir():
            raise ValueError(f"缺少或拒绝复制授权目录：{directory_name}")
        for source_file in safe_directory.rglob("*.py"):
            if source_file.is_symlink():
                raise ValueError(f"拒绝复制符号链接：{source_file.relative_to(source_root)}")
            safe_file = _safe_source_path(source_root, source_file)
            relative_path = safe_file.relative_to(source_root)
            target_file = destination / relative_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_file, target_file)


def build_sandbox_command(operation: str, workspace: Path) -> list[str]:
    """构造不使用 Shell 的固定 Docker Compose 命令，仅允许受限操作和只读临时挂载。"""
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(f"不允许的沙箱操作：{operation}")
    workspace = workspace.resolve(strict=True)
    volume = f"{workspace}:/workspace:ro"
    return [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--profile",
        "sandbox",
        "run",
        "--rm",
        "--no-deps",
        "--volume",
        volume,
        "python-sandbox",
        operation,
    ]


def run_sandbox(operation: str, source_root: Path = PROJECT_ROOT) -> int:
    """在自动清理的临时白名单工作区中运行一次 Docker 沙箱任务。"""
    with tempfile.TemporaryDirectory(prefix="rail_rag-sandbox-") as temporary_directory:
        workspace = Path(temporary_directory)
        stage_project_workspace(source_root, workspace)
        completed = subprocess.run(
            build_sandbox_command(operation, workspace),
            cwd=PROJECT_ROOT,
            check=False,
        )
        return completed.returncode


def main() -> int:
    """解析 analyze 或 test 操作；调用方不能传入容器命令、挂载路径或源文件清单。"""
    parser = argparse.ArgumentParser(description="运行受控 Python 代码分析沙箱")
    parser.add_argument("operation", choices=ALLOWED_OPERATIONS)
    arguments = parser.parse_args()
    return run_sandbox(arguments.operation)


def _safe_source_path(source_root: Path, path: Path) -> Path:
    """解析路径并确保其始终位于授权项目根目录内，拒绝路径穿越。"""
    resolved_path = path.resolve(strict=True)
    try:
        resolved_path.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(f"拒绝访问授权范围外的路径：{path}") from exc
    return resolved_path


if __name__ == "__main__":
    raise SystemExit(main())
