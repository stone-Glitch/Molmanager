import tkinter as tk
from tkinter import ttk

from ui.ui_theme import (
    COLORS,
)

# ------------------------- 🎨 主题颜色常量 -------------------------
from ._theme import add_tooltip


def build_toolbar(app, parent):
    """
    顶部工具栏：工作目录显示 + 最近目录 + 扫描/刷新 + 撤销/重做，
    进度条放到状态栏（底部），新手的主要动作集中在各标签页。
    """
    # 取字体（问题一：字太小）
    F = getattr(app, "_fonts", {})
    F.get("BASE", ("Microsoft YaHei", 12))
    BOLD = F.get("BOLD", ("Microsoft YaHei", 12, "bold"))
    SMALL_BTN = F.get("BTN2", ("Microsoft YaHei", 12))
    ENTRY = F.get("ENTRY", ("Microsoft YaHei", 12))
    HINT_BTN = F.get("SMALL", ("Microsoft YaHei", 11))

    bar = tk.Frame(
        parent,
        bg=COLORS["card_bg"],
        bd=1,
        relief=tk.SOLID,
        highlightbackground=COLORS["card_border"],
        highlightthickness=1,
    )
    bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
    bar.grid_columnconfigure(2, weight=1)

    # —— 列 0：工作目录 ——
    tk.Label(bar, text=" 📂 工作目录:", bg=COLORS["card_bg"], fg=COLORS["text"], font=BOLD).grid(
        row=0, column=0, sticky="w", padx=8, pady=6
    )
    app.work_dir_entry = ttk.Entry(bar, textvariable=app.work_dir_var, font=ENTRY, width=38)
    app.work_dir_entry.grid(row=0, column=1, sticky="w", padx=(0, 6), pady=6)

    def _row0_btn(text, cmd, bg=None, fg=None, tip=""):
        style_kw = {}
        if bg:
            style_kw.update(
                bg=bg, fg=fg or COLORS["btn_text"], activebackground=bg, activeforeground=fg or COLORS["btn_text"]
            )
        b = tk.Button(
            bar,
            text=text,
            command=cmd,
            relief=tk.RAISED,
            bd=1,
            padx=10,
            pady=5,
            font=SMALL_BTN,
            cursor="hand2",
            **style_kw,
        )
        if tip:
            add_tooltip(b, tip, font=HINT_BTN)
        return b

    _row0_btn("浏览…", app.controller.browse_work_dir, tip="选择新的工作目录并扫描文件").grid(
        row=0, column=2, sticky="w", padx=2, pady=6
    )
    try:
        _row0_btn("🕘 最近", app.controller.show_recent_dirs_dialog, tip="从最近打开的工作目录中切换").grid(
            row=0, column=3, sticky="w", padx=2, pady=6
        )
    except Exception:
        pass

    # —— 分隔 ——
    tk.Frame(bar, bg=COLORS["card_border"], width=2).grid(row=0, column=4, sticky="ns", padx=8, pady=4)

    # —— 列：扫描 / 刷新 ——
    _row0_btn(
        "🔍 扫描文件", app.controller.scan_files, bg=COLORS["btn_info_bg"], tip="重新扫描工作目录下的所有计算文件"
    ).grid(row=0, column=5, sticky="w", padx=2, pady=6)

    tk.Frame(bar, bg=COLORS["card_border"], width=2).grid(row=0, column=7, sticky="ns", padx=8, pady=4)

    # —— 列：撤销 / 重做 ——
    _row0_btn("↩ 撤销", app.controller.undo_last, tip="撤销上一步文件操作（重命名/移动/整理等）").grid(
        row=0, column=8, sticky="w", padx=2, pady=6
    )
    try:
        _row0_btn("↪ 重做", app.controller.redo_last, tip="重做被撤销的操作").grid(
            row=0, column=9, sticky="w", padx=2, pady=6
        )
    except Exception:
        pass

    # —— 列：文件类型过滤入口 ——
    tk.Frame(bar, bg=COLORS["card_border"], width=2).grid(row=0, column=10, sticky="ns", padx=8, pady=4)
    tk.Label(
        bar,
        text="文件类型:",
        bg=COLORS["card_bg"],
        fg=COLORS["text_light"],
        font=getattr(app, "_fonts", {}).get("SMALL", ("Microsoft YaHei", 11)),
    ).grid(row=0, column=11, sticky="w", padx=(0, 4), pady=6)
    app.ext_display_var = tk.StringVar()
    app.helpers.update_ext_display()
    tk.Label(
        bar,
        textvariable=app.ext_display_var,
        bg=COLORS["surface"],
        fg=COLORS["accent"],
        font=getattr(app, "_fonts", {}).get("LOG", ("Consolas", 12)),
        relief=tk.SUNKEN,
        padx=10,
        pady=2,
    ).grid(row=0, column=12, sticky="w", padx=(0, 4), pady=6)
    _row0_btn("选择…", app.controller.show_ext_filter_dialog, tip="调整需要显示/扫描的文件扩展名").grid(
        row=0, column=13, sticky="w", padx=2, pady=6
    )

    # —— 命令面板入口（设计落地 Phase 1）——
    try:
        from ui.command_palette import open_command_palette as _open_cmd_palette

        _row0_btn(
            "⌘ 命令面板",
            lambda: _open_cmd_palette(app),
            bg=COLORS["accent"],
            fg=COLORS["btn_text"],
            tip="Ctrl/Cmd+K 唤起命令面板：动作 / 导航 / 文件一搜即达",
        ).grid(row=0, column=14, sticky="w", padx=(10, 2), pady=6)
    except Exception as _cpe:
        import traceback as _tb

        print("[ui_builder] 命令面板按钮构建失败（已跳过）:", _tb.format_exc())  # noqa: T201

    # —— 主题 / 密度快速切换（设计落地 Phase 3，复用 ui_theme 助手）——
    try:
        from ui.ui_theme import toggle_density as _toggle_density
        from ui.ui_theme import toggle_theme as _toggle_theme

        _row0_btn("🌓 主题", lambda: _toggle_theme(app), tip="切换深 / 浅色主题（即时生效并记忆）").grid(
            row=0, column=15, sticky="w", padx=2, pady=6
        )
        _row0_btn("📐 密度", lambda: _toggle_density(app), tip="切换舒适 / 紧凑信息密度（即时重排并记忆）").grid(
            row=0, column=16, sticky="w", padx=2, pady=6
        )
    except Exception as _te:
        import traceback as _tb

        print("[ui_builder] 主题/密度按钮构建失败（已跳过）:", _tb.format_exc())  # noqa: T201


# ===========================================================
# 📁 Tab1：文件管理（新手默认页面）
# ===========================================================
