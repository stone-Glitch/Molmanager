import time
import tkinter as tk
from tkinter import scrolledtext, ttk

import ui.ui_theme as ui_theme
from ui.ui_theme import (
    CHECK_GLYPH,
    COLORS,
    dark_card,
    primary_button,
    section_title,
    themed_button,
)

# ------------------------- 🎨 主题颜色常量 -------------------------
from ._theme import CollapsibleFrame, add_tooltip


def build_tab_dashboard(app, parent):
    """🏠 工作台：概览统计（4 卡）+ 快捷操作（界面方案新增落地页）。"""
    F = getattr(app, "_fonts", {})
    f_h1 = F.get("H1", ("Microsoft YaHei", 20, "bold"))
    f_bold = F.get("BOLD", ("Microsoft YaHei", 14, "bold"))
    f_base = F.get("BASE", ("Microsoft YaHei", 13))
    f_small = F.get("SMALL", ("Microsoft YaHei", 12))
    f_num = ("Microsoft YaHei", 24, "bold")

    tk.Label(parent, text="工作台", bg=COLORS["bg"], fg=COLORS["text"], font=f_h1, anchor="w").pack(
        anchor="w", padx=20, pady=(18, 2)
    )
    tk.Label(
        parent,
        text="这里是你所有分子与计算任务的入口，一键直达高频操作。",
        bg=COLORS["bg"],
        fg=COLORS["text_secondary"],
        font=f_base,
        anchor="w",
    ).pack(anchor="w", padx=20, pady=(0, 14))

    # —— 统计卡（4 张，读 last_scan_result）——
    stats = tk.Frame(parent, bg=COLORS["bg"])
    stats.pack(fill="x", padx=20, pady=4)
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
            anchor="w", padx=16, pady=(0, 14)
        )
        app._dash_vars[key] = var

    # —— 快捷操作 ——
    tk.Label(parent, text="快捷操作", bg=COLORS["bg"], fg=COLORS["text"], font=f_bold, anchor="w").pack(
        anchor="w", padx=20, pady=(20, 8)
    )
    quick = tk.Frame(parent, bg=COLORS["bg"])
    quick.pack(fill="x", padx=20, pady=4)

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
    ).grid(row=1, column=0, sticky="w", pady=(0, 14))

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


# ===========================================================
# 🧬 Tab：分子映射（设计落地：独立一级导航页）
# ===========================================================


def build_tab_mapping(app, parent):
    """🧬 分子映射页：映射文件加载 + 管理操作 + 映射条目列表（eng→chn 预览）。"""
    parent.grid_columnconfigure(0, weight=1)
    F = getattr(app, "_fonts", {}) or {}

    tk.Label(
        parent,
        text="分子映射",
        bg=COLORS["bg"],
        fg=COLORS["text"],
        font=F.get("H1", ("Microsoft YaHei", 20, "bold")),
        anchor="w",
    ).pack(anchor="w", padx=20, pady=(18, 2))
    tk.Label(
        parent,
        text="管理「英文名 / 编号 → 中文名」的映射关系，让文件列表自动显示中文名。",
        bg=COLORS["bg"],
        fg=COLORS["text_secondary"],
        font=F.get("BASE", ("Microsoft YaHei", 13)),
        anchor="w",
    ).pack(anchor="w", padx=20, pady=(0, 14))

    # —— 卡片 1：映射文件加载 ——
    load_card = dark_card(parent)
    load_card.pack(fill="x", padx=12, pady=(4, 6))
    section_title(load_card, "📥  映射文件加载").pack(anchor="w", padx=12, pady=(10, 4))

    path_row = tk.Frame(load_card, bg=COLORS["surface"])
    path_row.pack(fill="x", padx=12, pady=(0, 8))
    tk.Label(
        path_row,
        text="映射文件路径:",
        bg=COLORS["surface"],
        fg=COLORS["text"],
        font=F.get("BASE", ("Microsoft YaHei", 12)),
    ).pack(side=tk.LEFT, padx=(0, 6))
    app.mapping_entry = ttk.Entry(
        path_row, textvariable=app.mapping_file_var, font=F.get("BASE", ("Microsoft YaHei", 12))
    )
    app.mapping_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

    btn_row = tk.Frame(load_card, bg=COLORS["surface"])
    btn_row.pack(fill="x", padx=12, pady=(0, 10))

    def _btn(text, cmd, kind="secondary", tip=""):
        b = themed_button(btn_row, text, cmd, kind)
        b.pack(side=tk.LEFT, padx=4, pady=2)
        if tip:
            add_tooltip(b, tip)
        return b

    _btn("📂 浏览", app.controller.browse_mapping, "secondary", tip="选择要加载的映射文件(.txt/.csv)")
    _btn("📥 加载", app.controller.load_mapping_file, "success", tip="读取映射文件，立刻生效到列表")
    try:
        _btn(
            "✏️ 编辑映射", app.controller.show_mapping_editor_dialog, "secondary", tip="打开映射编辑器：增删改中英文条目"
        )
        _btn("📊 映射管理器", app.controller.show_mapping_manager_dialog, "secondary", tip="映射批量导入/导出/补全工具")
    except Exception:
        pass
    try:
        _btn(
            "📋 生成缺失CSV",
            app.controller.generate_missing,
            "secondary",
            tip="扫描工作目录，把找不到中文名的文件名导出为 CSV 模板",
        )
        _btn("⬇ 导入CSV", app.controller.show_mapping_manager_dialog, "secondary", tip="从 CSV 导入中英文映射")
    except Exception:
        pass
    tk.Label(
        btn_row,
        text="  已加载:",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=F.get("BASE", ("Microsoft YaHei", 12)),
    ).pack(side=tk.RIGHT, padx=(10, 2))
    tk.Label(
        btn_row,
        textvariable=app.mapping_count,
        bg=COLORS["surface"],
        fg=COLORS["accent"],
        font=F.get("BOLD", ("Microsoft YaHei", 14, "bold")),
    ).pack(side=tk.RIGHT, padx=(0, 6))

    # —— 卡片 2：映射条目列表（eng → chn 预览）——
    list_card = dark_card(parent)
    list_card.pack(fill="both", expand=True, padx=12, pady=(0, 10))
    list_card.grid_columnconfigure(0, weight=1)
    list_card.grid_rowconfigure(1, weight=1)
    section_title(list_card, "📋  已加载映射条目（双击打开编辑器）").grid(
        row=0, column=0, sticky="w", padx=12, pady=(10, 4)
    )

    app.mapping_list_count = tk.StringVar(value="共 0 条")
    tk.Label(
        list_card,
        textvariable=app.mapping_list_count,
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=F.get("SMALL", ("Microsoft YaHei", 12)),
    ).grid(row=0, column=0, sticky="e", padx=12, pady=(10, 4))

    tree_frame = tk.Frame(list_card, bg=COLORS["surface"])
    tree_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
    tree_frame.grid_columnconfigure(0, weight=1)
    tree_frame.grid_rowconfigure(0, weight=1)

    app.mapping_tree = ttk.Treeview(tree_frame, columns=("英文名", "中文名"), show="headings", height=16)
    app.mapping_tree.heading("英文名", text="英文名 / 编号")
    app.mapping_tree.heading("中文名", text="中文名")
    app.mapping_tree.column("英文名", width=320, anchor=tk.W)
    app.mapping_tree.column("中文名", width=320, anchor=tk.W)
    mvsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=app.mapping_tree.yview)
    app.mapping_tree.configure(yscrollcommand=mvsb.set)
    app.mapping_tree.grid(row=0, column=0, sticky="nsew")
    mvsb.grid(row=0, column=1, sticky="ns")

    style = ttk.Style()
    style.configure("Treeview", font=F.get("BASE", ("Microsoft YaHei", 12)), rowheight=28)

    def _on_map_dbl(_event):
        try:
            app.controller.show_mapping_editor_dialog()
        except Exception:
            pass

    app.mapping_tree.bind("<Double-1>", _on_map_dbl)

    def refresh_mapping():
        try:
            mp = getattr(app.controller.model, "mapping", None) or {}
            tree = getattr(app, "mapping_tree", None)
            if tree is None:
                return
            for iid in tree.get_children():
                tree.delete(iid)
            for eng in sorted(mp, key=lambda k: str(k).lower()):
                tree.insert("", tk.END, values=(eng, mp[eng]))
            try:
                app.mapping_list_count.set(f"共 {len(mp)} 条")
            except Exception:
                pass
            try:
                app.mapping_count.set(str(len(mp)) if mp else "未加载")
            except Exception:
                pass
        except Exception:
            pass

    app.refresh_mapping = refresh_mapping
    refresh_mapping()


