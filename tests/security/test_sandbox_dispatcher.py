"""受控沙箱调度器测试：验证白名单暂存不复制密钥或非 Python 文件。

定义 sandbox_dispatcher 加载器及两项测试，分别验证文件白名单与 Docker 命令的只读挂载约束。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_dispatcher():
    path = Path(__file__).parents[2] / "infra" / "agent_sandbox" / "sandbox_dispatcher.py"
    spec = importlib.util.spec_from_file_location("sandbox_dispatcher_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sandbox_dispatcher = _load_dispatcher()


def test_stage_project_workspace_copies_only_python_whitelist(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination = tmp_path / "stage"
    (source_root / "src" / "package").mkdir(parents=True)
    (source_root / "tests").mkdir()
    (source_root / "infra" / "agent_sandbox").mkdir(parents=True)
    (source_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n")
    (source_root / "infra" / "agent_sandbox" / "sandbox_dispatcher.py").write_text(
        "value = 1\n"
    )
    (source_root / "src" / "package" / "service.py").write_text("value = 1\n")
    (source_root / "src" / "package" / "secret.txt").write_text("must not copy\n")
    (source_root / "tests" / "test_service.py").write_text("def test_ok():\n    pass\n")
    (source_root / ".env").write_text("API_KEY=secret\n")

    sandbox_dispatcher.stage_project_workspace(source_root, destination)

    assert (destination / "pyproject.toml").is_file()
    assert (destination / "infra" / "agent_sandbox" / "sandbox_dispatcher.py").is_file()
    assert (destination / "src" / "package" / "service.py").is_file()
    assert (destination / "tests" / "test_service.py").is_file()
    assert not (destination / ".env").exists()
    assert not (destination / "src" / "package" / "secret.txt").exists()


def test_build_sandbox_command_uses_only_read_only_staged_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "stage"
    workspace.mkdir()

    command = sandbox_dispatcher.build_sandbox_command("analyze", workspace)

    assert command[-2:] == ["python-sandbox", "analyze"]
    assert "--rm" in command
    assert "--no-deps" in command
    volume_index = command.index("--volume") + 1
    assert command[volume_index].endswith(":/workspace:ro")
