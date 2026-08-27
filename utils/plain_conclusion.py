#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U-09 计算结果「通俗结论」区域 · 纯逻辑层

把量化计算返回的结构化结果 dict 翻译成一句（或几句）人话，
供 UI 在结果面板顶部展示给非专家用户。

只做「字段 → 通俗文字」的映射，绝不新增/伪造数值；字段缺失就照实
说「未取得」，与项目「正确 > 假数据」红线一致。
"""
from typing import Any


TASK_LABELS = {
    "energy": "单点能",
    "optimize": "几何优化",
    "frequency": "频率分析",
    "scan": "势能面扫描",
    "ts": "过渡态搜索",
    "excited": "激发态",
    "sapt": "SAPT 相互作用",
    "thermo": "热化学分析",
}


def _g(res: dict[str, Any], key: str, default: Any | None = None) -> Any:
    return res.get(key, default)


def conclusion_for(result: dict[str, Any]) -> str:
    """返回一句话结论（可能含换行分句）。入参为空/非 dict 时给兜底句。"""
    if not isinstance(result, dict) or not result:
        return "暂无计算结果。"

    parts: list[str] = []

    task = str(_g(result, "task", "") or "").lower()
    task_label = TASK_LABELS.get(task, task or "计算")
    parts.append(f"本次执行的是{task_label}。")

    method = _g(result, "method")
    basis = _g(result, "basis")
    if method and basis:
        parts.append(f"采用 {method}/{basis} 方法。")
    elif method:
        parts.append(f"采用 {method} 方法。")

    energy = _g(result, "energy")
    if isinstance(energy, (int, float)):
        parts.append(f"得到的电子能量约为 {energy:.6f} Hartree。")

    # 警告类字段 —— 必须醒目，绝不静默
    warnings = []
    if _g(result, "pcm_rolled_back"):
        warnings.append("溶剂模型未能启用，已回退到气相（结果不含溶剂效应）。")
    if _g(result, "thermo_fallback"):
        warnings.append("热化学量未能取得，仅给出电子能。")
    if _g(result, "warning"):
        warnings.append(str(result["warning"]))
    for w in warnings:
        parts.append(f"⚠️ {w}")

    # 元信息
    elapsed = _g(result, "elapsed_sec")
    if isinstance(elapsed, (int, float)) and elapsed >= 0:
        parts.append(f"耗时约 {elapsed:.1f} 秒。")

    return " ".join(parts)


def conclusion_plain(result: dict[str, Any]) -> str:
    """alias，语义化命名，供 UI 直接 import。"""
    return conclusion_for(result)


__all__ = ["conclusion_for", "conclusion_plain", "TASK_LABELS"]
