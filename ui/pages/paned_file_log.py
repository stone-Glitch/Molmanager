"""📊 公共：文件列表 + 日志（垂直分割；自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import tkinter as tk
from tkinter import scrolledtext, ttk

from ui.theme_tokens import SPACING, STROKE
from ui.ui_theme import CHECK_GLYPH, COLORS, LOG_CONSOLE, LOG_TAG_KEYS, themed_button

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
        relief=tk.FLAT,
        bd=0,
        highlightbackground=COLORS["card_border"],
        highlightthickness=STROKE["hair"],
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
        relief=tk.FLAT,
        bd=0,
        highlightbackground=COLORS["accent"],
        highlightthickness=STROKE["hair"],
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
        relief=tk.FLAT,
        bd=0,
        highlightbackground=COLORS["card_border"],
        highlightthickness=STROKE["hair"],
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
        bg=LOG_CONSOLE["bg"],
        fg=LOG_CONSOLE["fg"],
    )
    app.log_text.pack(fill=tk.BOTH, expand=True, padx=SPACING["sm"], pady=(0, SPACING["sm"]))

    P_log = COLORS
    for _tag, _key in LOG_TAG_KEYS.items():
        app.log_text.tag_config(_tag, foreground=P_log[_key])
