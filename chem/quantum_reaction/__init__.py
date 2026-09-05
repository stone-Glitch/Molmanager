"""Quantum Reaction（量子反应能计算）子包。

源自独立的 Quantum Reaction Visualizer（Flask 网页版），融合进 MolManager 后
计算内核与前端解耦：

- ``reactions``   ：8 个预设反应库（含 O₂ 三线态标注）
- ``molbuild``    ：SMILES/名称 → 3D XYZ（rdkit + pubchempy，均惰性可选）
- ``quantum``     ：psi4 包装（优化/单点/逐帧能量/频率热化学 + 双缓存 +
                    单原子降级 + SCF 分级重试 + c1 对称强制）
- ``animate``     ：Kabsch 对齐 + 线性插值 + IQmol 兼容多帧 XYZ + MP4
- ``runner``      ：端到端编排（框架无关，回调驱动，支持协作式取消）
- ``iqmol_check`` ：按 IQmol XyzParser 规则校验多帧 XYZ

依赖均为**可选**：缺 psi4 / rdkit / imageio 时 import 本包不报错，
调用相应功能时给出可操作的中文提示（与 MolManager 优雅降级风格一致）。
"""

from __future__ import annotations

from .animate import make_trajectory, write_multiframe_xyz
from .molbuild import RDKIT_AVAILABLE, normalize_atom_counts, parse_xyz
from .reactions import REACTIONS, get_reaction, list_reactions
from .runner import (
    CancelledError,
    combined_mult,
    list_runs,
    parse_species_token,
    run_reaction,
)

__all__ = [
    "REACTIONS",
    "RDKIT_AVAILABLE",
    "CancelledError",
    "combined_mult",
    "get_reaction",
    "list_reactions",
    "list_runs",
    "make_trajectory",
    "normalize_atom_counts",
    "parse_species_token",
    "parse_xyz",
    "run_reaction",
    "write_multiframe_xyz",
]


def psi4_available() -> bool:
    """psi4 是否可导入（惰性探测，结果缓存）。"""
    global _PSI4_OK
    try:
        return _PSI4_OK
    except NameError:
        pass
    try:
        import psi4  # noqa: F401

        _PSI4_OK = True
    except Exception:
        _PSI4_OK = False
    return _PSI4_OK
