#!/usr/bin/env python3
"""utils/mapping_utils.py —— 文件名清洗、模糊建议与映射差异对比。"""

from __future__ import annotations

from pathlib import Path

from utils.mapping_utils import (
    clean_filename_stem,
    diff_mappings,
    filter_mapping_rows,
    find_fuzzy_pairs,
    fuzzy_suggestions,
    levenshtein,
)


# ---------------------------------------------------------------- 编辑距离
def test_levenshtein_basics() -> None:
    assert levenshtein("", "") == 0
    assert levenshtein("abc", "abc") == 0
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "") == 3
    assert levenshtein("kitten", "sitting") == 3


def test_levenshtein_single_char_operations() -> None:
    assert levenshtein("乙醇", "乙醚") == 1  # 替换
    assert levenshtein("乙醇", "乙醇醛") == 1  # 插入
    assert levenshtein("乙醇醛", "乙醇") == 1  # 删除


# ---------------------------------------------------------------- 模糊建议
def test_fuzzy_suggestions_sorted_by_distance() -> None:
    out = fuzzy_suggestions("乙醇", ["乙醚", "乙醇醛", "甲醇"])
    # 距离 1 的排在距离 2 的之前
    assert out.index("乙醚") < out.index("甲醇")
    assert "乙醇醛" in out


def test_fuzzy_suggestions_ignores_single_char_names() -> None:
    """单字中文名（苯/水/氧）恒为 1 次替换 —— 必须跳过，否则全是噪声。"""
    assert fuzzy_suggestions("苯", ["水", "氧", "氢"]) == []
    assert fuzzy_suggestions("乙醇", ["苯", "水"]) == []  # 候选为单字也跳过


def test_fuzzy_suggestions_excludes_itself_and_respects_limit() -> None:
    cands = [f"化合物{i}" for i in range(20)]
    out = fuzzy_suggestions("化合物0", cands, max_dist=1, limit=3)
    assert "化合物0" not in out
    assert len(out) <= 3


def test_find_fuzzy_pairs_detects_near_duplicates() -> None:
    # 返回 (name_a, name_b, distance) 三元组
    pairs = find_fuzzy_pairs(["乙醇", "乙醚"], max_dist=1)
    assert pairs
    a, b, dist = pairs[0]
    assert {a, b} == {"乙醇", "乙醚"}
    assert dist == 1


def test_find_fuzzy_pairs_empty_for_distinct_names() -> None:
    assert find_fuzzy_pairs(["乙醇", "阿司匹林"], max_dist=1) == []


# ---------------------------------------------------------------- 行过滤
def test_filter_mapping_rows_matches_both_columns() -> None:
    rows = [("benzene", "苯"), ("ethanol", "乙醇")]
    assert filter_mapping_rows(rows, "BEN") == [("benzene", "苯")]
    assert filter_mapping_rows(rows, "乙醇") == [("ethanol", "乙醇")]


def test_filter_mapping_rows_empty_keyword_returns_all() -> None:
    rows = [("a", "甲"), ("b", "乙")]
    assert filter_mapping_rows(rows, "") == rows
    assert filter_mapping_rows(rows, "  ") == rows


# ---------------------------------------------------------------- 文件名清洗
def test_clean_filename_stem_strips_trailing_qualifiers() -> None:
    assert clean_filename_stem("ethanol_opt") == "ethanol"
    assert clean_filename_stem("benzene_conf_003") == "benzene"
    assert clean_filename_stem("H2O_min_sp") == "H2O"


def test_clean_filename_stem_keeps_multiword_names() -> None:
    assert clean_filename_stem("ethanol_water") == "ethanol_water"


def test_clean_filename_stem_falls_back_to_original() -> None:
    """剥完限定符后若为空，回退原始 stem —— 绝不返回空串。"""
    assert clean_filename_stem("mol_001") == "mol_001"
    assert clean_filename_stem("") == ""
    assert clean_filename_stem("123") == "123"


# ---------------------------------------------------------------- 映射差异
def test_diff_mappings_classifies_changes() -> None:
    old = {"a": "甲", "b": "乙", "c": "丙"}
    new = {"b": "乙改", "c": "丙", "d": "丁"}
    d = diff_mappings(old, new)
    assert d["added"] == {"d": "丁"}
    assert d["changed"] == {"b": ("乙", "乙改")}
    assert d["removed"] == {"a": "甲"}
    assert d["counts"] == {"added": 1, "changed": 1, "removed": 1, "unchanged": 1}


def test_diff_mappings_identical_has_no_changes() -> None:
    m = {"a": "甲"}
    d = diff_mappings(m, dict(m))
    assert d["counts"] == {"added": 0, "changed": 0, "removed": 0, "unchanged": 1}


def test_diff_mappings_empty_old_is_all_added() -> None:
    d = diff_mappings({}, {"a": "甲", "b": "乙"})
    assert d["counts"]["added"] == 2
    assert d["counts"]["removed"] == 0


# ---------------------------------------------------------------- 从目录建议映射
def test_suggest_mapping_from_dir(tmp_path: Path) -> None:
    from utils.mapping_utils import suggest_mapping_from_dir

    (tmp_path / "benzene_opt.log").write_text("", encoding="utf-8")
    (tmp_path / "ethanol_sp.log").write_text("", encoding="utf-8")
    out = suggest_mapping_from_dir(str(tmp_path), existing_english=set(), extensions={".log"})
    names = {k for k, _ in out}
    assert "benzene" in names
    assert "ethanol" in names
