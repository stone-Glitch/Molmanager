#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-01 深度元数据索引（.log/.out/.fchk 动态列）· 纯逻辑层

从计算结果文件（Gaussian/ORCA/CP2K 的 .log/.out，以及 Gaussian .fchk）
抽取结构化元数据，并把「一批文件的元数据」归并成可动态渲染的列集合。

纯正则/文本解析，无外部依赖，可在沙箱用合成文本单测。
"""
import re
from typing import Any, Dict, List, Optional

from utils.calc_log_parser import parse_calc_log

# .fchk 头部：一行「标签  类型字符(R/I/C/L)  值」
_FCHK_LINE = re.compile(r"^(?P<label>.+?)\s+[RICL]\s+(?P<value>.+)$")


def parse_fchk(text: str) -> Dict[str, Any]:
    """解析 Gaussian .fchk 头部，返回 {标签: 值}（值转成数字/布尔/字符串）。"""
    out: Dict[str, Any] = {}
    for line in (text or "").splitlines():
        m = _FCHK_LINE.match(line)
        if not m:
            continue
        label = m.group("label").strip()
        raw = m.group("value").strip()
        if not label:
            continue
        # 尝试数值化
        try:
            if "." in raw or "e" in raw.lower() or "E" in raw:
                out[label] = float(raw)
            else:
                out[label] = int(raw)
        except ValueError:
            low = raw.lower()
            if low == "t":
                out[label] = True
            elif low == "f":
                out[label] = False
            else:
                out[label] = raw
    return out


def extract_metadata(path: str, text: Optional[str] = None) -> Dict[str, Any]:
    """
    按扩展名分派解析，返回统一元数据 dict（含 "source" 字段标注来源扩展名）。

    - .fchk → parse_fchk
    - .log / .out → parse_calc_log（自动识别 Gaussian/ORCA/CP2K）
    - 其它 → {"source": ext}（不伪造）
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext == "fchk":
        meta = parse_fchk(text or "")
        meta["source"] = "fchk"
        return meta
    if ext in ("log", "out"):
        meta = parse_calc_log(text or "")
        meta["source"] = ext
        return meta
    return {"source": ext}


def collect_columns(metadata_list: List[Dict[str, Any]]) -> List[str]:
    """
    把一批文件的元数据归并成「动态列」键名（确定性排序）。
    返回的列名是出现过的所有键的并集，按字母序排列，保证 UI 稳定渲染。
    """
    keys = set()
    for meta in metadata_list:
        if isinstance(meta, dict):
            keys.update(meta.keys())
    return sorted(keys)


def index_files(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    便捷入口：entries 里每项含 {name/path, meta}（meta 由调用方经
    extract_metadata 得到）。返回带 "_columns" 的汇总，便于 UI 直接画表。
    """
    metas = [e.get("meta", {}) for e in entries if isinstance(e, dict)]
    cols = collect_columns(metas)
    return [{"entries": entries, "columns": cols}]


__all__ = ["parse_fchk", "extract_metadata", "collect_columns", "index_files"]