# ===========================================================
# 🔬 Tab2：计算与动画
# ===========================================================


def build_tab_compute_and_animation(app, parent):
    """
    计算与动画页（深色卡片化）：
      - 快速计算预设卡片
      - 一键直达卡片（反应动画 / PSI4 面板 / 能垒图 / 构象搜索）
      - 高级计算参数（可折叠）
      - 扫描参数（可折叠）
      - 文件列表 + 日志（tab2 占位）
    """
    parent.grid_rowconfigure(4, weight=1)
    parent.grid_columnconfigure(0, weight=1)
    F = getattr(app, "_fonts", {}) or {}

    # —— 卡片 1：快速计算预设 ——
    preset_card = dark_card(parent)
    preset_card.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 6))
    preset_card.grid_columnconfigure(2, weight=1)
    section_title(preset_card, "⚡  快速计算预设（选一个直接运行，无需了解方法/基组细节）").grid(
        row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4)
    )

    try:
        from utils.constants import RUN_PRESETS

        preset_names = list(RUN_PRESETS.keys())
    except Exception:
        RUN_PRESETS = {}
        preset_names = []

    row1 = tk.Frame(preset_card, bg=COLORS["surface"])
    row1.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 10))
    row1.grid_columnconfigure(2, weight=1)

    tk.Label(
        row1,
        text="🎯 选择预设:",
        bg=COLORS["surface"],
        fg=COLORS["text"],
        font=F.get("BOLD", ("Microsoft YaHei", 13, "bold")),
    ).grid(row=0, column=0, padx=(0, 8), pady=8, sticky="w")

    app.quick_preset_var = tk.StringVar(value=(preset_names[0] if preset_names else "请先定义 RUN_PRESETS"))
    preset_cb = ttk.Combobox(
        row1,
        textvariable=app.quick_preset_var,
        values=preset_names,
        state="readonly",
        width=40,
        font=F.get("BASE", ("Microsoft YaHei", 12)),
    )
    preset_cb.grid(row=0, column=1, padx=4, pady=8, sticky="w")

    def _on_preset_change(_e=None):
        try:
            name = app.quick_preset_var.get()
            info = RUN_PRESETS.get(name, {})
            parts = []
            for k in ("task_type", "method", "basis", "solvent", "preset_name"):
                if k in info and info[k]:
                    parts.append(f"{k}={info[k]}")
            add_tooltip(preset_cb, "当前预设参数：\n" + "\n".join(parts) if parts else "无")
        except Exception:
            pass

    preset_cb.bind("<<ComboboxSelected>>", _on_preset_change)
    _on_preset_change()

    def _run_quick_preset():
        """把 RUN_PRESETS[name] 对应参数填到 PSI4 对话框，并打开（所有任务仍复用 PSI4 对话框）。"""
        try:
            name = app.quick_preset_var.get()
            info = RUN_PRESETS.get(name, {})
        except Exception:
            info = {}
        app._last_run_preset_name = info.get("preset_name", info.get("name", name))
        app.controller.show_psi4_dialog()

    run_btn = themed_button(row1, "▶  运行所选文件", _run_quick_preset, "success")
    app.run_selected_btn = run_btn
    run_btn.grid(row=0, column=3, padx=10, pady=8, sticky="e")
    add_tooltip(run_btn, "会自动打开 PSI4 完整对话框（专家参数可按需修改），默认使用预设里的方法/基组/溶剂")

    # —— 卡片 2：一键直达 ——
    quick_card = dark_card(parent)
    quick_card.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
    quick_card.grid_columnconfigure(0, weight=1)
    section_title(quick_card, "🚀  一键直达").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
    qa = tk.Frame(quick_card, bg=COLORS["surface"])
    qa.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

    def _qa(text, cmd, kind="secondary", tip=""):
        b = themed_button(qa, text, cmd, kind)
        b.pack(side=tk.LEFT, padx=4, pady=2)
        if tip:
            add_tooltip(b, tip)
        return b

    _qa(
        "🎬 制作反应动画",
        (
            lambda: (
                (
                    hasattr(app.controller, "show_reaction_animation_dialog")
                    and app.controller.show_reaction_animation_dialog()
                )
                or app.controller.show_advanced_tools_dialog()
            )
        ),
        "primary",
        tip="多反应物+多产物 → 插值生成反应轨迹/能量图/动画 GIF",
    )
    _qa(
        "⚡ 打开完整 PSI4 面板",
        app.controller.show_psi4_dialog,
        "secondary",
        tip="完整 PSI4 设置：任务/方法/基组/溶剂/D3/电荷/内存/扫描 等全部可调",
    )
    _qa(
        "📊 反应能垒/能垒图",
        (lambda: hasattr(app.controller, "show_advanced_tools_dialog") and app.controller.show_advanced_tools_dialog()),
        "secondary",
        tip="打开高级工具 → 反应能垒图 / pKa / NMR 等",
    )
    _qa(
        "📈 构象搜索 / NMR / pKa / IRC",
        (lambda: hasattr(app.controller, "show_advanced_tools_dialog") and app.controller.show_advanced_tools_dialog()),
        "secondary",
        tip="构象搜索、过渡态 IRC、pKa 预测、Boltzmann 加权 NMR",
    )

    # —— 卡片 3：高级计算参数（可折叠，默认收起）——
    adv = CollapsibleFrame(
        parent, title="⚙️ 高级计算参数（专家使用，包含所有任务类型/扫描/方法/基组/溶剂/电荷/内存）", collapsed=True
    )
    adv.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))

    tk.Label(
        adv.body,
        text="  完整 PSI4 对话框包含：任务类型下拉 (单点/优化/频率/扫描/过渡态/激发态/SAPT/热化学)、方法/基组、\n"
        "  溶剂(PCM/SMD)、D3 色散、电荷/多重度、内存(GB)、步数/收敛限、线性/刚性扫描参数 等 —— 所有原功能全部可用。",
        wraplength=900,
        justify="left",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=F.get("SMALL", ("Microsoft YaHei", 11)),
    ).pack(anchor="w", padx=8, pady=6)
    row_b = tk.Frame(adv.body, bg=COLORS["surface"])
    row_b.pack(fill="x", padx=8, pady=(0, 8))
    themed_button(row_b, "⚡ 打开 PSI4 完整设置对话框", app.controller.show_psi4_dialog, "primary").pack(
        side=tk.LEFT, padx=4
    )
    try:
        themed_button(row_b, "🛠 高级扫描（线性/刚性）", app.controller.show_advanced_tools_dialog, "warning").pack(
            side=tk.LEFT, padx=4
        )
    except Exception:
        pass

    # —— 卡片 4：扫描参数（可折叠）+ 说明 ——
    scan_adv = CollapsibleFrame(parent, title="📈 线性/刚性扫描参数（用于势能面 PES 扫描）", collapsed=True)
    scan_adv.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 6))
    tk.Label(
        scan_adv.body,
        text="  线性扫描：两个端点结构 → 线性插值 N 帧 → 每帧跑单点能 → 能垒 CSV/图；\n"
        "  刚性扫描：固定某个二面角/键长/键角步进，其他自由优化（完整 PSI4 对话框里可配置）。",
        wraplength=900,
        justify="left",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=F.get("SMALL", ("Microsoft YaHei", 11)),
    ).pack(anchor="w", padx=8, pady=6)
    themed_button(
        scan_adv.body, "📊 打开高级扫描/能垒图工具", app.controller.show_advanced_tools_dialog, "primary"
    ).pack(anchor="w", padx=8, pady=(0, 8))

    # —— 文件列表 + 日志（tab2 占位）——
    _build_paned_file_and_log(app, parent, row=4, column=0, show_in_tab2=True)


