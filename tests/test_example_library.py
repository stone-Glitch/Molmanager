#!/usr/bin/env python3
"""utils/example_library —— 示例分子库/失败案例教学测试（纯数据）。"""

from utils.example_library import (
    EXAMPLE_MOLECULES,
    FAILURE_CASES,
    categories,
    get_examples,
    get_failure_cases,
    lookup_example,
)


def test_data_integrity() -> None:
    assert len(EXAMPLE_MOLECULES) >= 5  # 至少 5 个示例
    for m in EXAMPLE_MOLECULES:
        for key in ("name", "english", "formula", "smiles", "category", "note"):
            assert m.get(key), f"示例缺字段 {key}: {m}"
        assert m["smiles"].strip() == m["smiles"]  # SMILES 无首尾空白


def test_get_examples_filter_case_insensitive() -> None:
    assert get_examples() == EXAMPLE_MOLECULES
    alcohols = get_examples("醇")
    assert len(alcohols) >= 2 and all(m["category"] == "醇" for m in alcohols)
    assert get_examples("  醇  ") == alcohols  # 带空白
    assert get_examples("不存在的分类") == []


def test_lookup_example() -> None:
    assert lookup_example("水")["smiles"] == "O"
    assert lookup_example("WATER")["formula"] == "H2O"  # 英文大小写不敏感
    assert lookup_example("  苯 ")["formula"] == "C6H6"
    assert lookup_example("不存在的分子") is None
    assert lookup_example("") is None
    assert lookup_example(None) is None  # type: ignore[arg-type]


def test_categories_dedup_preserve_order() -> None:
    cats = categories()
    assert len(cats) == len(set(cats))  # 去重
    assert cats  # 非空


def test_failure_cases_structure() -> None:
    cases = get_failure_cases()
    assert len(cases) >= 3
    for c in cases:
        assert c.get("title") and c.get("why")
    # 返回副本：外部修改不影响内部数据
    cases.append({"title": "污染", "why": "不该生效"})
    assert len(get_failure_cases()) == len(FAILURE_CASES)
