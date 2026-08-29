#!/usr/bin/env python3
"""MolManager 可选 HTTP 接口层（FastAPI）。

安装：``pip install -e ".[api]"``
启动：``uvicorn api.server:app --reload --port 8000``

本包遵循**惰性导出**：没装 FastAPI 时 ``import api`` 依然成功，
只有在真正取用 ``api.app`` 时才抛出带安装指引的错误。这样 CLI / GUI 里
做能力探测（``importlib.util.find_spec("api")``）不会被可选依赖绊倒。
"""

from __future__ import annotations

from typing import Any

__all__ = ["app", "create_app", "is_available"]


def is_available() -> bool:
    """FastAPI 是否已安装。"""
    import importlib.util

    try:
        return importlib.util.find_spec("fastapi") is not None
    except (ImportError, ValueError):
        return False


def __getattr__(name: str) -> Any:
    """惰性导出 ``app`` / ``create_app`` —— 缺依赖时给出可操作的报错。"""
    if name in ("app", "create_app"):
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