# ===========================================================
# ⚙️ Tab3：高级工具（子 Notebook 4 页）
# ===========================================================


def build_tab_advanced_tools(app, parent):
    """
    高级工具页：子 Notebook 4 页（分子工具 / 波函数 / 动力学 / 数据管理），
    所有原 OpenBabel + PSI4 高级对话框 + 历史/结果浏览/目录同步 入口全部收纳。
    功能零损失。
    """
    parent.grid_rowconfigure(0, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    nb = ttk.Notebook(parent)
    nb.grid(row=0, column=0, sticky="nsew", padx=2, pady=(6, 4))
    app.advanced_notebook = nb

    # —— 子页 1：分子工具（OB 全家桶 + 分子式） ——
    t1 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t1, text="  🧪  分子工具 (OB)  ")
    _adv_grid_of_buttons(
        t1,
        [
            (
                "🔬 OpenBabel 工具（全功能）",
                app.controller.show_openbabel_dialog,
                True,
                "格式转换/SMILES生成/描述符/叠加/2D预览/手性/pH加氢/SDF拆分/InChIKey",
            ),
            (
                "🧮 分子式/分子量/元素分析",
                lambda: (
                    app.dialogs.show_formula_dialog()
                    if hasattr(app, "dialogs") and hasattr(app.dialogs, "show_formula_dialog")
                    else None
                ),
                False,
                "从 XYZ/MOL/INP 等解析分子式、精确质量、元素百分比",
            ),
            ("🔎 最近工作目录", app.controller.show_recent_dirs_dialog, False, "快速切换到之前打开过的工作目录"),
            (
                "📐 导出几何参数 CSV",
                lambda: (
                    app.controller.export_geometry_csv() if hasattr(app.controller, "export_geometry_csv") else None
                ),
                False,
                "把文件列表里分子的键长/键角/二面角批量导出 CSV",
            ),
        ],
    )

    # —— 子页 2：波函数与分析（PSI4 所有高级 + NMR/pKa/IRC） ——
    t2 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t2, text="  🧠  波函数 / NMR / pKa  ")
    _adv_grid_of_buttons(
        t2,
        [
            (
                "⚡ PSI4 完整计算（所有任务类型）",
                app.controller.show_psi4_dialog,
                True,
                "单点/优化/频率/过渡态/激发态/SAPT/热化学 + 溶剂/D3/内存/电荷",
            ),
            (
                "📊 高级扫描（线性/刚性/能垒图）",
                app.controller.show_advanced_tools_dialog,
                True,
                "势能面 PES 线性扫描、刚性扫描、能垒曲线",
            ),
            (
                "🎞️ IRC + 反应路径动画",
                app.controller.show_advanced_tools_dialog,
                False,
                "从 TS 结构跑 IRC 前向/反向，导出动画帧",
            ),
            (
                "🧪 Boltzmann 加权 ¹H NMR 模拟",
                app.controller.show_advanced_tools_dialog,
                False,
                "OB 构象搜索 + PSI4 CPHF NMR σ + TMS 参考 → δ + Lorentz 展宽 PNG",
            ),
            (
                "⚗️ pKa 热力学循环预测",
                app.controller.show_advanced_tools_dialog,
                False,
                "SMD/water 水相单点 + H+(aq) 经验值 → pKa 估算 ±2",
            ),
            (
                "🧩 构象搜索（OB MMFF + PSI4 高精度）",
                app.controller.show_advanced_tools_dialog,
                False,
                "多构象搜索 + Boltzmann 权重",
            ),
            (
                "🧬 反应路径能垒图",
                app.controller.show_advanced_tools_dialog,
                False,
                "多步反应路径 Ea/ΔG 能垒图 + CSV 导出",
            ),
        ],
    )

    # —— 子页 3：动画与分子可视化 ——
    t3 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t3, text="  🎬  动画 / 反应路径  ")
    _adv_grid_of_buttons(
        t3,
        [
            (
                "🎬 反应动画生成器",
                (
                    lambda: (
                        hasattr(app.controller, "show_reaction_animation_dialog")
                        and app.controller.show_reaction_animation_dialog()
                    )
                ),
                True,
                "多反应物+多产物 → 自动对齐原子 → 插值 N 帧轨迹 → 能量 CSV + SDF/XYZ",
            ),
            (
                "🛠 高级工具箱（反应动画/NMR/pKa/IRC 综合入口）",
                app.controller.show_advanced_tools_dialog,
                False,
                "综合高级功能单页入口",
            ),
            (
                "🎞 结果浏览器 / 轨迹播放",
                (
                    lambda: (
                        hasattr(app.controller, "show_results_browser_dialog")
                        and app.controller.show_results_browser_dialog()
                    )
                ),
                False,
                "浏览 PSI4 .out/.fchk、动画轨迹、NMR PNG/CSV 等产物",
            ),
        ],
    )

    # —— 子页 4：数据管理（历史/结果/目录同步/映射编辑器） ——
    t4 = tk.Frame(nb, bg=COLORS["bg"])
    nb.add(t4, text="  🗂️  数据管理 / 历史  ")
    _adv_grid_of_buttons(
        t4,
        [
            (
                "📜 操作历史（撤销/重做列表）",
                (lambda: hasattr(app.controller, "show_history_dialog") and app.controller.show_history_dialog()),
                False,
                "查看所有已执行文件操作，支持逐条撤销/重做",
            ),
            (
                "🔍 结果浏览器（PSI4 输出/谱图）",
                (
                    lambda: (
                        hasattr(app.controller, "show_results_browser_dialog")
                        and app.controller.show_results_browser_dialog()
                    )
                ),
                False,
                "按工作目录浏览计算输出 .out/.fchk/.log、NMR 图、反应 CSV",
            ),
            (
                "🔄 目录同步 / 差异比对",
                (lambda: hasattr(app.controller, "show_diff_sync_dialog") and app.controller.show_diff_sync_dialog()),
                False,
                "两个目录间双向 diff：缺失项、同名不同内容，选择同步方向",
            ),
            (
                "✏️ 映射编辑器",
                (
                    lambda: (
                        hasattr(app.controller, "show_mapping_editor_dialog")
                        and app.controller.show_mapping_editor_dialog()
                    )
                ),
                False,
                "逐条增删改中英文映射条目（即时生效）",
            ),
            (
                "📊 映射管理器（导入/导出/补全）",
                (
                    lambda: (
                        hasattr(app.controller, "show_mapping_manager_dialog")
                        and app.controller.show_mapping_manager_dialog()
                    )
                ),
                False,
                "批量导入 CSV / 导出模板 / 从现有文件补全",
            ),
        ],
    )


