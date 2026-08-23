#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U-10 示例分子库 + 失败案例教育（纯数据/逻辑层）

为新手提供一组「开箱即用」的示例分子（含 SMILES/分子式/分类），
以及一组「常见失败案例」教学条目，供 UI 在欢迎/向导页展示，
降低上手门槛。

纯数据 + 查找函数，无 tkinter 依赖，可在沙箱单测。
"""
from typing import Dict, List, Optional


# 示例分子：覆盖有机小分子的几类典型结构
EXAMPLE_MOLECULES: List[Dict] = [
    {"name": "水", "english": "water", "formula": "H2O", "smiles": "O",
     "category": "无机", "note": "最简单的极性分子，单点能基准测试常用。"},
    {"name": "甲醇", "english": "methanol", "formula": "CH4O", "smiles": "CO",
     "category": "醇", "note": "含 O-H 氢键供体，适合演示 pKa/溶剂化。"},
    {"name": "乙醇", "english": "ethanol", "formula": "C2H6O", "smiles": "CCO",
     "category": "醇", "note": "MW≈46，常用作 SMILES 解析自测样本。"},
    {"name": "苯", "english": "benzene", "formula": "C6H6", "smiles": "c1ccccc1",
     "category": "芳香烃", "note": "刚性无转子分子，构象搜索应得 rotor_free=True。"},
    {"name": "乙酸", "english": "aceticacid", "formula": "C2H4O2", "smiles": "CC(=O)O",
     "category": "羧酸", "note": "可演示 pKa 热力学循环（HA 与 A- 必须成对）。"},
    {"name": "咖啡因", "english": "caffeine", "formula": "C8H10N4O2",
     "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "category": "杂环生物碱",
     "note": "多环柔性分子，构象多样性演示佳。"},
    {"name": "葡萄糖", "english": "glucose", "formula": "C6H12O6",
     "smiles": "OC[C@H]1O[C@@H](O)[C@H](O)[C@@H](O)[C@H](O)[C@H]1O",
     "category": "糖类", "note": "含手性中心，注意立体化学 SMILES。"},
]

# 失败案例教育：把「新手常踩的坑」结构化，UI 可逐条弹提示
FAILURE_CASES: List[Dict] = [
    {"title": "把 .log 当结构文件去重命名",
     "why": ".log/.out 是计算结果，不是分子结构；用 M-01 的 STRUCTURE_EXTS 区分。"},
    {"title": "中文名映射到两个不同英文名",
     "why": "反向映射会静默塌缩丢数据——S-06 已加冲突检测并红色告警。"},
    {"title": "PCM 溶剂计算被自动回退到气相",
     "why": "溶剂模型不支持时 psi4 会回退气相，S-04/05 已加醒目红字警示，别误当溶剂结果。"},
    {"title": "NMR 化学位移凭空出现",
     "why": "CPHF 不可用时旧版会编造经验位移；S-01 已改为直接拒绝，不会再有假谱。"},
    {"title": "IRC 解析出 0 帧仍画出反应路径",
     "why": "IRC 无收敛帧时旧版注入 TS 几何造假；S-03 已改为显式报错。"},
]


def get_examples(category: Optional[str] = None) -> List[Dict]:
    """返回示例分子；category 非空时按分类过滤（大小写不敏感）。"""
    if not category:
        return list(EXAMPLE_MOLECULES)
    cat = category.strip().lower()
    return [m for m in EXAMPLE_MOLECULES if m.get("category", "").lower() == cat]


def lookup_example(name: str) -> Optional[Dict]:
    """按中文名或英文名精确（大小写不敏感）查找一个示例分子。"""
    if not name:
        return None
    key = name.strip().lower()
    for m in EXAMPLE_MOLECULES:
        if m["name"].lower() == key or m["english"].lower() == key:
            return m
    return None


def get_failure_cases() -> List[Dict]:
    return list(FAILURE_CASES)


def categories() -> List[str]:
    seen = []
    for m in EXAMPLE_MOLECULES:
        c = m.get("category", "")
        if c and c not in seen:
            seen.append(c)
    return seen


__all__ = ["EXAMPLE_MOLECULES", "FAILURE_CASES",
           "get_examples", "lookup_example", "get_failure_cases", "categories"]
