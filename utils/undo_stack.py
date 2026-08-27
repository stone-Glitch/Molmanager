#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M-07 映射变更细粒度历史（逐条撤销）· 纯逻辑核心

为映射编辑器提供「快照式」撤销/重做栈。UI 每次执行一条可撤销操作
（增/删/改行、导入、重排）后调用 ``push`` 压入映射表快照；用户点
「撤销/重做」时 ``undo``/``redo`` 返回目标快照。

设计要点：
  - 栈只存「状态快照」（调用方负责深拷贝传入），自身不关心映射结构，
    因此与具体 UI（Treeview）彻底解耦，可独立单测。
  - 上限 ``maxlen`` 防止无限增长；超出后丢弃最旧快照。
  - 所有方法幂等且不会抛异常（边界条件返回 None/空）。
"""
import copy
from typing import Any


class UndoStack:
    def __init__(self, maxlen: int | None = 200):
        self._undo: list[tuple[Any, str | None]] = []
        self._redo: list[tuple[Any, str | None]] = []
        self._maxlen = maxlen if (maxlen is None or maxlen > 0) else 1

    # ---- 写入 ----
    def push(self, state: Any, label: str | None = None) -> None:
        """压入一个新状态快照（应已深拷贝）。会清空重做栈。"""
        snap = copy.deepcopy(state)
        if self._maxlen is not None and len(self._undo) >= self._maxlen:
            self._undo.pop(0)
        self._undo.append((snap, label))
        self._redo.clear()

    def reset(self, state: Any, label: str | None = None) -> None:
        """清空历史并以 state 作为基线（不计入可撤销步骤）。"""
        self._undo.clear()
        self._redo.clear()
        self._undo.append((copy.deepcopy(state), label))

    # ---- 读取 ----
    def can_undo(self) -> bool:
        return len(self._undo) > 1  # 至少有「基线 + 一步」才能撤

    def can_redo(self) -> bool:
        return len(self._redo) > 0

    def undo(self) -> Any | None:
        """
        撤销一步：弹出当前栈顶，把「上一个状态」放回栈顶并返回它；
        被弹出的状态压入重做栈。无可撤销时返回 None。
        """
        if not self.can_undo():
            return None
        current = self._undo.pop()
        self._redo.append(current)
        return copy.deepcopy(self._undo[-1][0])

    def redo(self) -> Any | None:
        """重做一步：从重做栈弹出一个状态压回撤销栈并返回。无则 None。"""
        if not self.can_redo():
            return None
        state = self._redo.pop()
        self._undo.append(state)
        return copy.deepcopy(state[0])

    def current(self) -> Any | None:
        if not self._undo:
            return None
        return copy.deepcopy(self._undo[-1][0])

    def current_label(self) -> str | None:
        if not self._undo:
            return None
        return self._undo[-1][1]

    def undo_labels(self) -> list[str | None]:
        """从旧到新列出撤销栈所有标签（含基线），供 UI 构建历史菜单。"""
        return [lbl for _, lbl in self._undo]

    def redo_labels(self) -> list[str | None]:
        return [lbl for _, lbl in self._redo]

    def __len__(self) -> int:
        return len(self._undo)


__all__ = ["UndoStack"]