def _adv_grid_of_buttons(parent, buttons_spec):
    """
    以 2 列网格形式放置「高级工具卡片」，每个卡片：
    (文字, 回调, 是否高亮主色, tooltip文字)
    卡片下方自动有小字说明，新手友好。语义色：高亮→主青绿，否则次按钮。
    """
    # 本函数没有 app 形参（是通用布局辅助），通过 winfo_toplevel() 取主窗口的字体基线；
    # 取不到就退回默认字体，绝不能让取字体失败把整个「高级工具」页构建搞崩。
    try:
        _F = getattr(parent.winfo_toplevel(), "_fonts", {}) or {}
    except Exception:
        _F = {}
    _SMALL_FONT = _F.get("SMALL", ("Microsoft YaHei", 11))

    container = tk.Frame(parent, bg=COLORS["bg"])
    container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    for i in range(2):
        container.grid_columnconfigure(i, weight=1)

    for idx, spec in enumerate(buttons_spec):
        text, cmd, highlight, tip = (spec + (None,))[:4] if len(spec) < 4 else spec
        r, c = divmod(idx, 2)
        card = dark_card(container)
        card.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        btn = themed_button(card, text, cmd, "primary" if highlight else "secondary")
        btn.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 4))
        if tip:
            add_tooltip(btn, tip)
            # tooltip 文字也同时显示在卡片下方（避免用户不知道要悬停）
            tk.Label(
                card,
                text="💡 " + (tip if len(tip) <= 96 else tip[:94] + "…"),
                wraplength=360,
                justify="left",
                bg=COLORS["surface"],
                fg=COLORS["text_secondary"],
                font=_SMALL_FONT,
            ).grid(row=1, column=0, sticky="nw", padx=10, pady=(0, 10))


# ===========================================================
# 📊 公共：文件列表 + 日志（垂直分割）
# ===========================================================


