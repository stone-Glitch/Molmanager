#!/usr/bin/env python3
"""
版本常量与版本号比较（T01 / Phase 1）
────────────────────────────────────
职责：
  1. 提供全局唯一的 ``__version__`` 常量（此前项目无任何版本标识，见架构 C14）；
  2. 提供**不依赖任何第三方库**也能工作的版本号解析 / 比较能力：
     优先使用 ``packaging.version``（C16：mol_manager_312 已装 packaging 26.2），
     不可用时自动降级为内置的元组比较，保证离线 / 精简环境下不崩。

约束（架构 §6）：
  - 本模块**无 Tk 依赖**，可脱离 GUI 单测；
  - 本模块**不 import requests**（网络唯一入口是 utils/net.py，批次二交付）；
  - 本模块**不 import chem.psi4**（PSI4 命名陷阱）。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 常量
APP_NAME: str = "MolManager"
APP_DISPLAY_NAME: str = "分子管理器"

#: 应用语义化版本号。发版时**只改这一处**（pyproject.toml / CHANGELOG.md 跟着对齐）。
__version__: str = "1.0.2"

#: 构建标识（可选，例如 CI 号 / 日期戳）。空串表示未标注。
BUILD_TAG: str = ""

# 宽松版本号提取：允许 "v1.2.3"、"release-1.2.3"、"1.2.3-beta.1" 等形式
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*(?:[-+.][0-9A-Za-z.\-+]+)?)")

# packaging 可用性探测（失败即降级，绝不抛）
try:  # pragma: no cover - 取决于运行环境
    from packaging.version import InvalidVersion as _InvalidVersion
    from packaging.version import Version as _Version

    _HAS_PACKAGING = True
except Exception:  # pragma: no cover
    _Version = None  # type: ignore[assignment]
    _InvalidVersion = Exception  # type: ignore[assignment,misc]
    _HAS_PACKAGING = False


# ---------------------------------------------------------------- 基本查询


def get_version() -> str:
    """返回应用版本号字符串（不含 build tag）。"""
    return __version__


def get_full_version() -> str:
    """返回带 build tag 的完整版本串，例如 ``1.0.0+20260806``。"""
    return f"{__version__}+{BUILD_TAG}" if BUILD_TAG else __version__


def get_user_agent() -> str:
    """返回统一 User-Agent 串，供 utils/net.py（批次二）使用。"""
    return f"{APP_NAME}/{get_full_version()}"


# ---------------------------------------------------------------- 解析 / 比较


def normalize_version(raw: object) -> str:
    """
    把任意来源的版本串规范化为纯数字版本，例如：

        "v1.2.3"          -> "1.2.3"
        "release-1.2.3"   -> "1.2.3"
        "  1.2 "          -> "1.2"
        None / 垃圾输入    -> ""
    """
    if raw is None:
        return ""
    try:
        text = str(raw).strip()
    except Exception:
        return ""
    if not text:
        return ""
    m = _VERSION_RE.search(text)
    return m.group(1) if m else ""


def parse_version(raw: object) -> tuple[int, ...] | None:
    """
    把版本串解析成可比较的整数元组（降级路径专用）。

    仅取前导的数字段，忽略预发布后缀：``"1.2.3-beta.1"`` -> ``(1, 2, 3)``。
    无法解析时返回 ``None``（调用方需判空，本函数不抛异常）。
    """
    norm = normalize_version(raw)
    if not norm:
        return None
    head = re.split(r"[-+]", norm, maxsplit=1)[0]
    parts: list[int] = []
    for seg in head.split("."):
        if not seg.isdigit():
            break
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts) if parts else None


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """把两个版本元组右侧补 0 到等长，便于逐位比较。"""
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def compare_versions(left: object, right: object) -> int:
    """
    比较两个版本号。

    返回:
        -1  left < right
         0  left == right（或两者都无法解析）
         1  left > right

    契约：**永不抛异常**。任何解析失败都退化为 0（视作相等 → 不提示更新）。
    """
    l_norm = normalize_version(left)
    r_norm = normalize_version(right)
    if not l_norm and not r_norm:
        return 0
    if not l_norm:
        return -1
    if not r_norm:
        return 1

    if _HAS_PACKAGING and _Version is not None:
        try:
            lv = _Version(l_norm)
            rv = _Version(r_norm)
            if lv < rv:
                return -1
            if lv > rv:
                return 1
            return 0
        except Exception:
            # InvalidVersion 或其他异常 → 落到下面的元组比较
            pass

    lt = parse_version(l_norm)
    rt = parse_version(r_norm)
    if lt is None and rt is None:
        return 0
    if lt is None:
        return -1
    if rt is None:
        return 1
    lt, rt = _pad(lt, rt)
    if lt < rt:
        return -1
    if lt > rt:
        return 1
    return 0


def is_newer(remote: object, local: object = None) -> bool:
    """
    判断 ``remote`` 是否比 ``local``（默认为当前 ``__version__``）新。

    契约：永不抛异常；无法判定时返回 ``False``（宁可不提示，也不误报更新）。
    """
    base = __version__ if local is None else local
    try:
        return compare_versions(remote, base) > 0
    except Exception:
        return False


__all__ = [
    "APP_NAME",
    "APP_DISPLAY_NAME",
    "BUILD_TAG",
    "__version__",
    "get_version",
    "get_full_version",
    "get_user_agent",
    "normalize_version",
    "parse_version",
    "compare_versions",
    "is_newer",
]
