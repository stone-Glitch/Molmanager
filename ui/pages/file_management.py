"""📁 文件管理页（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import tkinter as tk
from tkinter import ttk

from ui.pages.paned_file_log import _build_paned_file_and_log
from ui.theme_tokens import SPACING
from ui.ui_builder._theme import add_tooltip
from ui.ui_theme import COLORS, dark_card, primary_button, section_title, themed_button


def build_tab_file_management(app, parent):
    """
    文件管理页：
      - 上：映射文件管理行
      - 中：两行主操作按钮（一键修复 / 整理 / 映射 高确定性操作）
      - 下：文件列表（Treeview + 过滤） +  右侧 日志（垂直 PanedWindow 保留）
    """
    parent.grid_rowconfigure(2, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    F = getattr(app, "_fonts", {}) or {}

    # 映射加载/编辑功能统一收敛到「分子映射」页（build_tab_molecular_mapping），
    # 此页不再放置重复的加载控件，避免双入口状态不一致。

    # —— 卡片 2：常用文件操作（深色卡片化） ——
    ops_card = dark_card(parent)
    ops_card.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
    ops_card.grid_columnconfigure(0, weight=1)
    section_title(ops_card, "⚡  常用文件操作（推荐：先按顺序点前 3 个）").grid(
        row=0, column=0, sticky="w", padx=12, pady=(10, 4)
    )

    grid = tk.Frame(ops_card, bg=COLORS["surface"])
    grid.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
    for c in range(4):
        grid.grid_columnconfigure(c, weight=1)

    def _ab(text, cmd, row, col, kind="secondary", tip="", width=18):
        b = themed_button(grid, text, cmd, kind)
        if width:
            b.config(width=width)
        b.grid(row=row, column=col, padx=5, pady=6, sticky="ew")
        if tip:
            add_tooltip(b, tip)
        return b

    # 行 1：高确定性一键式操作
    _ab(
        "🔧 一键修复全部",
        app.controller.run_fix_by_mode,
        0,
        0,
        "success",
        tip="依次执行：映射重命名→修复中文名→修复命名错误→修正中文内容（每项可预览取消）",
        width=18,
    )
    _ab(
        "📂 按类型整理",
        app.controller.organize_by_type,
        0,
        1,
        "primary",
        tip="按扩展名把文件移动到 mol_files/xyz_files/fchk_files 等子目录",
    )
    _ab(
        "🧹 删除重复文件",
        app.controller.remove_duplicate_files,
        0,
        2,
        "warning",
        tip="扫描内容完全相同的重复文件并删除（会先弹确认）",
    )
    try:
        _ab(
            "📋 生成缺失映射表",
            app.controller.generate_missing,
            0,
            3,
            "secondary",
            tip="把没有中文名的文件列表导出为 CSV 模板，方便批量填入后导入",
        )
    except Exception:
        pass

    # 行 2：仍常用但更具体的操作
    _ab(
        "🧪 补全 .mol 文件",
        app.controller.supplement_mol,
        1,
        0,
        "secondary",
        tip="对有 .xyz 但缺 .mol 的文件，用 OpenBabel 自动生成 mol",
    )
    _ab(
        "📁 按文件名分组",
        app.controller.organize_by_basename,
        1,
        1,
        "secondary",
        tip="按基本名（无扩展名）相同，把 .mol/.xyz/.fchk/.out 等放入同名文件夹",
    )
    _ab(
        "🏷️ 前缀重命名",
        app.controller.prefix_rename_dialog,
        1,
        2,
        "secondary",
        tip="为选中的文件批量加前缀、改后缀（弹对话框配置）",
    )
    _ab(
        "🗑️ 删除选中文件",
        app.controller.delete_selected,
        1,
        3,
        "danger",
        tip="删除列表中当前勾选的文件（建议先预览选中项）",
    )

    # 行 3：修复模式选择（高级）
    mode_row = tk.Frame(ops_card, bg=COLORS["surface"])
    mode_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
    tk.Label(
        mode_row,
        text="💡 修复模式（高级）：",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=F.get("BTN", ("Microsoft YaHei", 12, "bold")),
    ).pack(side=tk.LEFT, padx=(0, 8))
    app.fix_mode_var = tk.StringVar(value="一键修复（推荐）")
    fix_menu = ttk.Combobox(
        mode_row,
        textvariable=app.fix_mode_var,
        values=["一键修复（推荐）", "映射重命名", "修复中文名", "修复命名错误", "修正中文内容"],
        width=24,
        state="readonly",
        font=F.get("BASE", ("Microsoft YaHei", 12)),
    )
    fix_menu.pack(side=tk.LEFT, padx=3)
    add_tooltip(fix_menu, "如果你只需要单独执行某一步修复，可在此切换；否则推荐保持「一键修复」")
    themed_button(mode_row, "▶ 执行", app.controller.run_fix_by_mode, "success").pack(side=tk.LEFT, padx=6)

    # —— R2：文件列表 + 日志（垂直分割） ——
    _build_paned_file_and_log(app, parent, row=2, column=0)

    # —— 空状态引导卡（设计落地 Phase 4）：工作目录无文件时显示，有文件时隐藏 ——
    es = dark_card(parent)
    es.grid(row=2, column=0, sticky="nsew", padx=8, pady=(8, 4))
    es.grid_remove()  # 默认隐藏，交由 refresh_empty_state 控制
    es.grid_rowconfigure(0, weight=1)
    es.grid_columnconfigure(0, weight=1)
    app._empty_state = es

    _es_inner = tk.Frame(es, bg=COLORS["surface"])
    _es_inner.grid(row=0, column=0, sticky="nsew", padx=24, pady=24)
    _es_inner.grid_columnconfigure(0, weight=1)

    tk.Label(
        _es_inner,
        text="📭  工作目录还没有文件",
        bg=COLORS["surface"],
        fg=COLORS["text"],
        font=("Microsoft YaHei", 16, "bold"),
        anchor="w",
    ).grid(row=0, column=0, sticky="w", pady=(0, 4))
    tk.Label(
        _es_inner,
        text="把分子 / 计算结果文件放进工作目录，或直接选择目录开始。三步即可上手：",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=("Microsoft YaHei", 12),
        anchor="w",
        wraplength=560,
        justify="left",
    ).grid(row=1, column=0, sticky="w", pady=(0, SPACING["md"]))

    steps = [
        ("①", "选择工作目录", "点右上「📂 浏览…」或下方按钮，指定存放分子文件的文件夹"),
        ("②", "一键修复全部", "自动补全中文名、修正命名错误、整理内容（可逐项预览）"),
        ("③", "按类型整理", "按扩展名归档到 mol_files / xyz_files / fchk_files 等子目录"),
    ]
    for i, (num, title, desc) in enumerate(steps):
        _sc = tk.Frame(
            _es_inner, bg=COLORS["elevated"], bd=0, highlightbackground=COLORS["card_border"], highlightthickness=1
        )
        _sc.grid(row=2 + i, column=0, sticky="ew", pady=5)
        tk.Label(
            _sc,
            text=num,
            bg=COLORS["elevated"],
            fg=COLORS["accent"],
            font=("Microsoft YaHei", 18, "bold"),
            width=2,
            anchor="center",
        ).pack(side=tk.LEFT, padx=12, pady=10)
        _txt = tk.Frame(_sc, bg=COLORS["elevated"])
        _txt.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12), pady=10)
        tk.Label(
            _txt, text=title, bg=COLORS["elevated"], fg=COLORS["text"], font=("Microsoft YaHei", 13, "bold"), anchor="w"
        ).pack(anchor="w")
        tk.Label(
            _txt,
            text=desc,
            bg=COLORS["elevated"],
            fg=COLORS["text_secondary"],
            font=("Microsoft YaHei", 11),
            anchor="w",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

    _es_btn = primary_button(
        _es_inner, "📂  选择工作目录", app.controller.browse_work_dir, tip="选择存放分子文件的文件夹"
    )
    _es_btn.grid(row=2 + len(steps), column=0, sticky="w", pady=(14, 0))

    def refresh_empty_state():
        """根据 tree 是否空，切换「空状态引导卡」与「文件列表 Paned」的显示。"""
        try:
            tree = getattr(app, "tree", None)
            n = len(tree.get_children()) if tree is not None else 0
            paned = getattr(app, "_file_list_paned", None)
            _es = getattr(app, "_empty_state", None)
            if n == 0:
                if _es is not None:
                    _es.grid(row=2, column=0, sticky="nsew", padx=8, pady=(8, 4))
                if paned is not None:
                    paned.grid_remove()
            else:
                if _es is not None:
                    _es.grid_remove()
                if paned is not None:
                    paned.grid(row=2, column=0, sticky="nsew", pady=(0, 4))
        except Exception:
            pass

    app.refresh_empty_state = refresh_empty_state
    refresh_empty_state()  # 初始判定（此时 tree 多半为空）

    # 包裹 apply_filter：每次填充 tree 后刷新空状态（扫描/筛选均在主线程完成）
    try:
        _orig_apply = app.helpers.apply_filter

        def _wrapped_apply():
            _orig_apply()
            refresh_empty_state()

        app.helpers.apply_filter = _wrapped_apply
    except Exception:
        pass