def _build_paned_file_and_log(app, parent, row, column, show_in_tab2: bool = False):
    """
    文件列表 + 日志 垂直 PanedWindow。
    注意：**app.tree / app.log_text / app.context_menu / app.filter_keyword_entry / filter_count_var 只创建一次**，
    第二次调用（tab2 复用）时，就不创建 Treeview/Log 控件，而是放一个占位提示：
    「切回「📁 文件管理」页查看文件列表与日志」，避免多份 UI 导致 controller 引用错漏。
    这保证 controller.py/dialogs.py 里所有对 app.tree / app.log_text 的引用仍然唯一、功能零损失。
    """
    if hasattr(app, "_file_log_paned_built") and app._file_log_paned_built:
        # Tab2 版本：显示一个友好的占位卡片，提示当前文件列表在 Tab1；右侧放常用按钮直通 Tab1
        placeholder = tk.Frame(parent, bg=COLORS["bg"])
        placeholder.grid(row=row, column=column, sticky="nsew", pady=(0, 4))
        card = tk.Frame(
            placeholder,
            bg=COLORS["card_bg"],
            bd=1,
            relief=tk.SOLID,
            highlightbackground=COLORS["card_border"],
            highlightthickness=1,
        )
        card.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        tk.Label(
            card,
            text="\n   💡 提示：当前选中的文件列表、日志输出请在左侧「📁 文件管理」标签页查看。\n"
            "   在这里选择预设并点「运行」后，会自动打开 PSI4 对话框。\n",
            bg=COLORS["card_bg"],
            fg=COLORS["text_light"],
            font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 12)),
            justify="left",
        ).pack(padx=16, pady=20, anchor="w")

        def _jump_tab1():
            try:
                app.main_notebook.select(1)  # 文件管理（工作台已是第 0 页）
            except Exception:
                pass

        row_b = tk.Frame(card, bg=COLORS["card_bg"])
        row_b.pack(anchor="w", padx=16, pady=(0, 20))
        tk.Button(
            row_b,
            text="跳转到 📁 文件管理页",
            command=_jump_tab1,
            font=getattr(app, "_fonts", {}).get("BTN", ("Microsoft YaHei", 12, "bold")),
            relief=tk.RAISED,
            bd=1,
            padx=12,
            pady=5,
            cursor="hand2",
            bg=COLORS["btn_info_bg"],
            fg=COLORS["btn_text"],
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            row_b,
            text="🔍 立刻扫描文件列表",
            command=app.controller.scan_files,
            font=getattr(app, "_fonts", {}).get("BTN", ("Microsoft YaHei", 12, "bold")),
            relief=tk.RAISED,
            bd=1,
            padx=12,
            pady=5,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=4)
        return

    paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
    paned.grid(row=row, column=column, sticky="nsew", pady=(0, 4))
    app._file_list_paned = paned  # 暴露引用，供空状态引导卡切换显示/隐藏
    parent.grid_rowconfigure(row, weight=1)
    app._file_log_paned_built = True

    # ---------- 文件列表 ----------
    list_frame = tk.LabelFrame(
        paned,
        text="📄 文件列表（勾选多选 · 双击编辑中文名 · 右键删除勾选）",
        bg=COLORS["card_bg"],
        font=getattr(app, "_fonts", {}).get("H1", ("Microsoft YaHei", 14, "bold")),
        relief=tk.GROOVE,
        bd=2,
    )
    paned.add(list_frame, weight=2)

    # 🔎 关键词过滤条（输入即搜）
    filter_row = tk.Frame(list_frame, bg=COLORS["card_bg"])
    filter_row.pack(fill=tk.X, padx=8, pady=6)
    tk.Label(
        filter_row,
        text="🔎 关键词:",
        bg=COLORS["card_bg"],
        fg=COLORS["text"],
        font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 13)),
    ).pack(side=tk.LEFT, padx=(0, 6))
    app.filter_keyword_var = getattr(app, "filter_keyword_var", None) or tk.StringVar()
    app.filter_keyword_entry = ttk.Entry(
        filter_row,
        textvariable=app.filter_keyword_var,
        width=30,
        font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 13)),
    )
    app.filter_keyword_entry.pack(side=tk.LEFT, padx=(0, 8))
    app.filter_keyword_entry.bind("<KeyRelease>", lambda e: app.helpers.apply_filter())
    ttk.Button(
        filter_row, text="清除", command=lambda: (app.filter_keyword_var.set(""), app.helpers.apply_filter()), width=8
    ).pack(side=tk.LEFT)

    def _toggle_bar():
        # 重新展开底部浮动批量条（标签随选中数变化）
        app.batch_bar_open = True
        _tree_update_check_state()

    app.batch_toggle_btn = ttk.Button(filter_row, text="批量操作 ▾", command=_toggle_bar, width=12)
    app.batch_toggle_btn.pack(side=tk.LEFT, padx=(10, 0))
    if not getattr(app, "filter_count_var", None):
        app.filter_count_var = tk.StringVar(value="共 0 / 0 个")
    tk.Label(
        filter_row,
        textvariable=app.filter_count_var,
        bg=COLORS["card_bg"],
        fg=COLORS["primary"],
        font=getattr(app, "_fonts", {}).get("BOLD", ("Microsoft YaHei", 14, "bold")),
    ).pack(side=tk.LEFT, padx=(16, 0))

    # ---------- 批量操作条（复选框联动） ----------
    app.selection_count_var = getattr(app, "selection_count_var", None) or tk.StringVar(value="已选 0 项")

    def _run_checked():
        if not getattr(app, "checked_names", None):
            app.helpers.on_log("⚠️ 请先在左侧勾选要计算的文件的复选框", "warning")
            return
        app.controller.show_psi4_dialog()

    def _delete_checked():
        if not getattr(app, "checked_names", None):
            app.helpers.on_log("⚠️ 请先勾选要删除的文件的复选框", "warning")
            return
        app.controller.delete_selected()

    # （批量操作条改为底部「浮动 · 可隐藏」条，见下方 Treeview 创建之后的浮动条创建块）

    # ---------- 文件 Treeview（含多选复选框列） ----------
    # 勾选状态以「文件名」为键存于 app.checked_names（跨筛选/重渲染保持），
    # 作为所有批量操作（计算/导出/删除/描述符）的唯一真值来源。
    if not hasattr(app, "checked_names") or app.checked_names is None:
        app.checked_names = set()
    # P-04：可见行「已勾选」计数，O(1) 维护，避免 _tree_update_check_state 逐行扫描几千行
    if not hasattr(app, "_vis_checked"):
        app._vis_checked = 0
    # 浮动批量条：True=允许显示（选中自动浮现）；用户点 ✕ 收起后置 False，下次勾选再自动重开
    app.batch_bar_open = getattr(app, "batch_bar_open", True)
    app._pending_toggle_id = None

    def _tree_toggle_row(iid):
        try:
            vals = app.tree.item(iid, "values")
            if not vals or len(vals) < 2:
                return
            name = vals[1]  # 文件名（select 列之后）
        except Exception:
            return
        if name in app.checked_names:
            app.checked_names.discard(name)
            app.tree.set(iid, "select", CHECK_GLYPH["off"])
            app._vis_checked = max(0, app._vis_checked - 1)
        else:
            app.checked_names.add(name)
            app.tree.set(iid, "select", CHECK_GLYPH["on"])
            app._vis_checked += 1
        app.batch_bar_open = True
        _tree_update_check_state()

    def _tree_toggle_all():
        children = app.tree.get_children()
        if not children:
            return
        # 仅一次 O(N) 读扫描判定当前是否全选（读比写便宜，且只扫一遍）
        all_on = all(app.tree.set(c, "select") == CHECK_GLYPH["on"] for c in children)
        new_on = not all_on
        names = set()
        if new_on:
            for c in children:
                try:
                    v = app.tree.item(c, "values")
                    if v and len(v) >= 2:
                        names.add(v[1])
                except Exception:
                    pass
        app.checked_names = names
        app.batch_bar_open = True
        # P-04：可见勾选计数同步为「全选=可见行数 / 全不选=0」，O(1) 维护
        app._vis_checked = len(children) if new_on else 0
        # 🔴 P-04 修复：全选/全不选的字形重绘**分批**进行（每批 400 行 + after_idle 让出
        # 主线程事件循环），避免一次性 tree.set 几千行导致 GUI 卡死。
        _repaint_selection_glyphs(new_on, 0)

    def _repaint_selection_glyphs(new_on: bool, start: int):
        children = app.tree.get_children()
        n = len(children)
        if start >= n:
            _tree_update_check_state()
            return
        end = min(start + 400, n)
        glyph = CHECK_GLYPH["on"] if new_on else CHECK_GLYPH["off"]
        for i in range(start, end):
            try:
                app.tree.set(children[i], "select", glyph)
            except Exception:
                pass
        if end < n:
            app.after_idle(_repaint_selection_glyphs, new_on, end)
        else:
            _tree_update_check_state()

    def _tree_update_check_state():
        children = app.tree.get_children()
        n = len(app.checked_names)
        # 表头半选态：用 checked_names 与可见行数 O(1) 推导，不再逐行 tree.set 扫描
        if not children or n == 0:
            head = CHECK_GLYPH["off"]
        elif n >= len(children):
            head = CHECK_GLYPH["on"]
        else:
            head = CHECK_GLYPH["partial"]
        try:
            app.tree.heading("select", text=head)
        except Exception:
            pass
        try:
            app.selection_count_var.set(f"已选 {n} 项")
        except Exception:
            pass
        # 计算按钮联动（文件页批量条 + 计算页运行按钮）
        enabled = n > 0
        for btn in (
            getattr(app, "batch_run_btn", None),
            getattr(app, "batch_del_btn", None),
            getattr(app, "run_selected_btn", None),
        ):
            if btn is not None:
                try:
                    btn.config(state="normal" if enabled else "disabled")
                except Exception:
                    pass
        try:
            rb = getattr(app, "run_selected_btn", None)
            if rb is not None:
                rb.config(text=f"▶  运行所选 {n} 个文件" if n else "▶  运行所选文件")
        except Exception:
            pass
        # 浮动条显隐：选中≥1 且未手动收起 → 浮现；否则收起
        try:
            bar = getattr(app, "batch_bar", None)
            if bar is not None:
                if getattr(app, "batch_bar_open", True) and n > 0:
                    bar.place(relx=0.0, rely=1.0, x=10, y=-10, anchor="sw")
                    bar.lift()
                else:
                    bar.place_forget()
        except Exception:
            pass
        # 过滤条「批量操作」按钮标签随选中数变化
        try:
            tb = getattr(app, "batch_toggle_btn", None)
            if tb is not None:
                tb.config(text=f"批量操作 ▾ ({n})" if n else "批量操作 ▾")
        except Exception:
            pass

    # 暴露给 app_helpers.render_files：重渲染后刷新表头半选态与计数
    app._tree_update_check_state = _tree_update_check_state

    def _tree_hide_bar():
        # 手动收起浮动批量条（下次勾选会自动重开）
        app.batch_bar_open = False
        try:
            app.batch_bar.place_forget()
        except Exception:
            pass

    def _update_chn_in_models(name, new_chn):
        # 中文名仅作显示字段；同步到主列表与各视图，跨筛选/重渲染保持
        for lst in (getattr(app, "current_files", None), getattr(app, "last_scan_result", None)):
            if not lst:
                continue
            for f in lst:
                if isinstance(f, dict) and f.get("name") == name:
                    f["chn"] = new_chn

    def _open_chn_editor(iid, name, chn):
        top = tk.Toplevel(app)
        top.title("编辑中文名")
        top.transient(app)
        top.resizable(False, False)
        try:
            top.grab_set()
        except Exception:
            pass
        frm = tk.Frame(top, bg=COLORS["bg"], padx=14, pady=12)
        frm.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frm,
            text=f"文件：{name}",
            bg=COLORS["bg"],
            fg=COLORS["text_secondary"],
            font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 11)),
        ).pack(anchor="w")
        tk.Label(
            frm,
            text="中文名（显示）：",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 12)),
        ).pack(anchor="w", pady=(8, 2))
        var = tk.StringVar(value=chn or "")
        ent = ttk.Entry(
            frm, textvariable=var, width=42, font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 12))
        )
        ent.pack(fill=tk.X, pady=(0, 10))
        ent.focus_set()
        ent.select_range(0, tk.END)

        def _commit():
            new = var.get().strip()
            try:
                app.tree.set(iid, "中文名", new)
            except Exception:
                pass
            _update_chn_in_models(name, new)
            try:
                top.destroy()
            except Exception:
                pass
            try:
                app.helpers.on_log(f"✏️ 已更新「{name}」的中文名为：{new or '（空）'}", "info")
            except Exception:
                pass

        def _cancel():
            try:
                top.destroy()
            except Exception:
                pass

        btns = tk.Frame(frm, bg=COLORS["bg"])
        btns.pack(anchor="e")
        themed_button(btns, "✔ 确定", _commit, "success").pack(side=tk.LEFT, padx=4)
        themed_button(btns, "取消", _cancel, "secondary").pack(side=tk.LEFT, padx=4)
        ent.bind("<Return>", lambda e: _commit())
        ent.bind("<Escape>", lambda e: _cancel())

    def _tree_on_click(event):
        region = app.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row = app.tree.identify_row(event.y)
        if not row:
            return
        rid = row
        # 防抖：220ms 内若发生双击则取消本次切换（双击用于编辑中文名，避免复选框闪烁）
        if getattr(app, "_pending_toggle_id", None) is not None:
            try:
                app.tree.after_cancel(app._pending_toggle_id)
            except Exception:
                pass
        app._pending_toggle_id = app.tree.after(220, lambda: _tree_toggle_row(rid))

    def _tree_on_double(event):
        # 取消可能挂起的单击切换
        if getattr(app, "_pending_toggle_id", None) is not None:
            try:
                app.tree.after_cancel(app._pending_toggle_id)
            except Exception:
                pass
            app._pending_toggle_id = None
        region = app.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row = app.tree.identify_row(event.y)
        if not row:
            return
        vals = app.tree.item(row, "values")
        if not vals or len(vals) < 5:
            return
        _open_chn_editor(row, vals[1], vals[4])

    columns = ("select", "文件名", "状态", "英文名", "中文名")
    app.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=18)
    import ui.ui_theme as _ut

    _ut.bind_treeview_hover(app.tree)
    app.tree.heading("select", text=CHECK_GLYPH["off"], command=_tree_toggle_all)
    app.tree.heading("文件名", text="文件名")
    app.tree.heading("状态", text="状态")
    app.tree.heading("英文名", text="英文名")
    app.tree.heading("中文名", text="中文名")
    app.tree.column("select", width=40, anchor=tk.CENTER, stretch=False)
    app.tree.column("文件名", width=330, anchor=tk.W)
    app.tree.column("状态", width=150, anchor=tk.CENTER)
    app.tree.column("英文名", width=210, anchor=tk.W)
    app.tree.column("中文名", width=210, anchor=tk.W)
    app.tree.bind("<Double-1>", _tree_on_double)
    app.tree.bind("<Button-1>", _tree_on_click)

    style = ttk.Style()
    style.configure("Treeview", font=getattr(app, "_fonts", {}).get("BASE", ("Microsoft YaHei", 12)), rowheight=30)
    style.configure("Treeview.Heading", font=getattr(app, "_fonts", {}).get("BOLD", ("Microsoft YaHei", 14, "bold")))

    vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=app.tree.yview)
    app.tree.configure(yscrollcommand=vsb.set)
    app.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------- 批量操作浮动条（复选框联动，可隐藏） ----------
    # 浮动覆盖在文件列表底部左侧（避开右侧滚动条）；选中≥1 自动浮现，点 ✕ 收起；
    # 过滤条「批量操作 ▾」按钮可重新展开，标签随选中数变化。
    batch_bar = tk.Frame(
        list_frame,
        bg=COLORS["card_bg"],
        relief=tk.RAISED,
        bd=2,
        highlightbackground=COLORS["card_border"],
        highlightthickness=1,
    )
    app.batch_bar = batch_bar
    tk.Label(
        batch_bar,
        textvariable=app.selection_count_var,
        bg=COLORS["card_bg"],
        fg=COLORS["accent"],
        font=getattr(app, "_fonts", {}).get("BOLD", ("Microsoft YaHei", 12, "bold")),
    ).pack(side=tk.LEFT, padx=(10, 8))
    app.batch_run_btn = themed_button(batch_bar, "▶ 计算所选", _run_checked, "success")
    app.batch_run_btn.pack(side=tk.LEFT, padx=4)
    app.batch_del_btn = themed_button(batch_bar, "🗑 删除所选", _delete_checked, "danger")
    app.batch_del_btn.pack(side=tk.LEFT, padx=4)
    close_btn = tk.Button(
        batch_bar,
        text="✕",
        command=_tree_hide_bar,
        relief=tk.FLAT,
        bd=0,
        cursor="hand2",
        padx=6,
        pady=2,
        bg=COLORS["card_bg"],
        fg=COLORS["text_secondary"],
        font=getattr(app, "_fonts", {}).get("BOLD", ("Microsoft YaHei", 12, "bold")),
    )
    close_btn.pack(side=tk.LEFT, padx=(4, 8))
    # 主题刷新时同步浮动条 / 关闭按钮配色
    _ut._register(batch_bar, lambda w: w.config(bg=COLORS["card_bg"], highlightbackground=COLORS["card_border"]))
    _ut._register(close_btn, lambda w: w.config(bg=COLORS["card_bg"], fg=COLORS["text_secondary"]))
    # 浮动定位（覆盖 Treeview 底部，初始隐藏，选中后由 _tree_update_check_state 显示）
    batch_bar.place(relx=0.0, rely=1.0, x=10, y=-10, anchor="sw")
    batch_bar.lift()

    app.context_menu = tk.Menu(app, tearoff=0)
    app.context_menu.add_command(label="🗑️ 删除勾选文件", command=app.controller.delete_selected)
    app.tree.bind("<Button-3>", app.controller.show_context_menu)

    # 初始刷新（统一表头/计数/按钮状态）
    _tree_update_check_state()

    # ---------- 日志 ----------
    log_frame = tk.LabelFrame(
        paned,
        text="📋 日志（所有操作/错误实时显示）",
        bg=COLORS["card_bg"],
        font=getattr(app, "_fonts", {}).get("H1", ("Microsoft YaHei", 14, "bold")),
        relief=tk.GROOVE,
        bd=2,
    )
    paned.add(log_frame, weight=1)

    log_toolbar = tk.Frame(log_frame, bg=COLORS["card_bg"])
    log_toolbar.pack(fill=tk.X, padx=8, pady=6)
    ttk.Button(log_toolbar, text="🗑️ 清空日志", command=app.helpers.clear_log, width=12).pack(side=tk.LEFT)

    # ---------- F15 日志过滤条（T06 挂载点）----------
    # 放在「清空日志」工具条下面、日志正文上面，形成 [工具条] / [过滤条] / [正文] 三层。
    # 采用局部导入：过滤条是增值功能，模块导入失败也不能让整个 build_ui 崩掉。
    try:
        from ui.log_filter_bar import build_log_filter_bar

        build_log_filter_bar(app, log_frame, COLORS)
    except Exception as _e_filter_bar:
        try:
            from utils.logger import default_logger as _log

            _log.warning("⚠️ 日志过滤条挂载失败（日志面板仍可用）: %s", _e_filter_bar)
        except Exception:
            pass

    # 日志台始终深色（科学工具约定：深色控制台 + 浅色工作区，护眼且突出输出）
    app.log_text = scrolledtext.ScrolledText(
        log_frame,
        height=10,
        wrap=tk.WORD,
        font=getattr(app, "_fonts", {}).get("LOG", ("Consolas", 13)),
        bg="#0F172A",
        fg="#C8D3E0",
    )
    app.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    app.log_text.tag_config("info", foreground="#8AB4F8")
    app.log_text.tag_config("success", foreground="#3FB950")
    app.log_text.tag_config("error", foreground="#F85149")
    app.log_text.tag_config("warning", foreground="#D29922")


