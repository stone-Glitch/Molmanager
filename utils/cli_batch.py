#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-08 CLI 无头模式（--batch --fix-all）· 纯逻辑规划层

解析命令行参数，产出**有序的操作计划**（plan），供无 GUI 环境批量执行。
本模块只做「参数 → 操作列表」的纯映射，不实际改文件/映射——真正的执行
由调用方（controller/model）消费 plan 后完成，从而可脱离 GUI 单测。

用法示例：
    python -m utils.cli_batch --batch --work-dir output --fix-all --dry-run
"""
import argparse
from typing import Any


# 操作定义：按「安全顺序」排列（扫描→整理→重命名→修中文→生成缺失→导出）
OPERATION_ORDER = [
    "scan", "organize", "rename", "fix_chinese", "generate_missing", "export_mapping",
]

OPERATION_LABELS = {
    "scan": "扫描文件",
    "organize": "按类型整理",
    "rename": "按映射重命名",
    "fix_chinese": "修正中文名",
    "generate_missing": "生成缺失映射",
    "export_mapping": "导出映射表",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="molmanager", description="MolManager 无头批处理")
    p.add_argument("--batch", action="store_true", help="无头模式")
    p.add_argument("--work-dir", default=None, help="工作目录")
    p.add_argument("--fix-all", action="store_true", help="执行全部整理/重命名/修中文/生成缺失")
    p.add_argument("--rename", action="store_true", help="按映射重命名")
    p.add_argument("--organize", action="store_true", help="按类型整理")
    p.add_argument("--fix-chinese", action="store_true", help="修正中文名")
    p.add_argument("--generate-missing", action="store_true", help="生成缺失映射")
    p.add_argument("--export-mapping", default=None, help="导出映射到指定文件")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    return p.parse_args(argv)


def _pick(opts: argparse.Namespace) -> dict[str, dict[str, Any]]:
    """根据参数挑选要执行的操作，返回 {name: op_spec}。"""
    plan: dict[str, dict[str, Any]] = {}

    if getattr(opts, "fix_all", False):
        for name in OPERATION_ORDER:
            if name == "export_mapping":
                continue
            plan[name] = {"name": name, "label": OPERATION_LABELS[name], "args": {}}
    if getattr(opts, "organize", False):
        plan["organize"] = {"name": "organize", "label": OPERATION_LABELS["organize"], "args": {}}
    if getattr(opts, "rename", False):
        plan["rename"] = {"name": "rename", "label": OPERATION_LABELS["rename"], "args": {}}
    if getattr(opts, "fix_chinese", False):
        plan["fix_chinese"] = {"name": "fix_chinese", "label": OPERATION_LABELS["fix_chinese"], "args": {}}
    if getattr(opts, "generate_missing", False):
        plan["generate_missing"] = {"name": "generate_missing", "label": OPERATION_LABELS["generate_missing"], "args": {}}
    export = getattr(opts, "export_mapping", None)
    if export:
        plan["export_mapping"] = {"name": "export_mapping",
                                  "label": OPERATION_LABELS["export_mapping"],
                                  "args": {"path": export}}
    return plan


def build_batch_plan(opts: argparse.Namespace) -> list[dict[str, Any]]:
    """把参数归一成有序操作列表（按 OPERATION_ORDER 排序，去重）。"""
    picked = _pick(opts)
    ordered = []
    for name in OPERATION_ORDER:
        if name in picked:
            ordered.append(picked[name])
    return ordered


def plan_summary(plan: list[dict[str, Any]], dry_run: bool = False) -> str:
    """把计划渲染成可打印摘要。"""
    if not plan:
        return "无操作（未指定任何 --fix-all/--rename/... 参数）。"
    lines = ["计划执行（" + ("dry-run，不落地" if dry_run else "实际执行") + "）："]
    for i, op in enumerate(plan, 1):
        extra = ""
        if op.get("args"):
            extra = " " + " ".join(f"{k}={v}" for k, v in op["args"].items())
        lines.append(f"  {i}. {op['label']}{extra}")
    return "\n".join(lines)


__all__ = ["parse_args", "build_batch_plan", "plan_summary",
           "OPERATION_ORDER", "OPERATION_LABELS"]
