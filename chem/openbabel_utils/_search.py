#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于 OpenBabel 指纹与 SMARTS 的子结构 / 相似性搜索。

仅依赖项目已有的 openbabel（C++ 绑定），不引入 RDKit 等额外重依赖。
所有函数对解析失败的单条分子保持健壮：记录日志并跳过，不影响整体结果。
"""

from __future__ import annotations

from collections.abc import Iterable
import logging

import openbabel as ob


logger = logging.getLogger(__name__)

# OpenBabel 常见指纹类型（随版本可能略有差异，调用前会实际探测）
SUPPORTED_FP_TYPES = ("FP2", "FP3", "FP4", "MACCS", "maccs", "EState", "estate")

_FP_CACHE: dict = {}


def _get_fingerprint_type(fptype: str):
    """返回已注册的 OBFingerprint 实例（带缓存）。"""
    key = fptype.upper()
    if key in _FP_CACHE:
        return _FP_CACHE[key]
    try:
        inst = ob.OBFingerprint.FindType(fptype)
    except Exception as exc:  # pragma: no cover - 依赖 openbabel 运行时
        logger.warning("获取指纹类型 %s 失败：%s", fptype, exc)
        return None
    if inst is None:
        logger.warning("OpenBabel 不支持的指纹类型：%s", fptype)
        return None
    _FP_CACHE[key] = inst
    return inst


def _mol_from_input(molecule: str, fmt: str) -> ob.OBMol | None:
    """从 SMILES / molfile 文本构建 OBMol，失败时返回 None。"""
    fmt = (fmt or "smi").lower()
    if fmt == "smi":
        fmt = "smiles"
    conv = ob.OBConversion()
    if not conv.SetInFormat(fmt):
        logger.warning("OpenBabel 不支持的输入格式：%s", fmt)
        return None
    mol = ob.OBMol()
    if not conv.ReadString(mol, molecule or ""):
        return None
    if mol.NumAtoms() == 0:
        return None
    return mol


def substructure_search(
    smarts: str,
    molecules: Iterable[str],
    fmt: str = "smi",
) -> list[str]:
    """返回所有匹配 SMARTS 子结构式的分子输入文本。

    Args:
        smarts: SMARTS 子结构模式（如 ``C-O``、``[NH2]``）。
        molecules: 分子输入文本的可迭代对象（SMILES 或 molfile）。
        fmt: 分子输入格式（``smi`` 或 ``mol``）。

    Returns:
        匹配到的原始分子输入字符串列表（保持入参顺序去重）。
    """
    pat = ob.OBSmartsPattern()
    if not pat.Init(smarts):
        logger.warning("无效 SMARTS 模式：%s", smarts)
        return []
    results: list[str] = []
    seen: set = set()
    for mol_text in molecules:
        if mol_text in seen:
            continue
        mol = _mol_from_input(mol_text, fmt)
        if mol is None:
            continue
        if pat.Match(mol):
            results.append(mol_text)
            seen.add(mol_text)
    return results


def compute_fingerprint(
    molecule: str,
    fmt: str = "smi",
    fptype: str = "FP2",
) -> ob.vectorUnsignedInt | None:
    """计算单条分子的指纹向量；失败返回 None。"""
    fp_type = _get_fingerprint_type(fptype)
    if fp_type is None:
        return None
    mol = _mol_from_input(molecule, fmt)
    if mol is None:
        return None
    fp = ob.vectorUnsignedInt()
    try:
        fp_type.GetFingerprint(mol, fp)
    except Exception as exc:  # pragma: no cover - 依赖 openbabel 运行时
        logger.warning("计算指纹失败：%s", exc)
        return None
    if len(fp) == 0:
        return None
    return fp


def tanimoto(fp_a, fp_b) -> float:
    """两个 OpenBabel 指纹向量的 Tanimoto 相似度，范围 [0, 1]。"""
    if fp_a is None or fp_b is None:
        return 0.0
    try:
        return float(ob.OBFingerprint.Tanimoto(fp_a, fp_b))
    except Exception:  # pragma: no cover - 依赖 openbabel 运行时
        return 0.0


def similarity_search(
    query: str,
    molecules: Iterable[str],
    fmt: str = "smi",
    fptype: str = "FP2",
    threshold: float = 0.3,
    top_n: int | None = None,
) -> list[tuple[str, float]]:
    """在 ``molecules`` 中检索与 ``query`` 指纹相似度 >= ``threshold`` 的分子。

    Returns:
        按相似度降序排列的 ``(分子输入文本, 相似度)`` 列表。
    """
    q_fp = compute_fingerprint(query, fmt, fptype)
    if q_fp is None:
        return []
    scored: list[tuple[str, float]] = []
    for mol_text in molecules:
        fp = compute_fingerprint(mol_text, fmt, fptype)
        if fp is None:
            continue
        sim = tanimoto(q_fp, fp)
        if sim >= threshold:
            scored.append((mol_text, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    if top_n is not None:
        scored = scored[:top_n]
    return scored
