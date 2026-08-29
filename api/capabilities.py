#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后端能力探测：OpenBabel / PSI4 是否可用。

设计要点：
  1. **导入永不抛异常** —— 没装 OpenBabel 时返回全 False，让 ``/health`` 能如实汇报，
     而不是让整个 API 进程起不来；
  2. **结果缓存** —— 探测涉及子进程调用（``obabel --version``），只在首次与
     显式 ``refresh()`` 时执行；
  3. **不 import chem.psi4** —— PSI4 导入代价高（数秒）且可能污染 CWD，
     这里只用 ``importlib.util.find_spec`` 做轻量探测。
"""

from __future__ import annotations

import importlib.util
import shutil
from typing import Any

_CACHE: dict[str, Any] | None = None


def _probe_openbabel() -> tuple[bool, bool, str | None]:
    """返回 (openbabel 可用, pybel 可用, 版本号)。"""
    version: str | None = None
    try:
        import openbabel as ob  # type: ignore[import-untyped]

        ob_ok = True
        try:
            version = str(ob.OBReleaseVersion()).strip()
        except Exception:
            version = None
    except Exception:
        ob_ok = False

    pybel_ok = False
    if ob_ok:
        try:
            import openbabel.pybel  # noqa: F401  # type: ignore[import-untyped]

            pybel_ok = True
        except Exception:
            # 旧版可能是顶层 pybel 模块
            try:
                import pybel  # noqa: F401  # type: ignore[import-untyped]

                pybel_ok = True
            except Exception:
                pybel_ok = False
    return ob_ok, pybel_ok, version


def _probe_psi4() -> tuple[bool, str | None]:
    """轻量探测 PSI4：只看 spec 能否找到，不真正导入（导入很贵）。"""
    try:
        spec = importlib.util.find_spec("psi4")
    except (ImportError, ValueError):
        return False, None
    if spec is None:
        return False, None
    # 真导入拿版本号代价高（~数秒），只在能拿到时尽力而为
    version = None
    try:
        import psi4  # type: ignore[import-untyped]

        version = str(getattr(psi4, "__version__", "") or "")
    except Exception:
        version = None
    return True, (version or None)


def _probe_obabel_cli() -> str | None:
    """定位 obabel 可执行文件，找不到返回 None。"""
    return shutil.which("obabel")


def detect(refresh: bool = False) -> dict[str, Any]:
    """返回能力字典；``refresh=True`` 时强制重新探测。"""
    global _CACHE
    if _CACHE is not None and not refresh:
        return dict(_CACHE)

    ob_ok, pybel_ok, ob_version = _probe_openbabel()
    psi4_ok, psi4_version = _probe_psi4()

    # OpenBabel Python 绑定缺失时，再看看命令行 obabel 在不在（功能降级但仍可用）
    cli = None
    if not pybel_ok:
        cli = _probe_obabel_cli()

    _CACHE = {
        "openbabel": ob_ok,
        "pybel": pybel_ok,
        "obabel_cli": cli,
        "psi4": psi4_ok,
        "psi4_version": psi4_version,
        "openbabel_version": ob_version,
    }
    return dict(_CACHE)


def has_chem_backend() -> bool:
    """是否至少具备一种化学计算后端（Python 绑定或命令行 obabel）。"""
    caps = detect()
    return bool(caps.get("pybel") or caps.get("obabel_cli"))


__all__ = ["detect", "has_chem_backend"]
