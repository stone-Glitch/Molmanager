#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""utils/version.py —— 版本号解析与比较（无第三方依赖，纯 Python）。"""

from __future__ import annotations

from utils.version import (
    __version__,
    compare_versions,
    get_full_version,
    get_user_agent,
    get_version,
    is_newer,
    normalize_version,
    parse_version,
)


def test_version_constant_is_semver() -> None:
    assert get_version() == __version__
    parts = __version__.split(".")
    assert len(parts) == 3, f"版本号应为 x.y.z 形式，实际：{__version__}"
    assert all(p.isdigit() for p in parts)


def test_full_version_includes_build_tag_only_when_present() -> None:
    # BUILD_TAG 为空时不应出现尾部的 "+"
    assert get_full_version() == __version__ or get_full_version().startswith(__version__ + "+")


def test_user_agent_contains_app_name() -> None:
    assert "MolManager" in get_user_agent()


# ---------------------------------------------------------------- 规范化
def test_normalize_version() -> None:
    assert normalize_version("v1.2.3") == "1.2.3"
    assert normalize_version("release-1.2.3") == "1.2.3"
    assert normalize_version("  1.2  ") == "1.2"
    assert normalize_version("1.2.3-beta.1") == "1.2.3-beta.1"


def test_normalize_version_garbage_returns_empty() -> None:
    assert normalize_version(None) == ""
    assert normalize_version("") == ""
    assert normalize_version("   ") == ""
    assert normalize_version("no-digits-here") == ""


# ---------------------------------------------------------------- 解析
def test_parse_version() -> None:
    assert parse_version("1.2.3") == (1, 2, 3)
    # 预发布后缀应被剥离
    assert parse_version("1.2.3-beta.1") == (1, 2, 3)
    assert parse_version("v2.0") == (2, 0)


def test_parse_version_returns_none_on_garbage() -> None:
    assert parse_version("abc") is None
    assert parse_version(None) is None


# ---------------------------------------------------------------- 比较
def test_compare_versions_numeric_not_lexicographic() -> None:
    """1.10 必须大于 1.9 —— 字符串比较会得出相反结论。"""
    assert compare_versions("1.10.0", "1.9.0") == 1
    assert compare_versions("1.9.0", "1.10.0") == -1
    assert compare_versions("1.0.0", "1.0.0") == 0


def test_compare_versions_pads_missing_segments() -> None:
    assert compare_versions("1.2", "1.2.0") == 0
    assert compare_versions("1.2.1", "1.2") == 1


def test_compare_versions_never_raises() -> None:
    """契约：任何垃圾输入都退化为 0，绝不让「检查更新」崩掉主程序。"""
    for left, right in [("abc", "1.0"), (None, None), ("", ""), ("###", "1.2.3")]:
        assert compare_versions(left, right) in (-1, 0, 1)


def test_compare_versions_single_side_parseable() -> None:
    assert compare_versions("garbage", "1.0.0") == -1
    assert compare_versions("1.0.0", "garbage") == 1


# ---------------------------------------------------------------- is_newer
def test_is_newer_against_current_version() -> None:
    assert is_newer("999.0.0") is True
    assert is_newer("0.0.1") is False
    assert is_newer(__version__) is False  # 等于当前版本不算新


def test_is_newer_with_explicit_local() -> None:
    assert is_newer("2.0", "1.0") is True
    assert is_newer("1.0", "2.0") is False


def test_is_newer_never_raises() -> None:
    assert is_newer("not-a-version") is False
    assert is_newer(None) is False
