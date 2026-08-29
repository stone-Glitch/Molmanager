#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""utils/chem_query.py —— 化学感知搜索的纯逻辑层。

不依赖文件系统 / OpenBabel，因此是 CI 里最该优先跑通的一块。
"""

from __future__ import annotations

import pytest

from utils.chem_query import (
    _split_operator,
    filter_entries,
    looks_like_chem_query,
    match_entry,
    matches_free_text,
    parse_chem_query,
)


# ---------------------------------------------------------------- 查询串解析
@pytest.mark.parametrize(
    "token,expected",
    [
        ("mw:>60", ("mw", ">", "60")),
        ("mw:>=60", ("mw", ">=", "60")),
        ("logP:<3", ("logp", "<", "3")),
        ("formula:C6H6", ("formula", ":", "C6H6")),
        ("heavy:=10", ("heavy", "=", "10")),
        ("MW:>60", ("mw", ">", "60")),          # 键不分大小写
        ("MolecularWeight:>60", ("molecularweight", ">", "60")),
        ("xlogp:<0.5", ("xlogp", "<", "0.5")),
    ],
)
def test_split_operator_recognises(token: str, expected: tuple[str, str, str]) -> None:
    assert _split_operator(token) == expected


@pytest.mark.parametrize(
    "token",
    [
        "benzene",          # 无冒号 → 自由文本
        "unknown:>10",      # 未知键 → 自由文本
        "mw:",              # 空值 → 自由文本
        ">",                # 垃圾输入
        "",
    ],
)
def test_split_operator_rejects(token: str) -> None:
    assert _split_operator(token) == (None, None, None)


def test_parse_mixes_conditions_and_free_text() -> None:
    conds, free = parse_chem_query("mw:>60 芳香 logP:<3")
    assert [c.key for c in conds] == ["mw", "logp"]
    assert free == ["芳香"]


def test_parse_empty_query() -> None:
    assert parse_chem_query("") == ([], [])
    assert parse_chem_query("   ") == ([], [])


def test_looks_like_chem_query() -> None:
    assert looks_like_chem_query("mw:>60") is True
    assert looks_like_chem_query("benzene mw:>60") is True
    assert looks_like_chem_query("benzene") is False
    assert looks_like_chem_query("") is False


# ---------------------------------------------------------------- 条目匹配
ENTRIES = [
    {"name": "benzene.mol", "base": "benzene", "mw": 78.11, "formula": "C6H6", "logP": 2.13},
    {"name": "aspirin.mol", "base": "aspirin", "mw": 180.16, "formula": "C9H8O4", "logP": 1.19},
    {"name": "ethanol.mol", "base": "ethanol", "mw": 46.07, "formula": "C2H6O", "logP": -0.18},
    # 描述符缺失的条目：任何针对缺失字段的条件都必须判 False（红线：不造假阳性）
    {"name": "unknown.mol", "base": "unknown"},
]


def test_filter_numeric_greater_than() -> None:
    out = filter_entries(ENTRIES, "mw:>100")
    assert [e["name"] for e in out] == ["aspirin.mol"]


def test_filter_missing_field_never_matches() -> None:
    """缺失 mw 的条目不能被 mw:>0 或 mw:<999 命中。"""
    assert "unknown.mol" not in [e["name"] for e in filter_entries(ENTRIES, "mw:>0")]
    assert "unknown.mol" not in [e["name"] for e in filter_entries(ENTRIES, "mw:<999")]


def test_filter_alias_molecular_weight() -> None:
    """molecular_weight 是描述符里的真实字段名，应被 mw 条件识别。"""
    entries = [{"name": "x", "molecular_weight": 250.0}]
    assert len(filter_entries(entries, "mw:>200")) == 1


def test_filter_field_name_is_case_insensitive() -> None:
    """UI / 导出 CSV 常把字段写成 MW、LogP，照着表头输入也必须能查到。"""
    entries = [
        {"name": "upper", "MW": 250.0},
        {"name": "lower", "mw": 10.0},
        {"name": "mixed", "LogP": 3.5},
    ]
    assert [e["name"] for e in filter_entries(entries, "mw:>200")] == ["upper", ]
    assert [e["name"] for e in filter_entries(entries, "logp:>3")] == ["mixed"]


def test_filter_still_rejects_missing_field() -> None:
    """放宽的是「字段名写法」，不是「字段缺失」——缺字段依然不匹配。"""
    entries = [{"name": "x", "formula": "C6H6"}]
    assert filter_entries(entries, "mw:>0") == []


def test_filter_formula_substring_is_case_insensitive() -> None:
    out = filter_entries(ENTRIES, "formula:c6h6")
    assert [e["name"] for e in out] == ["benzene.mol"]


def test_filter_conditions_are_anded() -> None:
    out = filter_entries(ENTRIES, "mw:>40 logP:<0")
    assert [e["name"] for e in out] == ["ethanol.mol"]


def test_filter_free_text_matches_name_base_eng_chn() -> None:
    entries = [{"name": "a.mol", "eng": "benzene", "chn": "苯"}]
    assert len(filter_entries(entries, "ben")) == 1
    assert len(filter_entries(entries, "苯")) == 1
    assert len(filter_entries(entries, "zzz")) == 0


def test_filter_empty_query_returns_all() -> None:
    assert filter_entries(ENTRIES, "") == ENTRIES


def test_match_entry_without_conditions_is_true() -> None:
    assert match_entry({"name": "x"}, []) is True


def test_matches_free_text_without_terms_is_true() -> None:
    assert matches_free_text({"name": "x"}, []) is True


def test_filter_does_not_mutate_input() -> None:
    snapshot = [dict(e) for e in ENTRIES]
    filter_entries(ENTRIES, "mw:>100")
    assert ENTRIES == snapshot
