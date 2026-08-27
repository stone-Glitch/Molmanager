# -*- coding: utf-8 -*-
"""
命令面板（Ctrl/Cmd+K）—— 设计落地 Phase 1，增强版。

定位（见《MolManager-界面设计方案》4.1）：
  - 效率交互入口，覆盖层 Toplevel，居中显示，输入即过滤；
  - 分组展示「动作 / 导航 / 文件」，文件组从 app.tree 实时读取（审查 6.1 动态绑定）；
  - ↑↓ 选择、Enter 执行、Esc / 点击遮罩关闭；支持鼠标点击。

本次增强（用户需求 ③）：
  - 搜索关键词在命令标签内**真实高亮**（hl 标签，命中片段加粗变色）；
  - **最近命令记忆**：执行过的命令持久化到配置，打开时置顶「最近使用」分组，一键重跑。

契约（与现有代码一致）：
  - 仅接线已存在的安全方法（getattr + callable 兜底），绝不引入新业务逻辑；
  - import 阶段只依赖 ui.ui_theme（仅 tkinter/json/os），不触碰 psi4 / OpenBabel，
    保证在缺量化依赖的环境下也能正常 import。
"""

from __future__ import annotations

import tkinter as tk

import ui.ui_theme as ui_theme


def _safe(app, name):
    """取 app 上可调用方法，不存在返回 None（避免命令面板因缺方法而崩）。"""
    fn = getattr(app, name, None)
    return fn if callable(fn) else None


def _load_recent(app):
    """从配置读取最近命令 [(group, label), ...]。"""
    try:
        raw = (getattr(app, "config_data", {}) or {}).get("cmd_recent", []) or []
        return [tuple(x) for x in raw if isinstance(x, (list, tuple)) and len(x) == 2]
    except Exception:
        return []


def _push_recent(app, c):
    """执行命令后写入最近记录（去重、最多 8 条、持久化）。"""
    try:
        key = [c["group"], c["label"]]
        cd = getattr(app, "config_data", None)
        if not isinstance(cd, dict):
            return
        raw = [list(k) for k in (cd.get("cmd_recent", []) or []) if list(k) != key]
        raw.insert(0, key)
        cd["cmd_recent"] = raw[:8]
        from utils.config import save_config
        save_config(cd)
    except Exception:
        pass


def open_command_palette(app):
    """Ctrl/Cmd+K 入口：已打开则置顶聚焦，否则新建覆盖层。"""
    try:
        for w in app.winfo_children():
            if getattr(w, "_is_cmd_palette", False):
                w.lift()
                try:
                    w.focus_set()
                except Exception:
                    pass
                return
    except Exception:
        pass
    _CommandPalette(app)


def _build_commands(app):
    """构造命令清单。文件组在每次打开时实时从 app.tree 读取。"""
    cmds = []

    # —— 动作 ——
    def _act(label, hint, fn):
        if fn is not None:
            cmds.append({"group": "动作", "label": label, "hint": hint, "run": fn})

    _act("扫描 / 刷新文件", "Ctrl+F", _safe(app, "controller") and app.controller.scan_files)
    _act("选择工作目录", "Ctrl+O", _safe(app, "controller") and app.controller.browse_work_dir)
    _act("一键修复全部", "", _safe(app, "controller") and app.controller.run_fix_by_mode)
    _act("按类型整理", "", _safe(app, "controller") and app.controller.organize_by_type)
    _act("打开反应动画", "Ctrl+G", _safe(app, "_on_ctrl_g"))
    _act("打开环境诊断", "", _safe(app, "show_environment_dialog_from_menu"))
    _act("字体大小设置", "", _safe(app, "show_font_size_dialog_from_menu"))
    _act("切换主题（深 / 浅）", "", lambda: ui_theme.toggle_theme(app))
    _act("切换信息密度（舒适 / 紧凑）", "", lambda: ui_theme.toggle_density(app))
    _act("显示快捷键帮助", "F1", _safe(app, "_show_help"))

    # —— 导航 ——
    nav = (
        ("去「工作台」", 0),
        ("去「文件管理」", 1),
        ("去「分子映射」", 2),
        ("去「计算与动画」", 3),
        ("去「高级工具」", 4),
        ("去「任务队列」", 5),
    )
    for label, idx in nav:
        cmds.append({
            "group": "导航",
            "label": label,
            "hint": "",
            "run": (lambda i=idx, lb=label: (app._show_page(i),
                                   _log(app, f"已切换到：{lb}"))),
        })

    # —— 文件（动态，从 tree 读取，审查 6.1）—— 放到最后，避免覆盖动作/导航
    try:
        tree = getattr(app, "tree", None)
        if tree is not None:
            for iid in tree.get_children():
                try:
                    name = tree.set(iid, "文件名") or ""
                except Exception:
                    name = ""
                if not name:
                    continue
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                cmds.append({
                    "group": "文件",
                    "label": name,
                    "hint": (". " + ext) if ext else "",
                    "run": (lambda i=iid, n=name: (
                        app._show_page(1),
                        _select_and_see(tree, i),
                        _log(app, f"定位：{n}"),
                    )),
                })
    except Exception:
        pass

    return cmds


