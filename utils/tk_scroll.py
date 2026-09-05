"""
跨平台 Tk 鼠标滚轮滚动工具。

背景
----
Tk 的滚轮事件在各平台上是**两套完全不同的事件**：

  - Windows / macOS：触发 ``<MouseWheel>``，``event.delta`` 为 ±120（或其倍数），
    向上滚动为正值；触控板可能产生很小的 delta（如 12、30）。
  - Linux / X11：触发 ``<Button-4>``（向上）/ ``<Button-5>``（向下），
    ``event.num`` 为 4 / 5，而 ``event.delta`` 恒为 0。

本项目原先只绑定 ``<MouseWheel>``，导致 **Linux 上所有滚动区域
（主界面 6 个标签页的滚动框、各类可滚动对话框）滚轮滚动完全失效**。
本模块把差异归一化，一次绑定即可三平台通用。

归一化约定
----------
``_delta_from_event()`` 统一返回「滚动单元数」：**正数 = 向下/向右**。

用法
----
    bind_mousewheel(canvas)               # 纵向滚动；Shift+滚轮 → 横向
    bind_mousewheel(canvas, orient="x")   # 始终横向滚动
"""

from __future__ import annotations

import tkinter as tk

# Shift 键掩码：与 _make_scrolled_frame 原逻辑一致，用 state & 0x0001 判断
_SHIFT = 0x0001

# 三平台全覆盖的事件序列
_WHEEL_SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def _delta_from_event(evt) -> int:
    """把平台各异的滚轮事件归一化为滚动单元数（正数 = 向下/向右）。

    - Windows/macOS：``delta`` 非零，向上为正 → 取反使其「向下为正」。
      触控板小 delta 会导致 int(x/120) == 0（滚不动），此处兜底为 ±1。
    - Linux/X11：``delta`` 为 0，改用 ``num``：4 = 向上，5 = 向下。
    """
    try:
        d = int(getattr(evt, "delta", 0) or 0)
    except Exception:
        d = 0

    if d:
        n = int(-1 * (d / 120))
        if n == 0:
            # 高精度触控板：delta 绝对值小于 120，保证至少滚动一格
            n = -1 if d > 0 else 1
        return n

    # Linux / X11
    num = getattr(evt, "num", 0)
    if num == 4:
        return -1
    if num == 5:
        return 1
    return 0


def bind_mousewheel(widget: tk.Misc, orient: str = "y") -> callable:
    """给 ``widget`` 绑定跨平台滚轮滚动，返回处理函数（便于必要时 unbind）。

    参数：
      - widget：需支持 ``xview_scroll`` / ``yview_scroll`` 的控件（Canvas / Text /
        Listbox / Treeview / Entry / Spinbox 等）。
      - orient：``"y"``（默认）纵向；``"x"`` 始终横向。
        纵向模式下按住 Shift 会转为横向滚动（沿用原有交互习惯）。

    说明：滚轮是鼠标事件，Tk 会派发给指针下的控件，并沿 bindtags 向祖先传播，
    因此把绑定挂在 Canvas 上，指针停在其内部子控件（如工具栏按钮）时同样生效。
    """

    def _handler(evt):
        try:
            n = _delta_from_event(evt)
            if not n:
                return None
            shift = bool(getattr(evt, "state", 0) & _SHIFT)
            if orient == "x" or shift:
                widget.xview_scroll(n, "units")
            else:
                widget.yview_scroll(n, "units")
        except Exception:
            # 控件已销毁或不支持滚动：静默忽略，绝不影响界面其它功能
            pass
        # 阻断继续传播，避免同时滚动外层容器
        return "break"

    for seq in _WHEEL_SEQUENCES:
        try:
            widget.bind(seq, _handler)
        except Exception:
            pass
    return _handler
