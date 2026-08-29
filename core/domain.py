#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心领域数据结构（框架无关的纯数据定义）。

目标（Phase B · 可维护性）：
  - 用 ``dataclass`` / ``TypedDict`` 统一定义分子、映射、计算结果等核心结构，
    避免散落在各处的 ``dict[str, Any]`` 随意拼接。
  - 全程不依赖 Tkinter / PSI4 / OpenBabel，可被 mypy 静态检查、被 pytest 单测。

后续若需要给整个代码库加 ``mypy --strict``，这里是最先被覆盖、也最容易达标的模块。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any


# ------------------------- 分子记录 -------------------------
@dataclass
class MoleculeRecord:
    """一个分子在本地数据库中的归一化记录。

    ``name`` 作为主键（通常是文件名去掉扩展名，或 InChIKey 前 14 位）。
    """

    name: str
    formula: str | None = None
    smiles: str | None = None
    inchi: str | None = None
    mol_file: str | None = None  # 关联 .mol 路径（相对工作目录）
    xyz_file: str | None = None  # 关联 .xyz 路径
    source_path: str | None = None  # 原始来源路径
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MoleculeRecord:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        base = {k: v for k, v in d.items() if k in known}
        # tags 允许以逗号字符串或列表传入
        if isinstance(base.get("tags"), str):
            base["tags"] = [t for t in base["tags"].split(",") if t]
        return cls(**base)


# ------------------------- 映射条目 -------------------------
@dataclass
class MappingEntry:
    """中英文 / 编号 → 中文名 的映射条目（去重后按 eng 主键存储）。"""

    eng: str
    chn: str
    note: str | None = None
    created_at: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MappingEntry:
        return cls(
            eng=str(d.get("eng", "")),
            chn=str(d.get("chn", "")),
            note=d.get("note"),
        )


# ------------------------- 计算结果 -------------------------
@dataclass
class CalcResult:
    """一次量化 / 描述符计算的结果记录。"""

    id: int | None = None
    molecule: str = ""  # 关联 MoleculeRecord.name
    calc_type: str = ""  # psi4 / descriptor / ob_convert ...
    method: str | None = None  # 如 B3LYP / HF
    basis: str | None = None  # 如 6-31G*
    energy: float | None = None  # 单位 Hartree（若是能量）
    status: str = "done"  # pending / running / done / error
    output_path: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CalcResult:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


__all__ = [
    "MoleculeRecord",
    "MappingEntry",
    "CalcResult",
]
