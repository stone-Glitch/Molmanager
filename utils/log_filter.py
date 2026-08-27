#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F15 日志过滤 —— 纯匹配逻辑（T03 / Phase 1）
──────────────────────────────────────────
职责：把「级别阈值 + 关键词」两个维度的匹配规则抽成**无副作用的纯函数**，
供 utils/logger.GuiLogHandler 与 ui/log_filter_bar.LogFilterBar 共用。

约束（架构 §6）：
  - **无 Tk 依赖**，可脱离 GUI 单测；
  - 不 import chem.psi4（PSI4 命名陷阱）；
  - 命名一律带 log_filter 语义，避免与文件列表过滤（filter_keyword_var）串扰（C8）。

记录格式约定（与 GuiLogHandler._all_records 一致）：
    4 元组 ``(levelno, levelname, display_msg, raw_message)``
其中 ``raw_message`` 是**未着色、未加级别前缀的原始消息**，关键词匹配只对它做，
不受 tag / 前缀影响（架构 §3.1）。为兼容旧数据，本模块同时接受 3 元组
``(levelno, levelname, display_msg)``，此时用 display_msg 参与关键词匹配。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


# ---------------------------------------------------------------- 级别定义
# 与 logging 模块及 utils/logger.LEVEL_SUCCESS(=25) 保持一致。
LEVEL_ALL: str = "ALL"

