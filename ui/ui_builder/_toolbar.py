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

    ===== 窄窗口防裁切（本次修复） =====
    原实现把 17 列固定宽度控件直接排进一个普通 Frame，且把 weight 给了「浏览」按钮列，
    窗口窄于约 1200px（多数笔记本）时右侧「命令面板 / 主题 / 密度」等按钮**被直接裁掉
    且无法触及**。现改为双层结构：
      · 内容放进「画布 + 仅在溢出时出现的横向滚动条」，任何窗口宽度下所有按钮都可达；
      · 工作目录输入框改为弹性列（weight=1），宽屏时自动吸收多余空间，不再拉伸按钮。
    """
    # 取字体（问题一：字太小）
    F = getattr(app, "_fonts", {})
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
    bar.grid_columnconfigure(0, weight=1)

    # —— 可横向滚动的视口：内容宽度超出视口时才出现滚动条 ——
    canvas = tk.Canvas(bar, bg=COLORS["card_bg"], highlightthickness=0, bd=0, height=40)
    canvas.grid(row=0, column=0, sticky="ew")
    hbar = ttk.Scrollbar(bar, orient=tk.HORIZONTAL, command=canvas.xview)
    canvas.configure(xscrollcommand=hbar.set)
    hbar.grid(row=1, column=0, sticky="ew")
    hbar.grid_remove()  # 默认隐藏；_sync 检测到溢出时再显示

    inner = tk.Frame(canvas, bg=COLORS["card_bg"])
    _win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    # 跨平台滚轮横向滚动（Windows/macOS <MouseWheel>；Linux/X11 <Button-4>/<Button-5>）
    try:
        from utils.tk_scroll import bind_mousewheel

        bind_mousewheel(canvas, orient="x")
    except Exception:
        pass

    def _sync(_e=None):
        """把画布尺寸贴合内容，并按需显示/隐藏横向滚动条。"""
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
            cw = canvas.winfo_width()
            iw = inner.winfo_reqwidth()
            ih = inner.winfo_reqheight()
            # 内容窄于视口 → 撑满；宽于视口 → 保持自然宽度，交由横向滚动条接管
            try:
                canvas.itemconfigure(_win_id, width=max(cw, iw))
            except Exception:
                pass
            # 画布高度贴合内容高度（仅在变化时设置，避免 <Configure> 无限循环）
            if ih > 1 and int(float(canvas.cget("height"))) != ih:
                canvas.configure(height=ih)
            # 溢出判断：显示 / 隐藏横向滚动条
            need_bar = iw > cw + 1
            if need_bar and not hbar.winfo_ismapped():
                hbar.grid()
            elif not need_bar and hbar.winfo_ismapped():
                hbar.grid_remove()
        except Exception:
            pass

    inner.bind("<Configure>", _sync)
    canvas.bind("<Configure>", _sync)

    # 弹性列：工作目录输入框（列 1）吸收多余空间，避免宽屏下按钮被拉伸变形
    inner.grid_columnconfigure(1, weight=1)

    # —— 列 0：工作目录 ——
    tk.Label(inner, text=" 📂 工作目录:", bg=COLORS["card_bg"], fg=COLORS["text"], font=BOLD).grid(
        row=0, column=0, sticky="w", padx=8, pady=6
    )
    app.work_dir_entry = ttk.Entry(inner, textvariable=app.work_dir_var, font=ENTRY, width=38)
    app.work_dir_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=6)

    def _row0_btn(text, cmd, bg=None, fg=None, tip=""):
        style_kw = {}
        if bg:
            style_kw.update(
                bg=bg, fg=fg or COLORS["btn_text"], activebackground=bg, activeforeground=fg or COLORS["btn_text"]
            )
        b = tk.Button(
            inner,
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
    tk.Frame(inner, bg=COLORS["card_border"], width=2).grid(row=0, column=4, sticky="ns", padx=8, pady=4)

    # —— 列：扫描 / 刷新 ——
    _row0_btn(
        "🔍 扫描文件", app.controller.scan_files, bg=COLORS["btn_info_bg"], tip="重新扫描工作目录下的所有计算文件"
    ).grid(row=0, column=5, sticky="w", padx=2, pady=6)

    tk.Frame(inner, bg=COLORS["card_border"], width=2).grid(row=0, column=7, sticky="ns", padx=8, pady=4)

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
    tk.Frame(inner, bg=COLORS["card_border"], width=2).grid(row=0, column=10, sticky="ns", padx=8, pady=4)
    tk.Label(
        inner,
        text="文件类型:",
        bg=COLORS["card_bg"],
        fg=COLORS["text_light"],
        font=getattr(app, "_fonts", {}).get("SMALL", ("Microsoft YaHei", 11)),
    ).grid(row=0, column=11, sticky="w", padx=(0, 4), pady=6)
    app.ext_display_var = tk.StringVar()
    app.helpers.update_ext_display()
    tk.Label(
        inner,
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
