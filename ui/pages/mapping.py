"""🧬 分子映射页（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import tkinter as tk
from tkinter import ttk

from ui.ui_theme import COLORS, dark_card, section_title, themed_button
from ui.ui_builder._theme import add_tooltip

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