# ===========================================================
# 📊 Tab4：任务队列（设计落地 Phase 5）
# ===========================================================


def _status_cn(st):
    return {"running": "运行中", "success": "成功", "failed": "失败", "cancelled": "已取消", "queued": "排队"}.get(
        st, st
    )


def _open_queue_log_drawer(app, job):
    """队列任务日志右侧滑出面板（设计落地 Phase 5）。"""
    # 防重复：同一任务已开则置顶
    for w in app.winfo_children():
        if getattr(w, "_is_log_drawer", False) and getattr(w, "_drawer_job_id", None) == job.get("id"):
            try:
                w.lift()
            except Exception:
                pass
            return
    dlg = tk.Toplevel(app)
    dlg._is_log_drawer = True
    dlg._drawer_job_id = job.get("id")
    dlg.transient(app)
    dlg.overrideredirect(True)
    dlg.title("任务日志")
    P = ui_theme.get_palette()
    dlg.configure(bg=P["border_strong"])

    try:
        sw = app.winfo_screenwidth()
        sh = app.winfo_screenheight()
    except Exception:
        sw, sh = 1920, 1080
    H = min(600, sh - 60)
    W = 420
    x = max(0, sw - W - 10)
    y = 30
    dlg.geometry(f"{W}x{H}+{x}+{y}")

    # 头部
    head = tk.Frame(dlg, bg=P["surface"], bd=0)
    head.pack(fill=tk.X, padx=1, pady=1)
    tk.Label(
        head,
        text="📜 任务日志 · %s" % job.get("name", ""),
        bg=P["surface"],
        fg=P["text"],
        font=("Microsoft YaHei", 12, "bold"),
        anchor="w",
        padx=12,
        pady=8,
    ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _close():
        try:
            dlg.destroy()
        except Exception:
            pass

    tk.Button(
        head,
        text="✕",
        command=_close,
        relief=tk.FLAT,
        bd=0,
        bg=P["surface"],
        fg=P["text_secondary"],
        activebackground=P["border"],
        activeforeground=P["accent"],
        font=("Microsoft YaHei", 12),
        cursor="hand2",
        width=3,
        padx=6,
        pady=4,
    ).pack(side=tk.RIGHT, padx=6, pady=4)

    # 日志正文
    body = tk.Frame(dlg, bg=P["input"], bd=1, relief=tk.SOLID, highlightbackground=P["border"], highlightthickness=1)
    body.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
    txt = tk.Text(
        body, bg=P["input"], fg=P["text"], relief=tk.FLAT, bd=0, font=("Consolas", 10), wrap=tk.WORD, state=tk.DISABLED
    )
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
    sb = tk.Scrollbar(body, command=txt.yview, bg=P["surface"], troughcolor=P["bg"], bd=0, relief=tk.FLAT)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    txt.config(yscrollcommand=sb.set)
    logs = job.get("log") or []
    txt.configure(state=tk.NORMAL)
    if logs:
        for ln in logs:
            txt.insert(tk.END, ln + "\n")
    else:
        txt.insert(tk.END, "（暂无日志输出）")
    txt.configure(state=tk.DISABLED)

    dlg.bind("<Escape>", lambda e: _close())


def build_tab_compute_queue(app, parent):
    """
    任务队列页（统一后台任务可视化，设计落地 Phase 5）：
      - 工具条：取消当前任务 / 清除已完成 / 并发度下拉（1/2/4/8，持久化）
      - 任务表：# / 名称 / 类型 / 方法-基组 / 状态 / 进度 / 耗时 / 操作
      - 每行操作：日志（右侧滑出抽屉）、失败行额外「诊断」（F07）
      - 无任务时显示空状态引导
    数据来自 app.task_manager.jobs（由 run_task→submit 接入，Phase 5 包装）。
    """
    parent.grid_rowconfigure(1, weight=1)
    parent.grid_columnconfigure(0, weight=1)
    F = getattr(app, "_fonts", {}) or {}

    # —— 工具条 ——
    tool = tk.Frame(
        parent,
        bg=COLORS["card_bg"],
        bd=1,
        relief=tk.SOLID,
        highlightbackground=COLORS["card_border"],
        highlightthickness=1,
    )
    tool.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 6))
    tool.grid_columnconfigure(5, weight=1)

    def _cancel_current():
        try:
            app.task_manager.request_cancel()
            app.helpers.on_log("⏹ 已请求取消当前任务", "warning")
        except Exception:
            pass

    def _clear_finished():
        try:
            with app.task_manager._jobs_lock:
                app.task_manager.jobs = [j for j in app.task_manager.jobs if j.get("status") == "running"]
            refresh_queue()
            app.helpers.on_log("🧹 已清除已完成任务", "info")
        except Exception:
            pass

    themed_button(
        tool, "⏹ 取消当前任务", _cancel_current, "warning", tip="请求取消正在运行的任务（协作式，下次进度上报时中止）"
    ).pack(side=tk.LEFT, padx=4, pady=6)
    themed_button(
        tool, "🧹 清除已完成", _clear_finished, "secondary", tip="从列表中移除成功 / 失败 / 已取消的任务"
    ).pack(side=tk.LEFT, padx=4, pady=6)

    # 并发度下拉（持久化；当前常驻 worker 串行执行，此值为规划档位）
    tk.Label(
        tool,
        text="并发度:",
        bg=COLORS["card_bg"],
        fg=COLORS["text_light"],
        font=F.get("SMALL", ("Microsoft YaHei", 11)),
    ).pack(side=tk.LEFT, padx=(16, 4), pady=6)
    _conc_var = tk.StringVar(value=str(int(app.config_data.get("queue_concurrency", 2) or 2)))
    _conc = ttk.Combobox(
        tool,
        textvariable=_conc_var,
        values=["1", "2", "4", "8"],
        width=5,
        state="readonly",
        font=F.get("BASE", ("Microsoft YaHei", 12)),
    )
    _conc.pack(side=tk.LEFT, padx=2, pady=6)
    add_tooltip(_conc, "同时运行的任务数（1=串行，2/4/8=并行）。实时生效并保存到配置。")

    def _on_conc(_e=None):
        try:
            v = int(_conc_var.get())
            app.config_data["queue_concurrency"] = v
            from utils.config import save_config

            save_config(app.config_data)
            # 实时驱动常驻 worker 池并发度（无需重启）
            tm = getattr(app, "task_manager", None)
            if tm is not None and hasattr(tm, "set_concurrency"):
                tm.set_concurrency(v)
            app.helpers.on_log("🔧 并发度已设为 %d（实时生效，已保存）" % v, "info")
        except Exception:
            pass

    _conc.bind("<<ComboboxSelected>>", _on_conc)

    # —— 任务表 ——
    tbl_card = dark_card(parent)
    tbl_card.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    tbl_card.grid_rowconfigure(0, weight=1)
    tbl_card.grid_columnconfigure(0, weight=1)

    cols = ("#", "名称", "类型", "方法-基组", "状态", "进度", "耗时", "操作")
    tree = ttk.Treeview(tbl_card, columns=cols, show="headings", height=14)
    for c, w in zip(cols, (4, 22, 12, 18, 10, 10, 10, 22), strict=False):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor=tk.W if c in ("名称", "类型", "方法-基组") else tk.CENTER)
    tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
    vsb = ttk.Scrollbar(tbl_card, command=tree.yview)
    vsb.grid(row=0, column=1, sticky="ns", pady=10)
    tree.configure(yscrollcommand=vsb.set)

    # 状态色标签
    P = ui_theme.get_palette()
    tree.tag_configure("st_running", foreground=P["link"])
    tree.tag_configure("st_success", foreground=P["success"])
    tree.tag_configure("st_failed", foreground=P["danger"])
    tree.tag_configure("st_cancelled", foreground=P["text_muted"])

    app._queue_tree = tree

    # —— 空状态 ——
    es = tk.Frame(tbl_card, bg=COLORS["surface"])
    es.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
    es.grid_remove()
    tk.Label(
        es, text="📭  暂无任务", bg=COLORS["surface"], fg=COLORS["text"], font=("Microsoft YaHei", 15, "bold")
    ).pack(anchor="center", pady=(40, 6))
    tk.Label(
        es,
        text="去「计算与动画」提交 PSI4 计算，或运行文件整理 / OpenBabel 工具，\n"
        "任务会自动出现在这里并实时显示进度与日志。",
        bg=COLORS["surface"],
        fg=COLORS["text_secondary"],
        font=("Microsoft YaHei", 11),
        justify="center",
    ).pack(anchor="center")
    app._queue_empty = es

    def _fmt_dur(j):
        try:
            s = (j.get("finished") or time.time()) - j.get("started", time.time())
            return "%.0fs" % max(0, s)
        except Exception:
            return "—"

    def _open_diag(job):
        try:
            app.show_error_diagnosis(job.get("error", ""), summary="任务失败：%s" % job.get("name", ""))
        except Exception:
            pass

    def _on_click(event):
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        row = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not row or col != "#8":  # 仅「操作」列响应
            return
        job = getattr(tree, "_job_map", {}).get(row)
        if not job:
            return
        if job.get("status") == "failed" and "诊断" in tree.set(row, "操作"):
            _open_diag(job)
        else:
            _open_queue_log_drawer(app, job)

    tree.bind("<Button-1>", _on_click)

    def refresh_queue():
        jobs = getattr(app.task_manager, "jobs", [])
        with app.task_manager._jobs_lock:
            snapshot = list(jobs)
        tree.delete(*tree.get_children())
        tree._job_map = {}
        if not snapshot:
            es.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
            return
        es.grid_remove()
        for j in snapshot:
            st = j.get("status", "running")
            tag = {
                "running": "st_running",
                "success": "st_success",
                "failed": "st_failed",
                "cancelled": "st_cancelled",
            }.get(st, "st_running")
            op = "日志 · 诊断" if st == "failed" else "日志"
            vals = (
                j.get("id", ""),
                j.get("name", ""),
                j.get("kind", ""),
                j.get("spec", "—"),
                _status_cn(st),
                "%d%%" % j.get("progress", 0),
                _fmt_dur(j),
                op,
            )
            iid = tree.insert("", tk.END, values=vals, tags=(tag,))
            tree._job_map[iid] = j

    app.refresh_queue = refresh_queue

    # 周期性刷新（仅队列页可见时刷新，省开销）
    def _poll():
        try:
            if getattr(app, "_cur_page", 0) == 5:  # 任务队列（工作台/文件管理/分子映射/计算/高级已占前 5 页）
                refresh_queue()
        except Exception:
            pass
        try:
            app.after(700, _poll)
        except Exception:
            pass

    refresh_queue()
    app.after(700, _poll)


