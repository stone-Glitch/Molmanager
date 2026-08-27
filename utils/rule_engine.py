#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-03 智能规则引擎（if-this-then-that）· 纯逻辑层

用声明式规则对「文件条目」做条件匹配，并输出一组待执行的动作描述
（flag/rename/move/tag/notify）。本模块**只做求值与动作规划**，不实际
改动文件系统——真正的执行由 controller/model 消费动作描述后落盘，
从而保持引擎可单测、可重放、零副作用。

规则结构（JSON 可序列化）：
    {
      "id": "r1",
      "name": "大结构文件标记待复核",
      "enabled": true,
      "when": {
        "all": [
          {"field": "ext", "op": "in", "value": [".xyz", ".mol"]},
          {"field": "size_kb", "op": "gt", "value": 100}
        ]
      },
      "then": {"action": "flag", "target": "status", "label": "review"}
    }

条件组支持嵌套 all/any；字段路径支持点号（如 "descriptors.molecular_weight"）。
红线：字段缺失时，比较类条件一律 False（绝不把缺失当命中）。
"""
import re
from typing import Any


# 操作符别名 → 规范名
_OPS = {
    "eq": "eq", "==": "eq", "=": "eq",
    "ne": "ne", "!=": "ne",
    "gt": "gt", ">": "gt",
    "gte": "gte", ">=": "gte",
    "lt": "lt", "<": "lt",
    "lte": "lte", "<=": "lte",
    "in": "in",
    "not_in": "not_in", "notin": "not_in",
    "contains": "contains", "not_contains": "not_contains",
    "startswith": "startswith", "endswith": "endswith",
    "exists": "exists", "missing": "missing",
    "matches": "matches", "regex": "matches",
}

_NUMERIC_OPS = {"gt", "gte", "lt", "lte", "eq", "ne"}
_STRING_OPS = {"contains", "not_contains", "startswith", "endswith", "matches"}
_COLLECTION_OPS = {"in", "not_in"}
_PRESENCE_OPS = {"exists", "missing"}


def normalize_op(op: Any) -> str | None:
    """别名 → 规范操作符；未知返回 None。"""
    if isinstance(op, str):
        return _OPS.get(op.strip().lower())
    return None


def _resolve(entry: dict[str, Any], field: str) -> Any:
    """按点号路径取值；中间缺失返回 None。"""
    cur: Any = entry
    for seg in str(field).split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return None
    return cur


def _coerce_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _match_single(cond: dict[str, Any], entry: dict[str, Any]) -> bool:
    op = normalize_op(cond.get("op"))
    if op is None:
        return False  # 未知操作符：不命中，宁漏勿错
    field = cond.get("field")
    val = _resolve(entry, field)
    expected = cond.get("value")

    if op in _PRESENCE_OPS:
        if op == "exists":
            return val is not None
        return val is None

    if val is None:
        return False  # 字段缺失：任何需要值的比较都 False

    if op in _COLLECTION_OPS:
        coll = expected if isinstance(expected, (list, tuple, set, frozenset)) else [expected]
        hit = val in coll
        return hit if op == "in" else not hit

    if op in _STRING_OPS:
        sval = str(val)
        sval_l = sval.lower()
        if op == "contains":
            return str(expected).lower() in sval_l
        if op == "not_contains":
            return str(expected).lower() not in sval_l
        if op == "startswith":
            return sval_l.startswith(str(expected).lower())
        if op == "endswith":
            return sval_l.endswith(str(expected).lower())
        if op == "matches":
            try:
                return re.search(str(expected), sval) is not None
            except re.error:
                return False

    # 数值比较：能转数字就数值比，否则字符串比
    nval = _coerce_number(val)
    nexp = _coerce_number(expected)
    if nval is not None and nexp is not None:
        if op == "eq":
            return nval == nexp
        if op == "ne":
            return nval != nexp
        if op == "gt":
            return nval > nexp
        if op == "gte":
            return nval >= nexp
        if op == "lt":
            return nval < nexp
        if op == "lte":
            return nval <= nexp
    # 字符串比较兜底
    sval, sexp = str(val), str(expected)
    if op == "eq":
        return sval.lower() == sexp.lower()
    if op == "ne":
        return sval.lower() != sexp.lower()
    return False


def match_condition(when: Any, entry: dict[str, Any]) -> bool:
    """递归求值条件组（dict 可含 all/any 或直接是单条件）。"""
    if not isinstance(when, dict):
        return False
    if "all" in when:
        subs = when["all"]
        return bool(subs) and all(match_condition(s, entry) for s in subs)
    if "any" in when:
        subs = when["any"]
        return any(match_condition(s, entry) for s in subs)
    return _match_single(when, entry)


def match_rule(rule: dict[str, Any], entry: dict[str, Any]) -> bool:
    """单条规则是否命中（enabled=False 直接不命中）。"""
    if not isinstance(rule, dict):
        return False
    if rule.get("enabled") is False:
        return False
    when = rule.get("when")
    return match_condition(when, entry) if when is not None else False


def evaluate_rules(rules: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """返回命中的规则列表（保持输入顺序）。"""
    return [r for r in rules if match_rule(r, entry)]


def render_actions(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把命中规则的动作展开成可执行动作描述列表。"""
    out: list[dict[str, Any]] = []
    for r in matched:
        then = r.get("then") or {}
        action = then.get("action", "notify")
        out.append({
            "rule_id": r.get("id"),
            "rule_name": r.get("name", ""),
            "action": action,
            "target": then.get("target"),
            "params": then.get("params") or then,
        })
    return out


def validate_rule(rule: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验规则结构，返回 (ok, 错误列表)。"""
    errs: list[str] = []
    if not isinstance(rule, dict):
        return False, ["规则必须是对象"]
    if not rule.get("id"):
        errs.append("缺少 id")
    when = rule.get("when")
    if when is None:
        errs.append("缺少 when 条件")
    elif not isinstance(when, dict):
        errs.append("when 必须是对象")

    def _check(c):
        if not isinstance(c, dict):
            return ["条件必须是对象"]
        if "all" in c:
            return [e for s in c["all"] for e in _check(s)]
        if "any" in c:
            return [e for s in c["any"] for e in _check(s)]
        if not c.get("field"):
            return ["条件缺少 field"]
        if normalize_op(c.get("op")) is None:
            return [f"未知操作符: {c.get('op')}"]
        return []

    if isinstance(when, dict):
        errs.extend(_check(when))
    return (len(errs) == 0), errs


def load_rules(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """解析 JSON 规则文本（数组或单对象），返回 (规则列表, 错误列表)。"""
    import json
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return [], [f"JSON 解析失败: {e}"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return [], ["规则顶层必须是数组或对象"]
    rules, errs = [], []
    for i, r in enumerate(data):
        ok, es = validate_rule(r)
        if ok:
            rules.append(r)
        else:
            errs.append(f"第 {i + 1} 条: " + "; ".join(es))
    return rules, errs


__all__ = ["normalize_op", "match_condition", "match_rule", "evaluate_rules",
           "render_actions", "validate_rule", "load_rules", "_resolve"]
