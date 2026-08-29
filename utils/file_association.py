#!/usr/bin/env python3
"""
E-06 反向追溯（纯逻辑层）

给定目录里的文件名清单，按「词干(stem)」把结构文件与其计算结果
（.log/.out/.fchk/.inp 等）关联起来，便于 UI 展示
「一个分子 → 它对应的所有计算输出」的追溯链。

纯字符串处理，零文件系统依赖，可在沙箱单测。
"""

from typing import NamedTuple

# 视为「结构本体」的扩展名（追溯的锚点）
STRUCTURE_EXTS = (".xyz", ".mol", ".sdf", ".pdb", ".cif", ".mol2", ".pdbqt", ".cml")
# 视为「计算结果」的扩展名（挂在结构词干下）
RESULT_EXTS = (".log", ".out", ".fchk", ".inp", ".gjf", ".com", ".json")


class FileLink(NamedTuple):
    stem: str
    structure: str | None  # 结构文件全名（若有）
    results: list[str]  # 计算结果文件全名列表
    extras: list[str]  # 同词干但不属于上述两类的文件


def _stem_of(name: str) -> str:
    dot = name.rfind(".")
    if dot <= 0:
        return name
    return name[:dot]


def _ext_of(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def associate_by_stem(
    filenames: list[str],
    structure_exts: tuple = STRUCTURE_EXTS,
    result_exts: tuple = RESULT_EXTS,
) -> list[FileLink]:
    """
    按词干聚合同目录文件名，产出追溯链列表。

    - 同一词干下：结构文件（STRUCTURE_EXTS）至多取一个（多结构容器
      如 .sdf 也只算一个锚点）；计算结果（RESULT_EXTS）全部归入 results；
      其余扩展名归入 extras（如 .png 预览、.bak 备份）。
    - 仅当该词干至少含一个结构或结果文件时才产出一条 FileLink，
      纯 extras 的同名词干不单独成链（避免噪声）。
    - 返回按 stem 排序，结果稳定可重放。
    """
    groups: dict[str, dict[str, list[str]]] = {}
    for fn in filenames:
        ext = _ext_of(fn)
        if ext not in structure_exts and ext not in result_exts:
            # 既不是结构也不是结果：先放进 extras 桶（按词干）
            stem = _stem_of(fn)
            buckets = groups.setdefault(stem, {"s": [], "r": [], "x": []})
            buckets["x"].append(fn)
            continue
        stem = _stem_of(fn)
        buckets = groups.setdefault(stem, {"s": [], "r": [], "x": []})
        if ext in structure_exts:
            buckets["s"].append(fn)
        else:
            buckets["r"].append(fn)

    links: list[FileLink] = []
    for stem in sorted(groups.keys()):
        b = groups[stem]
        if not b["s"] and not b["r"]:
            continue  # 纯 extras，不成链
        structure = b["s"][0] if b["s"] else None
        results = sorted(b["r"])
        extras = sorted(b["x"])
        links.append(FileLink(stem=stem, structure=structure, results=results, extras=extras))
    return links


def unlinked_results(links: list[FileLink]) -> list[str]:
    """返回所有「没有对应结构文件」的孤立结果文件（无法追溯来源）。"""
    out: list[str] = []
    for lk in links:
        if lk.structure is None:
            out.extend(lk.results)
    return out


__all__ = ["FileLink", "STRUCTURE_EXTS", "RESULT_EXTS", "associate_by_stem", "unlinked_results", "_stem_of", "_ext_of"]
