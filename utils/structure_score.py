#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U-16 结构美观度评分 + 课本对照 · 纯逻辑层

对已有描述符做经验启发式打分，输出 0–100 分 + 等级 + 逐条「课本对照」
解释，作为教学辅助（如「为什么苯环比直链烷烃更规则」）。

⚠️ 红线声明：本评分是**经验启发式**，用于教学/可读性，**不构成任何
化学正确性、稳定性或合法性的判定**。项目原则「正确 > 假数据」——
这里绝不把美观度分数包装成科学结论。所有输出都附带免责说明。

输入：描述符 dict（键名对齐 openbabel_utils.calculate_descriptors：
molecular_weight / logP / tpsa / heavy_atoms / bonds / hbd / hba /
rotors / rings / formula / num_atoms）。
"""
from typing import Any, Dict, List, Optional


def _num(d: Dict[str, Any], key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def score_structure(descriptors: Dict[str, Any]) -> Dict[str, Any]:
    """
    返回 {score, grade, notes}。任何描述符缺失都只影响相关项、不影响整体，
    缺失字段在 notes 里如实标注「未提供」，绝不编造数值。
    """
    score = 50.0  # 中性基线
    notes: List[str] = []

    rings = _num(descriptors, "rings")
    rotors = _num(descriptors, "rotors")
    tpsa = _num(descriptors, "tpsa")
    hbd = _num(descriptors, "hbd")
    hba = _num(descriptors, "hba")
    mw = _num(descriptors, "molecular_weight")
    heavy = _num(descriptors, "heavy_atoms")
    bonds = _num(descriptors, "bonds")
    formula = descriptors.get("formula")

    # 1) 环结构：含环结构通常更"规整/对称"，小幅加分
    if rings is not None:
        if rings >= 1:
            score += 8
            notes.append(f"含 {int(rings)} 个环，具备环状骨架（课本对照：芳香/环状化合物往往更对称、构象更受约束）。")
        else:
            notes.append("无环结构（直链/支链），构象自由度较高。")
    else:
        notes.append("环数未提供。")

    # 2) 可旋转键：柔性与"规则度"的权衡
    if rotors is not None:
        if rotors == 0:
            score += 5
            notes.append("无可旋转键（刚性），结构确定、构象退化。")
        elif rotors <= 5:
            score += 3
            notes.append(f"可旋转键 {int(rotors)} 个，柔性适中。")
        else:
            score -= 4
            notes.append(f"可旋转键 {int(rotors)} 个，柔性较高、构象空间大。")
    else:
        notes.append("可旋转键数未提供。")

    # 3) TPSA / 极性：适中的极性表面更"常规"
    if tpsa is not None:
        if tpsa <= 140:
            score += 5
            notes.append(f"极性表面积 TPSA={tpsa:.1f} Å²，处于常见范围。")
        else:
            score -= 2
            notes.append(f"极性表面积 TPSA={tpsa:.1f} Å² 偏高（极性较强）。")
    else:
        notes.append("TPSA 未提供。")

    # 4) 氢键供/受体：过多时提示「强极性/可能难溶解于非极性溶剂」
    if hbd is not None and hba is not None:
        total_hb = hbd + hba
        if total_hb <= 8:
            score += 3
        else:
            score -= 2
            notes.append(f"氢键供/受体共 {int(total_hb)} 个，极性基团较多。")
    else:
        notes.append("氢键供/受体未提供。")

    # 5) 分子量：过大时提示计算成本（非美观，但实用）
    if mw is not None:
        if mw > 500:
            score -= 3
            notes.append(f"分子量 {mw:.1f} g/mol，较大，量化计算成本较高。")
        elif mw >= 100:
            score += 2
            notes.append(f"分子量 {mw:.1f} g/mol。")
    else:
        notes.append("分子量未提供。")

    # 6) 重原子/键数：结构规模
    if heavy is not None and bonds is not None:
        if heavy > 0 and bonds is not None and abs(bonds - heavy) <= heavy:
            # 键数/原子数比例正常（无孤立原子/异常拓扑）——仅作展示
            notes.append(f"重原子 {int(heavy)} 个、键 {int(bonds)} 条，规模常规。")
    else:
        notes.append("重原子/键数未提供。")

    if formula:
        notes.append(f"分子式：{formula}。")

    # 收束到 [0, 100]
    score = max(0.0, min(100.0, score))
    if score >= 85:
        grade = "A（很规整）"
    elif score >= 70:
        grade = "B（较规整）"
    elif score >= 55:
        grade = "C（一般）"
    else:
        grade = "D（复杂度/极性偏高）"

    notes.append("免责声明：本评分为教学启发式，不代表化学正确性或稳定性。")
    return {"score": round(score, 1), "grade": grade, "notes": notes}


__all__ = ["score_structure"]
