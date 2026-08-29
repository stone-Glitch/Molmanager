"""
F07 错误诊断弹窗（设计落地 Phase 2）—— JSON 规则库驱动。

信任感关键交互（审查 6.2）：把「技术错误原文」翻译成「原因 + 可操作修复」，
并支持一键复制错误原文。规则库见同目录 error_patterns.json（与 HTML 原型同源）。

契约：
  - 纯 UI + json 读取，import 阶段不依赖 psi4 / OpenBabel；
  - show_error_diagnosis() 为**非模态** Toplevel（不阻塞主窗口）；
  - 「一键修复」按钮当前为演示占位（toast 提示），真实修复逻辑需在 tkinter 端
    按命中规则实现（已在设计文档 7 落地表标注），不影响诊断展示本身。
"""

from __future__ import annotations

import json
import os
import re
import tkinter as tk

import ui.ui_theme as ui_theme
from ui.ui_theme import get_current_theme

_RULES_CACHE = None


def load_rules():
    """读取并缓存 error_patterns.json；读取失败返回空规则列表（降级为兜底命中）。"""
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    rules = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "error_patterns.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("rules", []):
            try:
                r["_re"] = re.compile(r.get("pattern", ".*"), re.IGNORECASE)
            except Exception:
                r["_re"] = re.compile(".*", re.IGNORECASE)
            rules.append(r)
    except Exception:
        # 规则库缺失：保留一个兜底规则，保证诊断弹窗永远能用
        rules = [
            {
                "id": "__default__",
                "pattern": ".*",
                "title": "未匹配到已知规则",
                "suggestion": "错误未被规则库命中，请把原文复制给开发者。",
                "fix": "先点「环境诊断」确认依赖可用。",
                "_re": re.compile(".*", re.IGNORECASE),
            }
        ]
    _RULES_CACHE = rules
    return rules


def match_rule(error_text: str):
    """返回命中规则 dict（找不到则 __default__）。"""
    text = error_text or ""
    rules = load_rules()
    for r in rules:
        try:
            if r["_re"].search(text):
                return r
        except Exception:
            continue
    # 理论上 __default__ 的 .* 一定命中，这里再兜底一次
    for r in rules:
        if r.get("id") == "__default__":
            return r
    return {"id": "__default__", "title": "未匹配到已知规则", "suggestion": "", "fix": ""}


def show_error_diagnosis(app, error_text: str, summary: str = None, hint: str = None):
    """错误诊断弹窗入口。error_text 为原始错误；summary/hint 为可选的大白话翻译。"""
    try:
        _ErrorDiagnosisDialog(app, error_text, summary, hint)
    except Exception as exc:
        # 弹窗自身失败不应静默吞掉原始错误：退回最朴素 messagebox
        try:
            from tkinter import messagebox as _mb

            _mb.showerror(
                summary or "出错啦", f"{error_text}\n\n（诊断弹窗启动失败：{exc}）", parent=getattr(app, "root", app)
            )
        except Exception:
            pass


