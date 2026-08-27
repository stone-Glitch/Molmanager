#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F15 日志过滤条控件（T05 / Phase 1）
──────────────────────────────────
挂在日志面板（``log_frame``）上方，提供：
    [级别下拉]  [🔎 关键词输入框（250ms 防抖）]  [清除]  [显示 X / 共 Y 条]

⚠️ 命名避让（架构 C8 / §6.1）
    ``filter_keyword_var`` 已被**文件列表过滤**占用，本控件所有变量一律带
    ``log_filter_`` 前缀，挂到 app 上的也是 ``app.log_filter_*``：
        app.log_filter_level_var / app.log_filter_keyword_var / app.log_filter_count_var
    快捷键同理避让为 ``Ctrl+Shift+F``（``Ctrl+F`` 归文件列表）。

实现要点：
  - 真正的过滤逻辑全在 utils/log_filter（纯函数）+ GuiLogHandler.set_filter，
    本控件只负责「收集用户输入 → set_filter → repaint_all → 刷新计数」；
  - 关键词输入 250ms 防抖，避免每敲一个字就全量重绘 5 万条；
  - 过滤条件持久化到 config["log_filter"]，但只在**值真的变了**时才落盘，
    避免高频写配置文件；
  - 所有外部调用都包 try/except：过滤条坏掉绝不能让日志面板或主窗口崩。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from utils import log_filter
from utils.logger import default_logger as logger
from utils.logger import get_gui_handler


#: 关键词输入防抖窗口（毫秒）。架构 §3.1 实测 5 万条全量重绘 <120ms，250ms 足够。
DEBOUNCE_MS: int = 250

#: 计数标签自动刷新周期（毫秒）。日志是持续流入的，光靠事件驱动会让计数发呆。
COUNT_REFRESH_MS: int = 1000


