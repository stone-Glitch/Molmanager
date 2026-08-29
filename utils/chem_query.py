#!/usr/bin/env python3
"""
化学感知搜索（E-04 的纯逻辑层）。

把「按化学属性过滤文件」的逻辑从 UI/模型里剥离出来，做成可单测的纯函数：

    benzene.mol  →  mw:>60 formula:C6H6 logP:<3

支持的前缀（不分大小写，含常用别名）：
    mw / molecularweight / weight / molweight / mass   → 分子量（数值）
    formula / molformula                               → 分子式（字符串，子串/精确）
    logp / log p / xlogp                               → 预测 logP（数值）
    heavy / heavyatoms                                 → 重原子数（数值）
    atoms / natoms                                     → 原子总数（数值）
    rotors / rotatable                                 → 可旋转键数（数值）

比较符：>  <  >=  <=  =  :  （冒号对数值=数值比较；对 formula=大小写不敏感子串）
未带任何已知 key: 前缀的查询词，视为「自由文本」（按文件名/英文名/中文名/词干子串匹配）。

设计红线（遵循本项目「验证 > 承诺 / 不造假」）：
    —— 某条目的目标字段缺失（描述符未算 / 解析失败）时，任何针对该字段的条件都判定为
       False（即该条目被排除），**绝不**用占位值凑出假阳性命中。
    —— 纯引擎不触碰文件系统 / OpenBabel；富集（算描述符）由调用方（model.filter_files）负责。
"""

from __future__ import annotations

import re
from typing import Any

# ---------- 键别名 → 内部规范键 ----------
_KEY_ALIASES: dict[str, str] = {
    "mw": "mw",
    "molecularweight": "mw",
    "molweight": "mw",
    "weight": "mw",
    "mass": "mw",
    "formula": "formula",
    "molformula": "formula",
    "logp": "logp",
    "log p": "logp",
    "xlogp": "logp",
    "heavy": "heavy",
    "heavyatoms": "heavy",
    "atoms": "atoms",
    "natoms": "atoms",
    "rotors": "rotors",
    "rotatable": "rotors",
}

# 哪些规范键是「数值比较」
_NUMERIC_KEYS = frozenset({"mw", "logp", "heavy", "atoms", "rotors"})

# entry 上实际可能存的字段名（描述符键 vs 我们的别名）
_FIELD_LOOKUP: dict[str, tuple[str, ...]] = {
    "mw": ("mw", "molecular_weight"),
    "formula": ("formula", "molecular_formula"),
    "logp": ("logP", "logp", "xlogp"),
    "heavy": ("heavy", "heavy_atoms"),
    "atoms": ("atoms", "natoms", "num_atoms"),
    "rotors": ("rotors", "rotatable_bonds"),
}

# 分隔符是「冒号」：key:value 或 key:opvalue（op 在冒号之后）。
# 注意：冒号本身不是比较符，避免 `mw:>60` 被误拆成 op=':' value='>60'。
_KEY_SPLIT = re.compile(r"^([A-Za-z_ ]+):(.*)$")
_OP_SPLIT = re.compile(r"^(>=|<=|>|<|=)?(.*)$")


class ChemCondition:
    """一条化学条件：key(别名) op value。"""

    __slots__ = ("raw_key", "key", "op", "value", "is_numeric")

    def __init__(self, raw_key: str, key: str, op: str, value: str):
        self.raw_key = raw_key.strip()
        self.key = key
        self.op = op
        self.value = value.strip()
        self.is_numeric = key in _NUMERIC_KEYS

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ChemCondition({self.key!r}{self.op!r}{self.value!r})"


def _split_operator(raw: str) -> tuple[str | None, str | None, str | None]:
    """把 'mw:>60' / 'formula:C6H6' 拆成 (key, op, value)。

    格式：key 与值之间用冒号分隔；冒号之后可紧跟比较符 > < >= <= =，否则视为 ':'（数值相等 / 公式子串）。
    无法识别（无冒号 / 未知 key / 空值）→ (None, None, None)。
    """
    m = _KEY_SPLIT.match(raw.strip())
    if not m:
        return (None, None, None)
    key_raw = m.group(1).strip().lower()
    norm = _KEY_ALIASES.get(key_raw)
    if norm is None:
        return (None, None, None)
    rest = m.group(2)
    om = _OP_SPLIT.match(rest)
    op = om.group(1) or ":"
    value = (om.group(2) or "").strip()
    if value == "":
        return (None, None, None)
    return (key_raw, op, value)