class _ErrorDiagnosisDialog:
    def __init__(self, app, error_text, summary, hint):
        self.app = app
        self.error_text = (error_text or "").strip()
        rule = match_rule(self.error_text)
        self.rule = rule

        root = tk.Toplevel(getattr(app, "root", app) if hasattr(app, "root") else app)
        self.root = root
        root.title("🩺 错误诊断  ·  F07")
        root.transient(app if isinstance(app, tk.Widget) else getattr(app, "root", app))
        root.grab_set()
        root.resizable(True, True)

        P = ui_theme.get_palette()
        root.configure(bg=P["bg"])

        # —— 几何：相对主窗口居中 ——
        try:
            aw, ah = app.winfo_width(), app.winfo_height()
            ax, ay = app.winfo_rootx(), app.winfo_rooty()
        except Exception:
            aw, ah, ax, ay = 1100, 780, 100, 100
        W, H = 540, 460
        x = max(0, ax + (aw - W) // 2)
        y = max(0, ay + (ah - H) // 2)
        root.geometry(f"{W}x{H}+{x}+{y}")

        # —— 顶部标题条 ——
        title_bar = tk.Frame(root, bg=P["surface"], bd=0)
        title_bar.pack(fill=tk.X, padx=0, pady=0)
        tk.Label(
            title_bar,
            text="🩺 错误诊断",
            bg=P["surface"],
            fg=P["text"],
            font=("Microsoft YaHei", 14, "bold"),
            anchor="w",
            padx=14,
            pady=10,
        ).pack(side=tk.LEFT)

        # —— 主体滚动区 ——
        body = tk.Frame(root, bg=P["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 8))

        # 1) 大白话摘要（来自 friendly_error 翻译）
        if summary:
            tk.Label(
                body,
                text=summary,
                bg=P["bg"],
                fg=P["accent"],
                font=("Microsoft YaHei", 12, "bold"),
                anchor="w",
                wraplength=W - 40,
                justify="left",
            ).pack(fill=tk.X, pady=(2, 2))
        if hint:
            tk.Label(
                body,
                text=hint,
                bg=P["bg"],
                fg=P["text_secondary"],
                font=("Microsoft YaHei", 11),
                anchor="w",
                wraplength=W - 40,
                justify="left",
            ).pack(fill=tk.X, pady=(0, 8))

        # 2) 命中规则标题
        badge_bg = P["accent"] if rule.get("id") != "__default__" else P["warning"]
        badge = tk.Frame(body, bg=badge_bg, bd=0)
        badge.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            badge,
            text=f"诊断：{rule.get('title', '未知')}",
            bg=badge_bg,
            fg=P["btn_text"],
            font=("Microsoft YaHei", 12, "bold"),
            anchor="w",
            padx=10,
            pady=6,
        ).pack(fill=tk.X)

        # 3) 原因
        tk.Label(
            body, text="可能原因", bg=P["bg"], fg=P["text"], font=("Microsoft YaHei", 11, "bold"), anchor="w"
        ).pack(fill=tk.X, pady=(6, 2))
        _reason = tk.Message(
            body,
            text=rule.get("suggestion", ""),
            bg=P["surface"],
            fg=P["text"],
            font=("Microsoft YaHei", 11),
            width=W - 40,
            relief=tk.FLAT,
            bd=0,
        )
        _reason.pack(fill=tk.X, pady=(0, 8))

        # 4) 错误原文（等宽、可滚动、可复制）
        tk.Label(
            body, text="错误原文（可复制）", bg=P["bg"], fg=P["text"], font=("Microsoft YaHei", 11, "bold"), anchor="w"
        ).pack(fill=tk.X, pady=(2, 2))
        txt_frame = tk.Frame(
            body, bg=P["input"], bd=1, relief=tk.SOLID, highlightbackground=P["border"], highlightthickness=1
        )
        txt_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.err_txt = tk.Text(
            txt_frame,
            bg=P["input"],
            fg=P["danger"],
            insertbackground=P["text"],
            relief=tk.FLAT,
            bd=0,
            font=("Consolas", 10),
            wrap=tk.WORD,
            height=6,
        )
        self.err_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.err_txt.insert("1.0", self.error_text or "（无错误原文）")
        self.err_txt.configure(state=tk.DISABLED)
        tsb = tk.Scrollbar(
            txt_frame, command=self.err_txt.yview, bg=P["surface"], troughcolor=P["bg"], bd=0, relief=tk.FLAT
        )
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.err_txt.config(yscrollcommand=tsb.set)

        # —— 底部按钮行 ——
        foot = tk.Frame(root, bg=P["bg"])
        foot.pack(fill=tk.X, padx=14, pady=(0, 12))

        # 一键修复（真实逻辑见 ui/dialogs/fix_engine.apply_fix）
        fix_action = (rule.get("fix") or {}).get("action", "none")
        if fix_action == "none":
            # 该规则没有可执行的自动修复：禁用按钮并改为提示
            fix_btn = tk.Button(
                foot,
                text="🔧 需手动处理",
                relief=tk.FLAT,
                bd=0,
                bg=P["surface"],
                fg=P["text_muted"],
                font=("Microsoft YaHei", 12, "bold"),
                state=tk.DISABLED,
                padx=14,
                pady=7,
            )
        else:
            fix_btn = tk.Button(
                foot,
                text="🔧 一键修复",
                command=self._on_fix,
                relief=tk.FLAT,
                bd=0,
                bg=P["success"],
                fg=P["btn_text"],
                activebackground="#56D364" if get_current_theme() == "dark" else "#15803D",
                activeforeground=P["btn_text"],
                font=("Microsoft YaHei", 12, "bold"),
                cursor="hand2",
                padx=14,
                pady=7,
            )
        fix_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # 复制错误
        copy_btn = tk.Button(
            foot,
            text="📋 复制错误",
            command=self._on_copy,
            relief=tk.SOLID,
            bd=1,
            bg=P["elevated"],
            fg=P["text"],
            activebackground=P["border"],
            activeforeground=P["accent"],
            font=("Microsoft YaHei", 11),
            cursor="hand2",
            padx=12,
            pady=7,
        )
        copy_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # 兜底规则的「打开环境诊断」入口
        if rule.get("id") == "__default__":
            env_btn = tk.Button(
                foot,
                text="🧪 环境诊断",
                command=self._on_env,
                relief=tk.SOLID,
                bd=1,
                bg=P["elevated"],
                fg=P["text"],
                activebackground=P["border"],
                activeforeground=P["accent"],
                font=("Microsoft YaHei", 11),
                cursor="hand2",
                padx=12,
                pady=7,
            )
            env_btn.pack(side=tk.RIGHT, padx=(6, 0))

        close_btn = tk.Button(
            foot,
            text="关闭",
            command=self._on_close,
            relief=tk.SOLID,
            bd=1,
            bg=P["elevated"],
            fg=P["text"],
            activebackground=P["border"],
            activeforeground=P["accent"],
            font=("Microsoft YaHei", 11),
            cursor="hand2",
            padx=12,
            pady=7,
        )
        close_btn.pack(side=tk.LEFT)

        root.bind("<Escape>", lambda e: self._on_close())
        root.after(0, lambda: root.focus_set())

    # ---------- 行为 ----------
    def _on_fix(self):
        # 真实修复：按命中规则的 fix.action 执行（ui/dialogs/fix_engine.apply_fix）。
        try:
            from ui.dialogs import fix_engine

            ok, msg = fix_engine.apply_fix(self.app, self.rule, self.error_text)
        except Exception as e:
            ok, msg = False, f"修复失败：{e}"
        # 把结果作为友好日志反馈到主窗口
        try:
            fn = getattr(getattr(self.app, "helpers", None), "on_log", None)
            if callable(fn):
                fn(msg, "success" if ok else "warning")
        except Exception:
            pass
        self._on_close()

    def _on_copy(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.error_text)
            try:
                fn = getattr(getattr(self.app, "helpers", None), "on_log", None)
                if callable(fn):
                    fn("📋 错误原文已复制到剪贴板", "success")
            except Exception:
                pass
        except Exception:
            pass

    def _on_env(self):
        self._on_close()
        try:
            fn = getattr(self.app, "show_environment_dialog_from_menu", None)
            if callable(fn):
                fn()
        except Exception:
            pass

    def _on_close(self):
        try:
            self.root.grab_release()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