# ===========================================================
# 📊 底部状态栏（新版：状态 + 进度 + 操作提示 + OB 指示灯）
# ===========================================================


def _inject_action_tips(app):
    """
    把常见 controller 动作包一层「动作完成后写提示到 action_tip_var」。
    非侵入式：用 try/except，失败不影响功能。
    """

    def _tip(msg: str):
        try:
            app.action_tip_var.set("💡 " + msg)
        except Exception:
            pass

    # 给几个最常用的控制器函数包装
    pairs = [
        ("scan_files", "已扫描文件列表，下一步：点「🔧 一键修复全部」自动处理命名问题"),
        ("run_fix_by_mode", "修复已完成。下一步：点「📂 按类型整理」或「📁 按文件名分组」归档"),
        ("organize_by_type", "已按扩展名整理归档。下一步：选文件 → 切到「🔬 计算与动画」运行预设"),
        ("organize_by_basename", "已按基本名分组（每个分子一个子目录）。下一步：点「生成缺失映射表」批量补名"),
        ("load_mapping_file", "映射已加载！列表里中文名已更新。下一步：点「一键修复全部」执行映射重命名"),
        ("generate_missing", "缺失的文件名已导出 CSV。填完中文名后，用「映射管理器」导入即可"),
        ("undo_last", "已撤销上一步。需要前进？点工具栏「↪ 重做」"),
        ("remove_duplicate_files", "重复文件清理完成。建议先点「扫描文件」确认结果"),
    ]
    for name, tip in pairs:
        try:
            original = getattr(app.controller, name)

            def _wrap(fn, t):
                def _w(*a, **kw):
                    try:
                        ret = fn(*a, **kw)
                    finally:
                        try:
                            _tip(t)
                        except Exception:
                            pass
                    return ret

                return _w

            setattr(app.controller, name, _wrap(original, tip))
        except Exception:
            pass
