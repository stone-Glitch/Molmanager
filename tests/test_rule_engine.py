#!/usr/bin/env python3
"""utils/rule_engine —— 智能规则引擎纯逻辑测试。

红线守卫：字段缺失时比较类条件一律 False（绝不把缺失当命中）。
"""

from utils.rule_engine import (
    evaluate_rules,
    load_rules,
    match_condition,
    match_rule,
    normalize_op,
    render_actions,
    validate_rule,
)


# ------------------------------------------------ 操作符归一
def test_normalize_op_aliases() -> None:
    assert normalize_op(">") == "gt"
    assert normalize_op(">=") == "gte"
    assert normalize_op("==") == "eq"
    assert normalize_op("regex") == "matches"
    assert normalize_op("notin") == "not_in"
    assert normalize_op(" GT ") == "gt"  # 大小写/空白
    assert normalize_op("no_such_op") is None
    assert normalize_op(123) is None  # 非字符串


# ------------------------------------------------ 单条件
def test_single_condition_numeric() -> None:
    entry = {"size_kb": 250}
    assert match_condition({"field": "size_kb", "op": "gt", "value": 100}, entry)
    assert match_condition({"field": "size_kb", "op": ">", "value": 100}, entry)  # 别名
    assert not match_condition({"field": "size_kb", "op": "lt", "value": 100}, entry)
    # 字符串数字可转数值比较
    assert match_condition({"field": "size_kb", "op": "gte", "value": 250}, {"size_kb": "250"})


def test_single_condition_string() -> None:
    entry = {"name": "Molecule_Final_v2.OPT.log"}
    assert match_condition({"field": "name", "op": "contains", "value": "final"}, entry)  # 忽略大小写
    assert match_condition({"field": "name", "op": "endswith", "value": ".LOG"}, entry)
    assert match_condition({"field": "name", "op": "startswith", "value": "molecule"}, entry)
    assert match_condition({"field": "name", "op": "matches", "value": r"_v\d+"}, entry)
    assert not match_condition({"field": "name", "op": "matches", "value": "(("}, entry)  # 坏正则→False


def test_single_condition_collection_and_presence() -> None:
    assert match_condition({"field": "ext", "op": "in", "value": [".xyz", ".mol"]}, {"ext": ".xyz"})
    assert match_condition({"field": "ext", "op": "not_in", "value": [".xyz"]}, {"ext": ".mol"})
    assert match_condition({"field": "tag", "op": "exists"}, {"tag": 1})
    assert match_condition({"field": "tag", "op": "missing"}, {})
    assert not match_condition({"field": "tag", "op": "exists"}, {})


def test_missing_field_is_never_a_hit() -> None:
    """红线：字段缺失时一切需要值的比较一律 False，绝不把缺失当命中。"""
    entry = {}
    for op in ("eq", "gt", "in", "contains", "startswith", "matches"):
        assert not match_condition({"field": "ghost", "op": op, "value": "x"}, entry), op


def test_dotted_field_path() -> None:
    entry = {"descriptors": {"molecular_weight": 78.11}}
    assert match_condition({"field": "descriptors.molecular_weight", "op": "lt", "value": 100}, entry)
    assert not match_condition({"field": "descriptors.logP", "op": "gt", "value": 1}, entry)  # 中间缺失


def test_unknown_op_never_hits() -> None:
    assert not match_condition({"field": "a", "op": "approx", "value": 1}, {"a": 1})


# ------------------------------------------------ 条件组嵌套
def test_all_any_groups() -> None:
    entry = {"ext": ".mol", "size_kb": 200}
    allc = {
        "all": [
            {"field": "ext", "op": "in", "value": [".xyz", ".mol"]},
            {"field": "size_kb", "op": "gt", "value": 100},
        ]
    }
    assert match_condition(allc, entry)
    allc["all"].append({"field": "size_kb", "op": "lt", "value": 100})
    assert not match_condition(allc, entry)
    anyc = {"any": [{"field": "ext", "op": "eq", "value": ".xyz"}, {"field": "ext", "op": "eq", "value": ".mol"}]}
    assert match_condition(anyc, entry)
    # 空组语义：all([])=False（宁漏勿错），any([])=False
    assert not match_condition({"all": []}, entry)
    assert not match_condition({"any": []}, entry)
    # 非法 when（非 dict）→ False
    assert not match_condition("not-a-dict", entry)