class LogFilterBar(tk.Frame):
    """日志过滤条。父容器应为日志面板的 ``log_frame``。"""

    def __init__(self, master: Any, app: Any, colors: dict | None = None) -> None:
        self.app = app
        self._colors = colors or {}
        bg = self._color("card_bg", "#161B22")
        super().__init__(master, bg=bg)

        self._debounce_job: str | None = None
        self._count_job: str | None = None
        self._destroyed = False
        # 最近一次已持久化的取值，用于避免重复写配置
        self._persisted: tuple[str, str] = ("", "")

        fonts = getattr(app, "_fonts", {}) or {}
        base_font = fonts.get("BASE", ("Microsoft YaHei", 13))
        bold_font = fonts.get("BOLD", ("Microsoft YaHei", 13, "bold"))

        init_level, init_keyword = self._load_initial()

        # ---- Tk 变量（挂到 app 上，便于快捷键 / 其他模块访问）----
        self.level_var = tk.StringVar(value=log_filter.level_label(init_level))
        self.keyword_var = tk.StringVar(value=init_keyword)
        self.count_var = tk.StringVar(value="显示 0 / 共 0 条")
        app.log_filter_level_var = self.level_var
        app.log_filter_keyword_var = self.keyword_var
        app.log_filter_count_var = self.count_var

        # ---- 级别下拉 ----
        tk.Label(
            self, text="级别:", bg=bg, fg=self._color("text", "#E6EDF3"), font=base_font,
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.level_combo = ttk.Combobox(
            self,
            textvariable=self.level_var,
            values=[log_filter.LEVEL_LABELS[k] for k in log_filter.LEVEL_ORDER],
            state="readonly",
            width=16,
            font=base_font,
        )
        self.level_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_changed)

        # ---- 关键词输入 ----
        tk.Label(
            self, text="🔎 关键词:", bg=bg, fg=self._color("text", "#E6EDF3"), font=base_font,
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.keyword_entry = ttk.Entry(
            self, textvariable=self.keyword_var, width=24, font=base_font,
        )
        self.keyword_entry.pack(side=tk.LEFT, padx=(0, 6))
        self.keyword_entry.bind("<KeyRelease>", self._on_keyword_typed)
        self.keyword_entry.bind("<Return>", self._on_keyword_commit)
        app.log_filter_keyword_entry = self.keyword_entry

        ttk.Button(self, text="清除", width=6, command=self.reset).pack(side=tk.LEFT, padx=(0, 10))

        # ---- 计数 ----
        tk.Label(
            self, textvariable=self.count_var, bg=bg,
            fg=self._color("primary", "#2DD4BF"), font=bold_font,
        ).pack(side=tk.LEFT)

        # ---- 应用初始过滤条件 ----
        self._persisted = (log_filter.normalize_level(init_level), init_keyword)
        self.apply(persist=False, repaint=False)
        self._schedule_count_refresh()
        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------ 内部工具

    def _color(self, key: str, fallback: str) -> str:
        try:
            return str(self._colors.get(key, fallback))
        except Exception:
            return fallback

    def _load_initial(self) -> tuple[str, str]:
        """从 config 读取上次的过滤条件。任何异常都回落到默认值。"""
        try:
            cfg = getattr(self.app, "config_data", {}) or {}
            section = cfg.get("log_filter", {}) or {}
            level = log_filter.normalize_level(section.get("level", log_filter.DEFAULT_LEVEL))
            keyword = str(section.get("keyword", log_filter.DEFAULT_KEYWORD) or "")
            return level, keyword
        except Exception:
            return log_filter.normalize_level(log_filter.DEFAULT_LEVEL), ""

    def current_level(self) -> str:
        """返回当前选中的级别键（英文，如 ``INFO`` / ``ALL``）。"""
        try:
            return log_filter.normalize_level(self.level_var.get())
        except Exception:
            return log_filter.LEVEL_ALL

    def current_keyword(self) -> str:
        try:
            return str(self.keyword_var.get() or "")
        except Exception:
            return ""

    # ------------------------------------------------------------ 事件

    def _on_level_changed(self, _event: Any = None) -> None:
        self._cancel_debounce()
        self.apply()

    def _on_keyword_typed(self, _event: Any = None) -> None:
        """按键后启动 / 重置防抖计时器。"""
        self._cancel_debounce()
        try:
            self._debounce_job = self.after(DEBOUNCE_MS, self._on_debounce_fire)
        except Exception:
            # after 不可用（窗口销毁中）→ 直接应用，不留悬挂状态
            self.apply()

    def _on_debounce_fire(self) -> None:
        self._debounce_job = None
        self.apply()

    def _on_keyword_commit(self, _event: Any = None) -> str:
        """回车立即生效（不等防抖）。"""
        self._cancel_debounce()
        self.apply()
        return "break"

    def _cancel_debounce(self) -> None:
        job, self._debounce_job = self._debounce_job, None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except Exception:
            pass

    def _on_destroy(self, event: Any = None) -> None:
        """控件销毁：取消所有挂起的 after 回调，避免 TclError 噪音。"""
        try:
            if event is not None and event.widget is not self:
                return
        except Exception:
            pass
        self._destroyed = True
        self._cancel_debounce()
        job, self._count_job = self._count_job, None
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass

    # ------------------------------------------------------------ 核心动作

    def apply(self, *, persist: bool = True, repaint: bool = True) -> None:
        """
        把当前 UI 取值推给 GuiLogHandler 并重绘日志面板。

        契约：内部全程 try/except，过滤失败只记 warning，绝不冒泡到 Tk 事件循环。
        """
        level = self.current_level()
        keyword = self.current_keyword()
        try:
            handler = get_gui_handler()
            if handler is not None:
                handler.set_filter(level=level, keyword=keyword)
                if repaint:
                    handler.repaint_all()
        except Exception as exc:
            logger.warning("⚠️ 应用日志过滤失败（日志显示不受影响）: %s", exc)
        self.refresh_count()
        if persist:
            self._persist(level, keyword)

    def reset(self) -> None:
        """清空过滤条件，恢复「全部 + 无关键词」。"""
        self._cancel_debounce()
        try:
            self.level_var.set(log_filter.LEVEL_LABELS[log_filter.LEVEL_ALL])
            self.keyword_var.set("")
        except Exception:
            pass
        self.apply()

    def refresh_count(self) -> None:
        """刷新「显示 X / 共 Y 条」计数标签。"""
        try:
            handler = get_gui_handler()
            if handler is None:
                self.count_var.set("显示 0 / 共 0 条")
                return
            shown, total = handler.count_visible()
            self.count_var.set(f"显示 {shown} / 共 {total} 条")
        except Exception:
            # 计数只是辅助信息，失败静默
            pass

    def _schedule_count_refresh(self) -> None:
        """周期性刷新计数（日志持续流入时保持数字新鲜）。"""
        if self._destroyed:
            return
        try:
            self.refresh_count()
        except Exception:
            pass
        try:
            self._count_job = self.after(COUNT_REFRESH_MS, self._schedule_count_refresh)
        except Exception:
            self._count_job = None

    def _persist(self, level: str, keyword: str) -> None:
        """把过滤条件写回 config（仅当值变化时落盘）。"""
        if (level, keyword) == self._persisted:
            return
        self._persisted = (level, keyword)
        try:
            cfg = getattr(self.app, "config_data", None)
            if not isinstance(cfg, dict):
                return
            section = cfg.get("log_filter")
            if not isinstance(section, dict):
                section = {}
                cfg["log_filter"] = section
            section["level"] = level
            section["keyword"] = keyword
            from utils.config import save_config
            save_config(cfg)
        except Exception as exc:
            logger.debug("持久化日志过滤条件失败（非致命）: %s", exc)

    def focus_keyword(self) -> None:
        """Ctrl+Shift+F 调用：聚焦关键词输入框并全选。"""
        try:
            self.keyword_entry.focus_set()
            self.keyword_entry.select_range(0, "end")
            self.keyword_entry.icursor("end")
        except Exception as exc:
            logger.debug("聚焦日志过滤框失败: %s", exc)


def build_log_filter_bar(app: Any, parent: Any, colors: dict | None = None) -> LogFilterBar | None:
    """
    工厂函数：创建过滤条并挂到 ``app.log_filter_bar``。

    失败时返回 None 并记 warning —— 过滤条是增值功能，建不出来也不能拖垮 build_ui。
    """
    try:
        bar = LogFilterBar(parent, app, colors=colors)
        bar.pack(fill=tk.X, padx=8, pady=(0, 6))
        app.log_filter_bar = bar
        return bar
    except Exception as exc:
        logger.warning("⚠️ 日志过滤条创建失败（日志面板仍可正常使用）: %s", exc)
        return None


__all__ = ["LogFilterBar", "build_log_filter_bar", "DEBOUNCE_MS", "COUNT_REFRESH_MS"]