LEVEL_VALUES: dict[str, int] = {
    LEVEL_ALL: 0,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

#: 过滤条下拉框的取值顺序（英文键，写入 config 的也是这些键）
LEVEL_ORDER: tuple[str, ...] = (
    LEVEL_ALL, "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL",
)

#: 下拉框展示用中文标签
LEVEL_LABELS: dict[str, str] = {
    LEVEL_ALL: "全部",
    "DEBUG": "DEBUG 及以上",
    "INFO": "INFO 及以上",
    "SUCCESS": "SUCCESS 及以上",
    "WARNING": "WARNING 及以上",
    "ERROR": "ERROR 及以上",
    "CRITICAL": "仅 CRITICAL",
}

#: 反查：中文标签 -> 英文键
LABEL_TO_LEVEL: dict[str, str] = {v: k for k, v in LEVEL_LABELS.items()}

DEFAULT_LEVEL: str = "INFO"
DEFAULT_KEYWORD: str = ""


# ---------------------------------------------------------------- 级别工具

def normalize_level(level: Any) -> str:
    """
    把任意输入规范化为 LEVEL_ORDER 中的合法级别键。

    支持：英文键（大小写不敏感）、中文标签、整数 levelno。
    无法识别时返回 ``LEVEL_ALL``（宁可多显示，也不静默吞日志）。
    """
    if level is None:
        return LEVEL_ALL
    if isinstance(level, bool):
        return LEVEL_ALL
    if isinstance(level, int):
        best = LEVEL_ALL
        for name in LEVEL_ORDER:
            if name == LEVEL_ALL:
                continue
            if LEVEL_VALUES[name] <= level:
                best = name
        return best
    try:
        text = str(level).strip()
    except Exception:
        return LEVEL_ALL
    if not text:
        return LEVEL_ALL
    if text in LABEL_TO_LEVEL:
        return LABEL_TO_LEVEL[text]
    upper = text.upper()
    if upper in LEVEL_VALUES:
        return upper
    return LEVEL_ALL


def level_threshold(level: Any) -> int:
    """返回级别键对应的数值阈值；``LEVEL_ALL`` -> 0（不过滤）。"""
    return LEVEL_VALUES.get(normalize_level(level), 0)


def level_label(level: Any) -> str:
    """返回级别键对应的中文展示标签。"""
    return LEVEL_LABELS.get(normalize_level(level), LEVEL_LABELS[LEVEL_ALL])


def level_name_of(levelno: Any) -> str:
    """把 levelno 数值映射回最接近的级别名（用于展示 / 统计）。"""
    try:
        n = int(levelno)
    except (TypeError, ValueError):
        return "INFO"
    exact = {v: k for k, v in LEVEL_VALUES.items() if k != LEVEL_ALL}
    if n in exact:
        return exact[n]
    best = "DEBUG"
    for name in ("DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"):
        if LEVEL_VALUES[name] <= n:
            best = name
    return best


# ---------------------------------------------------------------- 记录工具

def record_levelno(record: Sequence[Any]) -> int:
    """安全取出记录的 levelno；取不到时返回 logging.INFO(20)。"""
    try:
        return int(record[0])
    except (TypeError, ValueError, IndexError):
        return 20


def record_text(record: Sequence[Any]) -> str:
    """
    取出参与关键词匹配的文本。

    4 元组 → 第 4 位（原始 message）；3 元组 → 第 3 位（display_msg）。
    """
    try:
        if len(record) >= 4 and record[3] is not None:
            return str(record[3])
    except (TypeError, IndexError):
        pass
    try:
        return str(record[2])
    except (TypeError, IndexError):
        return ""


# ---------------------------------------------------------------- 匹配

def match_level(levelno: Any, level: Any = LEVEL_ALL) -> bool:
    """级别阈值匹配：记录级别 >= 过滤阈值 才通过。"""
    threshold = level_threshold(level)
    if threshold <= 0:
        return True
    try:
        return int(levelno) >= threshold
    except (TypeError, ValueError):
        # 级别不可解析时放行，避免误吞
        return True


def match_keyword(text: Any, keyword: Any = "", *, case_sensitive: bool = False) -> bool:
    """
    关键词子串匹配。空关键词恒为 True。

    默认大小写不敏感（化学文件名常混用大小写，用户不该被大小写绊住）。
    """
    if keyword is None:
        return True
    try:
        kw = str(keyword).strip()
    except Exception:
        return True
    if not kw:
        return True
    try:
        body = "" if text is None else str(text)
    except Exception:
        return False
    if case_sensitive:
        return kw in body
    return kw.lower() in body.lower()


def match_record(
    record: Sequence[Any],
    *,
    level: Any = LEVEL_ALL,
    keyword: Any = "",
    case_sensitive: bool = False,
) -> bool:
    """单条记录是否同时满足级别与关键词条件（AND 语义）。"""
    if not match_level(record_levelno(record), level):
        return False
    return match_keyword(record_text(record), keyword, case_sensitive=case_sensitive)


def filter_records(
    records: Iterable[Sequence[Any]],
    *,
    level: Any = LEVEL_ALL,
    keyword: Any = "",
    case_sensitive: bool = False,
) -> list[Sequence[Any]]:
    """返回所有命中的记录（保持原顺序）。"""
    return [
        r for r in records
        if match_record(r, level=level, keyword=keyword, case_sensitive=case_sensitive)
    ]


def count_matches(
    records: Iterable[Sequence[Any]],
    *,
    level: Any = LEVEL_ALL,
    keyword: Any = "",
    case_sensitive: bool = False,
) -> tuple[int, int]:
    """
    统计 ``(命中条数, 总条数)``，用于过滤条右侧的「显示 X / 共 Y 条」。

    只遍历一次，避免在 5 万条上限下做两遍扫描。
    """
    matched = 0
    total = 0
    for r in records:
        total += 1
        if match_record(r, level=level, keyword=keyword, case_sensitive=case_sensitive):
            matched += 1
    return matched, total


def describe_filter(level: Any = LEVEL_ALL, keyword: Any = "") -> str:
    """生成人类可读的过滤条件描述（写日志 / tooltip 用）。"""
    lv = normalize_level(level)
    kw = "" if keyword is None else str(keyword).strip()
    parts: list[str] = []
    if lv != LEVEL_ALL:
        parts.append(f"级别≥{lv}")
    if kw:
        parts.append(f"关键词“{kw}”")
    return " + ".join(parts) if parts else "无过滤"


__all__ = [
    "LEVEL_ALL",
    "LEVEL_VALUES",
    "LEVEL_ORDER",
    "LEVEL_LABELS",
    "LABEL_TO_LEVEL",
    "DEFAULT_LEVEL",
    "DEFAULT_KEYWORD",
    "normalize_level",
    "level_threshold",
    "level_label",
    "level_name_of",
    "record_levelno",
    "record_text",
    "match_level",
    "match_keyword",
    "match_record",
    "filter_records",
    "count_matches",
    "describe_filter",
]