def test_nested_all_any() -> None:
    when = {
        "all": [
            {"field": "ext", "op": "eq", "value": ".log"},
            {"any": [
                {"field": "size_kb", "op": "gt", "value": 1000},
                {"field": "tag", "op": "eq", "value": "big"},
            ]},
        ]
    }
    assert match_condition(when, {"ext": ".log", "tag": "big"})
    assert match_condition(when, {"ext": ".log", "size_kb": 2000})
    assert not match_condition(when, {"ext": ".log", "size_kb": 5})


# ------------------------------------------------ 规则层
def _rule(**over) -> dict:
    r = {
        "id": "r1",
        "name": "大文件标记",
        "enabled": True,
        "when": {"field": "size_kb", "op": "gt", "value": 100},
        "then": {"action": "flag", "target": "status", "label": "review"},
    }
    r.update(over)
    return r


def test_match_rule_disabled_or_empty() -> None:
    assert match_rule(_rule(), {"size_kb": 500})
    assert not match_rule(_rule(enabled=False), {"size_kb": 500})
    assert not match_rule(_rule(when=None), {"size_kb": 500})
    assert not match_rule("not-a-dict", {"size_kb": 500})


def test_evaluate_rules_keeps_order() -> None:
    r_hit1 = _rule(id="a", when={"field": "size_kb", "op": "gt", "value": 50})
    r_miss = _rule(id="b", when={"field": "size_kb", "op": "gt", "value": 900})
    r_hit2 = _rule(id="c", when={"field": "size_kb", "op": "gt", "value": 10})
    assert [r["id"] for r in evaluate_rules([r_hit1, r_miss, r_hit2], {"size_kb": 100})] == ["a", "c"]


def test_render_actions() -> None:
    acts = render_actions([_rule(id="r1", name="标记", then={"action": "flag", "target": "status", "label": "review"})])
    assert len(acts) == 1
    a = acts[0]
    assert a["rule_id"] == "r1"
    assert a["action"] == "flag"
    assert a["target"] == "status"
    assert a["params"]["label"] == "review"
    # then 缺失 → 动作回退 notify；then 无 params → 整个 then 作 params
    acts2 = render_actions([{"id": "r2", "name": "", "then": None}])
    assert acts2[0]["action"] == "notify"


# ------------------------------------------------ 校验与加载
def test_validate_rule() -> None:
    ok, errs = validate_rule(_rule())
    assert ok and errs == []
    ok, errs = validate_rule({"name": "无 id 无 when"})
    assert not ok and any("id" in e for e in errs) and any("when" in e for e in errs)
    ok, errs = validate_rule(_rule(when={"field": "x", "op": "approx", "value": 1}))
    assert not ok and any("未知操作符" in e for e in errs)
    ok, errs = validate_rule(_rule(when={"field": "", "op": "eq", "value": 1}))
    assert not ok and any("field" in e for e in errs)
    # 嵌套错误可被递归发现
    bad = _rule(when={"all": [{"any": [{"field": "x", "op": "!!!"}]}]})
    ok, errs = validate_rule(bad)
    assert not ok and errs


def test_load_rules() -> None:
    rules, errs = load_rules('[{"id":"r1","when":{"field":"a","op":"eq","value":1}}]')
    assert len(rules) == 1 and errs == []
    # 单对象自动包装
    rules, errs = load_rules('{"id":"r1","when":{"field":"a","op":"eq","value":1}}')
    assert len(rules) == 1
    # 坏 JSON
    rules, errs = load_rules("{oops")
    assert rules == [] and errs and "JSON" in errs[0]
    # 顶层非数组/对象
    rules, errs = load_rules('"just a string"')
    assert rules == [] and errs
    # 第 2 条无效 → 报错并指明序号
    rules, errs = load_rules(
        '[{"id":"r1","when":{"field":"a","op":"eq","value":1}}, {"id":"","when":null}]'
    )
    assert len(rules) == 1 and errs and "第 2 条" in errs[0]