def parse_chem_query(query: str) -> tuple[list[ChemCondition], list[str]]:
    """
    解析查询串 → (化学条件列表, 自由文本词列表)。

    - 命中已知 key: 前缀的词 → ChemCondition
    - 其余词 → 自由文本（保留顺序、去前后空格、忽略空串）
    - 空查询 → ([], [])
    """
    conditions: list[ChemCondition] = []
    free_terms: list[str] = []
    if not query or not query.strip():
        return conditions, free_terms
    for token in query.split():
        key_raw, op, value = _split_operator(token)
        if key_raw is not None and value != "":
            conditions.append(ChemCondition(key_raw, _KEY_ALIASES[key_raw], op, value))
        else:
            t = token.strip()
            if t:
                free_terms.append(t)
    return conditions, free_terms


def _entry_field(entry: dict[str, Any], key: str) -> Any:
    """从 entry 取某个规范键对应的真实字段值；多个候选键依次尝试，缺失返回 None。

    匹配顺序：
      1. 候选键精确命中（``molecular_weight``、``logP`` …）；
      2. 候选键的**大小写不敏感**命中 —— 因为 UI 与导出的 CSV 里同一字段
         常被写成 ``MW`` / ``LogP`` / ``TPSA``，而描述符实际输出的是
         ``molecular_weight`` / ``logP``。少了这一步，用户照着表头输入
         ``MW>200`` 会一条都查不到（表现为「搜索框失灵」）。

    注意：这里只放宽「字段名写法」，**不放宽字段缺失**——
    目标字段不存在时依然返回 None，由比较函数判 False（红线：不造假阳性）。
    """
    candidates = _FIELD_LOOKUP.get(key, (key,))
    for fld in candidates:
        if fld in entry and entry[fld] not in (None, "", "N/A"):
            return entry[fld]

    # 大小写不敏感兜底（每次调用构造一次映射，条目量级很小，代价可忽略）
    lowered = {str(k).lower(): v for k, v in entry.items()}
    for fld in candidates:
        val = lowered.get(str(fld).lower())
        if val not in (None, "", "N/A"):
            return val
    return None


def _to_float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _cmp_numeric(entry_val: Any, op: str, target: str) -> bool:
    """数值比较。entry 字段缺失或非数值 → 返回 False（红线：不造假阳性）。"""
    ev = _to_float(entry_val)
    tv = _to_float(target)
    if ev is None or tv is None:
        return False
    if op == ">":
        return ev > tv
    if op == "<":
        return ev < tv
    if op == ">=":
        return ev >= tv
    if op == "<=":
        return ev <= tv
    # '=' 与 ':' 对数值均按相等
    return ev == tv


def _cmp_text(entry_val: Any, op: str, target: str) -> bool:
    """formula 等字符串比较：缺失 → False；'='/':' 均按大小写不敏感子串包含。"""
    if entry_val is None:
        return False
    s = str(entry_val).lower()
    t = target.lower()
    return t in s


def match_entry(entry: dict[str, Any], conditions: list[ChemCondition]) -> bool:
    """
    判断单个 entry 是否满足全部化学条件（AND）。
    任一条件不满足即 False。缺失字段的条件 → False（不造假阳性）。
    """
    for c in conditions:
        fld = _entry_field(entry, c.key)
        if c.is_numeric:
            if not _cmp_numeric(fld, c.op, c.value):
                return False
        else:
            if not _cmp_text(fld, c.op, c.value):
                return False
    return True


def matches_free_text(entry: dict[str, Any], free_terms: list[str]) -> bool:
    """自由文本：所有词必须作为子串出现在 name/base/eng/chn 之一（大小写不敏感）。"""
    if not free_terms:
        return True
    hay = " ".join(str(entry.get(k, "")) for k in ("name", "base", "eng", "chn")).lower()
    return all(term.lower() in hay for term in free_terms)


def filter_entries(entries: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """
    纯引擎入口：按化学查询过滤 entries。
    - 无任何条件且无自由文本（空查询）→ 原样返回全部（调用方负责 status/ext 过滤）。
    - 仅自由文本 → 等价于原 keyword 子串匹配。
    - 含化学条件 → 先 match_entry 再 matches_free_text，缺失字段的条目被安全排除。
    """
    conditions, free_terms = parse_chem_query(query)
    if not conditions and not free_terms:
        return list(entries)
    out = []
    for e in entries:
        if match_entry(e, conditions) and matches_free_text(e, free_terms):
            out.append(e)
    return out


def looks_like_chem_query(query: str) -> bool:
    """是否含至少一个已知 key: 前缀（供 model.filter_files 决定走哪条分支）。"""
    if not query:
        return False
    for token in query.split():
        key_raw, _op, value = _split_operator(token)
        if key_raw is not None and value != "":
            return True
    return False
