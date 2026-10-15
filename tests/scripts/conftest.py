"""评测脚本测试夹具：按文件路径加载 scripts/ 下的独立脚本。

定义 load_script 夹具，供测试在不把 scripts 变成包的前提下导入脚本模块。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def load_script() -> Callable[[str], Any]:
    """返回按脚本文件名加载模块的函数。"""

    def _load(name: str) -> Any:
        path = ROOT / "scripts" / name
        spec = importlib.util.spec_from_file_location(f"eval_script_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载脚本：{path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load
