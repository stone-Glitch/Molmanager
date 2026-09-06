"""🏠 工作台页（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import tkinter as tk

from ui.theme_tokens import SPACING
from ui.ui_theme import COLORS, themed_button


def build_tab_dashboard(app, parent):
    """🏠 工作台：概览统计（4 卡）+ 快捷操作（界面方案新增落地页）。"""
    F = getattr(app, "_fonts", {})
    f_h1 = F.get("H1", ("Microsoft YaHei", 20, "bold"))
    f_bold = F.get("BOLD", ("Microsoft YaHei", 14, "bold"))
    f_base = F.get("BASE", ("Microsoft YaHei", 13))
    f_small = F.get("SMALL", ("Microsoft YaHei", 12))
    f_num = ("Microsoft YaHei", 24, "bold")

    tk.Label(parent, text="工作台", bg=COLORS["bg"], fg=COLORS["text"], font=f_h1, anchor="w").pack(
        anchor="w", padx=SPACING["xl"], pady=(SPACING["lg"], SPACING["xs"])
    )
    tk.Label(
        parent,
        text="这里是你所有分子与计算任务的入口，一键直达高频操作。",
        bg=COLORS["bg"],
        fg=COLORS["text_secondary"],
        font=f_base,
        anchor="w",
    ).pack(anchor="w", padx=20, pady=(0, SPACING["md"]))

    # —— 统计卡（4 张，读 last_scan_result）——
    stats = tk.Frame(parent, bg=COLORS["bg"])
    stats.pack(fill="x", padx=SPACING["xl"], pady=SPACING["xs"])
    app._dash_vars = {}
    cards = (
        ("文件总数", "total", COLORS["text"]),
        ("待重命名", "pending", COLORS["warning"]),
        ("无映射", "unmapped", COLORS["danger"]),
        ("已正确命名", "named", COLORS["success"]),
    )
    for idx, (label, key, color) in enumerate(cards):
        card = tk.Frame(
            stats,
            bg=COLORS["surface"],
            bd=0,
            relief=tk.FLAT,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.grid(row=0, column=idx, sticky="ew", padx=6)
        stats.grid_columnconfigure(idx, weight=1, uniform="dash")
        tk.Label(card, text=label, bg=COLORS["surface"], fg=COLORS["text_secondary"], font=f_small).pack(
            anchor="w", padx=16, pady=(14, 2)
        )
        var = tk.StringVar(value="0")
        tk.Label(card, textvariable=var, bg=COLORS["surface"], fg=color, font=f_num).pack(
            anchor="w", padx=16, pady=(0, SPACING["md"])
        )
        app._dash_vars[key] = var

    # —— 快捷操作 ——
    tk.Label(parent, text="快捷操作", bg=COLORS["bg"], fg=COLORS["text"], font=f_bold, anchor="w").pack(
        anchor="w", padx=20, pady=(SPACING["xl"], SPACING["sm"])
    )
    quick = tk.Frame(parent, bg=COLORS["bg"])
    quick.pack(fill="x", padx=SPACING["xl"], pady=SPACING["xs"])

    def _safe(fn):
        try:
            fn()
        except Exception:
            pass

    actions = (
        ("📥 导入文件", lambda: _safe(app.controller.import_files_from_dialog)),
        ("🗂️ 建立映射", lambda: _safe(app.controller.show_mapping_editor_dialog)),
        ("⚡ 运行计算", lambda: _safe(app.controller.show_psi4_dialog)),
        ("🔬 转换工具", lambda: _safe(app.controller.show_openbabel_dialog)),
    )
    for idx, (label, cmd) in enumerate(actions):
        themed_button(quick, label, cmd, "primary" if idx == 0 else "secondary").grid(
            row=0, column=idx, sticky="ew", padx=6, pady=4
        )
        quick.grid_columnconfigure(idx, weight=1, uniform="quick")

    # —— 统计刷新（scan 完成后 / 切到本页时调用）——
    def _refresh_dashboard():
        try:
            entries = getattr(app, "last_scan_result", []) or []
            counts = {"total": len(entries), "pending": 0, "unmapped": 0, "named": 0}
            for e in entries:
                st = e.get("status", "")
                if st in ("⏳ 待重命名", "⏳ 纯中文，待修复"):
                    counts["pending"] += 1
                elif st == "❌ 无映射":
                    counts["unmapped"] += 1
                elif st == "✅ 已正确命名":
                    counts["named"] += 1
            for key, var in (getattr(app, "_dash_vars", {}) or {}).items():
                var.set(str(counts.get(key, 0)))
        except Exception:
            pass

    app.refresh_dashboard = _refresh_dashboard
    _refresh_dashboard()