def _log(app, msg):
    try:
        fn = getattr(getattr(app, "helpers", None), "on_log", None)
        if callable(fn):
            fn(msg, "info")
    except Exception:
        pass


def _select_and_see(tree, iid):
    try:
        tree.selection_set(iid)
        tree.see(iid)
    except Exception:
        pass


class _CommandPalette:
    def __init__(self, app):
        self.app = app
        self.all_cmds = _build_commands(app)

        # —— 最近命令：置顶「最近使用」分组 ——
        lookup = {(c["group"], c["label"]): c for c in self.all_cmds}
        self.recent_cmds = []
        _seen = set()
        for k in _load_recent(app):
            c = lookup.get(k)
            if c is not None and id(c) not in _seen:
                _seen.add(id(c))
                self.recent_cmds.append(c)
        self.recent_ids = {id(c) for c in self.recent_cmds}

        # 列表顺序：无查询时「最近 + 其余全部」；有查询时按全部过滤
        # 注意：recent_cmds 与 all_cmds 是同一批 dict 对象（id 相同），
        # 尾部必须按 id 去重，否则同一命令会在「最近使用」下重复出现两次。
        self.items = list(self.recent_cmds) + [c for c in self.all_cmds if id(c) not in self.recent_ids]
        self.query = ""
        self.sel = 0
        self.line_of_item = []     # items[i] 所在的 Text 行号（1-based）
        self.item_at_line = {}     # 行号 -> items 下标

        root = tk.Toplevel(app)
        self.root = root
        root._is_cmd_palette = True
        root.title("命令面板")
        root.transient(app)
        root.overrideredirect(True)  # 无边框覆盖层，自己画圆角观感
        root.grab_set()

        P = ui_theme.get_palette()

        # —— 几何：相对主窗口居中，宽约 560，高约 460 ——
        try:
            aw, ah = app.winfo_width(), app.winfo_height()
            ax, ay = app.winfo_rootx(), app.winfo_rooty()
        except Exception:
            aw, ah, ax, ay = 1100, 780, 100, 100
        W, H = 560, 460
        x = max(0, ax + (aw - W) // 2)
        y = max(0, ay + (ah - H) // 3)
        root.geometry(f"{W}x{H}+{x}+{y}")

        # —— 外框（细描边 + 阴影观感）——
        root.configure(bg=P["border_strong"])
        pad = tk.Frame(root, bg=P["border_strong"], bd=0)
        pad.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        inner = tk.Frame(pad, bg=P["surface"], bd=0)
        inner.pack(fill=tk.BOTH, expand=True)

        # —— 搜索框 ——
        head = tk.Frame(inner, bg=P["input"], bd=0)
        head.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(head, text="🔍", bg=P["input"], fg=P["text_secondary"],
                 font=("Microsoft YaHei", 13)).pack(side=tk.LEFT, padx=(6, 4))
        self.entry = tk.Entry(head, bg=P["input"], fg=P["text"],
                              insertbackground=P["text"], relief=tk.FLAT, bd=0,
                              font=("Microsoft YaHei", 13))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=6)
        self.entry.insert(0, "")
        self.entry.focus_set()

        # —— 列表（只读 Text，支持关键词高亮）——
        list_frame = tk.Frame(inner, bg=P["surface"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))

        self.text = tk.Text(list_frame, bg=P["surface"], fg=P["text"],
                             relief=tk.FLAT, bd=0, highlightthickness=0,
                             wrap=tk.NONE, font=("Microsoft YaHei", 12),
                             cursor="arrow", state=tk.DISABLED,
                             padx=4, pady=4)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 标签样式
        self.text.tag_configure("header", foreground=P["accent"],
                                font=("Microsoft YaHei", 11, "bold"))
        self.text.tag_configure("hl", foreground=P["accent"],
                                font=("Microsoft YaHei", 12, "bold"))
        self.text.tag_configure("selrow", background=P["accent"], foreground=P["bg"])

        vsb = tk.Scrollbar(list_frame, command=self.text.yview,
                           bg=P["surface"], troughcolor=P["bg"],
                           bd=0, relief=tk.FLAT, width=10)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.config(yscrollcommand=vsb.set)

        # —— 底部提示条 ——
        foot = tk.Frame(inner, bg=P["surface"], bd=0)
        foot.pack(fill=tk.X, padx=10, pady=(2, 8))
        self.foot_lbl = tk.Label(foot, text="", bg=P["surface"], fg=P["text_light"],
                                 font=("Microsoft YaHei", 10), anchor="w")
        self.foot_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(foot, text="↑↓ 选择   Enter 执行   Esc 关闭",
                 bg=P["surface"], fg=P["text_hint"],
                 font=("Microsoft YaHei", 10)).pack(side=tk.RIGHT)

        # —— 事件 ——
        self.entry.bind("<KeyRelease>", self._on_type)
        self.entry.bind("<Up>", self._on_up)
        self.entry.bind("<Down>", self._on_down)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Escape>", self._on_escape)
        self.text.bind("<Button-1>", self._on_click)
        self.text.bind("<Double-1>", self._on_double)
        root.bind("<Escape>", self._on_escape)
        root.bind("<FocusOut>", self._on_focus_out)

        self._render()
        root.after(0, lambda: (self.entry.focus_set(), self.entry.select_range(0, "end")))

    # ---------- 渲染 ----------
    def _render(self):
        P = ui_theme.get_palette()
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.line_of_item = []
        self.item_at_line = {}
        last_group = None
        q = (self.query or "").lower()
        for c in self.items:
            g = "最近使用" if id(c) in self.recent_ids else c["group"]
            if g != last_group:
                self.text.insert(tk.END, f"  {g}\n")
                hline = int(self.text.index(tk.END).split(".")[0]) - 1
                self.text.tag_add("header", f"{hline}.0", f"{hline}.end")
                last_group = g
            self._insert_item(c, q)
        self.text.configure(state=tk.DISABLED)
        if self.line_of_item:
            self.sel = max(0, min(self.sel, len(self.line_of_item) - 1))
        else:
            self.sel = 0
        self._apply_selection()
        self._update_foot()

    def _insert_item(self, c, q):
        label = c["label"]
        hint = c.get("hint")
        disp = (label + "   " + hint) if hint else label
        prefix = "  "
        self.text.insert(tk.END, prefix + disp + "\n")
        line = int(self.text.index(tk.END).split(".")[0]) - 1
        self.line_of_item.append(line)
        self.item_at_line[line] = len(self.line_of_item) - 1
        # 关键词高亮（仅命中的标签片段）
        if q and q in label.lower():
            low = label.lower()
            start = 0
            ql = len(q)
            while True:
                p = low.find(q, start)
                if p < 0:
                    break
                self.text.tag_add("hl", f"{line}.{2 + p}", f"{line}.{2 + p + ql}")
                start = p + ql

    def _apply_selection(self):
        for ln in self.line_of_item:
            self.text.tag_remove("selrow", f"{ln}.0", f"{ln}.end")
        if self.line_of_item and 0 <= self.sel < len(self.line_of_item):
            ln = self.line_of_item[self.sel]
            self.text.tag_add("selrow", f"{ln}.0", f"{ln}.end")
            self.text.see(f"{ln}.0")
        self._update_foot()

    def _update_foot(self):
        try:
            if self.line_of_item and 0 <= self.sel < len(self.items):
                c = self.items[self.sel]
                self.foot_lbl.config(text=f"{c['group']} · {c['label']}")
            else:
                self.foot_lbl.config(text=f"共 {len(self.items)} 条命令")
        except Exception:
            pass

    # ---------- 事件 ----------
    def _on_type(self, _e=None):
        self.query = self.entry.get().strip()
        if not self.query:
            self.items = list(self.recent_cmds) + [c for c in self.all_cmds if id(c) not in self.recent_ids]
        else:
            q = self.query.lower()
            self.items = [c for c in self.all_cmds
                         if q in c["label"].lower() or q in c["group"].lower()]
        self.sel = 0
        self._render()

    def _on_up(self, _e=None):
        if self.line_of_item:
            self.sel = max(0, self.sel - 1)
            self._apply_selection()
        return "break"

    def _on_down(self, _e=None):
        if self.line_of_item:
            self.sel = min(len(self.line_of_item) - 1, self.sel + 1)
            self._apply_selection()
        return "break"

    def _on_click(self, e):
        try:
            ln = int(self.text.index(f"@{e.x},{e.y}").split(".")[0])
            if ln in self.item_at_line:
                self.sel = self.item_at_line[ln]
                self._apply_selection()
        except Exception:
            pass

    def _on_double(self, e):
        try:
            ln = int(self.text.index(f"@{e.x},{e.y}").split(".")[0])
            if ln in self.item_at_line:
                self.sel = self.item_at_line[ln]
                self._run(self.items[self.sel])
        except Exception:
            pass

    def _on_enter(self, _e=None):
        if self.line_of_item and 0 <= self.sel < len(self.items):
            self._run(self.items[self.sel])
        return "break"

    def _on_escape(self, _e=None):
        self._close()
        return "break"

    def _on_focus_out(self, _e=None):
        try:
            if self.root.focus_get() is None:
                self._close()
        except Exception:
            pass

    def _run(self, c):
        try:
            _push_recent(self.app, c)
        except Exception:
            pass
        self._close()
        try:
            c["run"]()
        except Exception as exc:
            _log(self.app, f"⚠️ 命令执行失败：{exc}")

    def _close(self):
        try:
            self.root.grab_release()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
