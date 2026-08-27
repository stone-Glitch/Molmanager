#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
映射管理对话框 - 映射表导入/导出/编辑
"""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from utils.dialog_geom import fit_dialog_geometry
from utils.mapping_utils import (
    find_fuzzy_pairs,
    fuzzy_suggestions,
    generate_blank_template,
    suggest_mapping_from_dir,
)


def show_mapping_manager_dialog(app, controller):
    model = controller.model
    dialog = tk.Toplevel(app)
    dialog.title("📋 映射表管理")
    dialog.geometry(fit_dialog_geometry(dialog, 560, 380))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    info_label_var = tk.StringVar(value=f"当前映射条目：{len(model.mapping)}  |  缺失映射：{len(model.generate_missing_list())}")
    ttk.Label(dialog, textvariable=info_label_var, font=('Arial', 10, 'bold')).pack(pady=15)

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)

    def refresh_info():
        info_label_var.set(f"当前映射条目：{len(model.mapping)}  |  缺失映射：{len(model.generate_missing_list())}")

    def export_missing():
        csv_path = filedialog.asksaveasfilename(
            initialdir=str(model.work_dir),
            initialfile="missing_mapping.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            defaultextension=".csv"
        )
        if not csv_path:
            return
        try:
            count = model.export_missing_csv(str(Path(csv_path)))
            messagebox.showinfo("导出成功", f"已导出 {count} 条缺失映射记录到：\n{csv_path}", parent=dialog)
            app.helpers.on_log(f"💾 导出缺失映射表: {count} 条", 'success')
        except Exception as e:
            messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 导出缺失映射表失败: {e}", 'error')

    def import_missing(overwrite=False):
        csv_path = filedialog.askopenfilename(
            initialdir=str(model.work_dir),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="选择映射表 CSV 文件"
        )
        if not csv_path:
            return
        try:
            result = model.import_mapping_csv(str(Path(csv_path)), overwrite=overwrite)
            app.helpers.on_log(
                f"📥 导入映射表: 新增 {result['added']} 条, 跳过 {result['skipped']} 条, "
                f"错误 {result['errors']} 条, 总行数 {result['total_rows']}",
                'success' if result['errors'] == 0 else 'warning'
            )
            # 科学红线 S-06：中文名冲突必须显式告知
            if result.get("dup_chn", 0) > 0:
                _ex = "；".join(
                    f"「{c[0]}」←{c[1]}/{c[2]}" for c in result["chn_conflicts"][:10]
                )
                app.helpers.on_log(
                    f"⚠️ 导入发现 {result['dup_chn']} 处中文名冲突（多英文名共用同一中文名，"
                    f"反向映射将只保留其一）：{_ex}",
                    'warning'
                )
            refresh_info()
            _msg = (
                f"导入结果：\n  新增：{result['added']} 条\n  跳过：{result['skipped']} 条\n"
                f"  错误：{result['errors']} 条"
            )
            if result.get("dup_chn", 0) > 0:
                _msg += (
                    f"\n\n⚠️ 中文名冲突 {result['dup_chn']} 处：多个英文名共用同一中文名，"
                    "反向映射将只保留其一，其余会悄悄丢失。建议在编辑器中拆分。"
                )
            if messagebox.askyesno("导入完成", _msg + "\n\n是否刷新文件列表？", parent=dialog):
                controller.scan_files()
        except Exception as e:
            messagebox.showerror("导入失败", f"导入失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 导入映射表失败: {e}", 'error')

    def export_mapping():
        csv_path = filedialog.asksaveasfilename(
            initialdir=str(model.work_dir),
            initialfile="mapping_full.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            defaultextension=".csv"
        )
        if not csv_path:
            return
        try:
            count = model.export_mapping_csv(str(Path(csv_path)))
            messagebox.showinfo("导出成功", f"已导出 {count} 条映射记录到：\n{csv_path}", parent=dialog)
            app.helpers.on_log(f"💾 导出完整映射表: {count} 条", 'success')
        except Exception as e:
            messagebox.showerror("导出失败", f"导出失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 导出完整映射表失败: {e}", 'error')

    btn_export_missing = ttk.Button(btn_frame, text="💾 导出缺失表 (CSV)", command=export_missing, width=28)
    btn_export_missing.grid(row=0, column=0, padx=10, pady=8)

    btn_import_missing = ttk.Button(btn_frame, text="📥 导入缺失表 (CSV)", command=lambda: import_missing(overwrite=False), width=28)
    btn_import_missing.grid(row=0, column=1, padx=10, pady=8)

    btn_export_mapping = ttk.Button(btn_frame, text="📤 导出当前映射表", command=export_mapping, width=28)
    btn_export_mapping.grid(row=1, column=0, padx=10, pady=8)

    btn_import_overwrite = ttk.Button(btn_frame, text="🔄 覆盖式导入", command=lambda: import_missing(overwrite=True), width=28)
    btn_import_overwrite.grid(row=1, column=1, padx=10, pady=8)

    btn_frame.grid_columnconfigure(0, weight=1)
    btn_frame.grid_columnconfigure(1, weight=1)

    ttk.Button(dialog, text="关闭", command=dialog.destroy, width=20).pack(pady=20)


def show_mapping_editor_dialog(app, controller):
    model = controller.model
    search_var = tk.StringVar()  # M-04：搜索框状态
    dialog = tk.Toplevel(app)
    dialog.title("📋 分子命名映射表编辑器")
    dialog.geometry(fit_dialog_geometry(dialog, 750, 550))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    top_info = ttk.Label(
        dialog,
        text="提示：双击单元格即可编辑英文名 / 中文名；英文名不能为空。",
        foreground="blue"
    )
    top_info.pack(anchor=tk.W, padx=12, pady=(10, 4))

    btn_top = ttk.Frame(dialog)
    btn_top.pack(fill=tk.X, padx=10, pady=5)

    def _tv_sort_column(tv, col, reverse):
        rows = [(tv.set(k, col), k) for k in tv.get_children("")]
        rows.sort(key=lambda r: r[0], reverse=reverse)
        for i, (_, k) in enumerate(rows):
            tv.move(k, "", i)
        tv.heading(col, command=lambda: _tv_sort_column(tv, col, not reverse))

    # M-04：主行清单（含新增行与被搜索隐藏的行），供过滤器正确还原/隐藏
    all_iids = []

    # M-07：撤销/重做提交占位。真实实现稍后绑定到快照 UndoStack；绑定失败也保持 no-op，绝不影响编辑。
    def _commit(label):
        pass

    def _add_row():
        new_iid = tv.insert("", tk.END, values=("NEW_ENGLISH_1", "中文名待填"))
        all_iids.append(new_iid)
        tv.selection_set(new_iid)
        tv.see(new_iid)
        _commit("添加行")

    def _del_selected():
        sel = tv.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要删除的行", parent=dialog)
            return
        if not messagebox.askyesno("确认删除", f"确定删除选中的 {len(sel)} 行映射？", parent=dialog):
            return
        for iid in reversed(sel):
            tv.delete(iid)
            if iid in all_iids:
                all_iids.remove(iid)
        _commit("删除行")

    def _on_double_click(event):
        rowid = tv.identify_row(event.y)
        col = tv.identify_column(event.x)
        if not rowid or not col:
            return
        try:
            col_idx = int(col.replace("#", "")) - 1
        except ValueError:
            return
        col_name = ("英文名", "中文名")[col_idx] if 0 <= col_idx < 2 else None
        if col_name is None:
            return
        current_vals = list(tv.item(rowid, "values"))
        if col_idx >= len(current_vals):
            current_vals.extend([""] * (col_idx + 1 - len(current_vals)))
        old_val = current_vals[col_idx]
        new_val = simpledialog.askstring(
            "编辑单元格",
            f"请输入新的{col_name}：",
            initialvalue=str(old_val),
            parent=dialog
        )
        if new_val is None:
            return
        new_val = new_val.strip()
        if col_idx == 0 and not new_val:
            messagebox.showwarning("提示", "英文名不能为空", parent=dialog)
            return
        current_vals[col_idx] = new_val
        tv.item(rowid, values=tuple(current_vals))
        _commit("编辑单元格")
        # M-03：编辑中文名后，主动建议已有近似写法（编辑距离 ≤2），避免拼写重复（如 苯/笨）
        if col_idx == 1 and new_val:
            others = []
            for iid2 in tv.get_children(""):
                if iid2 == rowid:
                    continue
                v2 = tv.item(iid2, "values")
                if len(v2) > 1 and v2[1]:
                    others.append(str(v2[1]))
            sug = fuzzy_suggestions(new_val, others, max_dist=2)
            if sug:
                messagebox.showinfo(
                    "拼写建议",
                    f"检测到 {len(sug)} 个近似中文名，可能是拼写重复：\n" + "、".join(sug),
                    parent=dialog,
                )

    def _save_mapping():
        rows = []
        invalid = False
        for iid in tv.get_children(""):
            vals = tv.item(iid, "values")
            if len(vals) < 2:
                continue
            eng = str(vals[0]).strip()
            chn = str(vals[1]).strip() if len(vals) > 1 else ""
            if not eng:
                invalid = True
                continue
            rows.append((eng, chn))
        if invalid:
            messagebox.showwarning("提示", "存在英文名空的行，已自动跳过这些行", parent=dialog)
        new_dict = {}
        dup_eng = 0
        for eng, chn in rows:
            if eng in new_dict:
                dup_eng += 1
                continue
            new_dict[eng] = chn
        warn_lines = []
        if dup_eng:
            warn_lines.append(f"有 {dup_eng} 条重复英文名已去重")
        # M-03：中文名模糊重复（编辑距离 ≤2）扫描，避免拼写近似导致的隐性重复
        chn_list = [c for _, c in rows if c]
        fuzzy = find_fuzzy_pairs(chn_list, max_dist=2)
        fuzzy_ex = ""
        if fuzzy:
            fuzzy_ex = "；".join(f"「{a}」≈「{b}」" for a, b, _ in fuzzy[:10])
            warn_lines.append(
                f"发现 {len(fuzzy)} 对近似中文名（编辑距离≤2，可能是拼写重复）：{fuzzy_ex}"
            )
        extra = ("\n（注意：" + "；".join(warn_lines) + "）") if warn_lines else ""
        if not messagebox.askyesno(
            "确认保存",
            f"是否保存 {len(new_dict)} 条映射到工作目录下的「分子命名映射.json」？" + extra,
            parent=dialog
        ):
            return
        # T10：保存逻辑已下沉到 model.save_mapping()。
        # 由 model 统一负责「快照 → 原子写 → 同步内存状态」三件事，
        # UI 层不再直接写盘，这样 F17 的备份钩子才有唯一挂载点。
        try:
            out_path = model.save_mapping(new_dict)
        except Exception as e:
            messagebox.showerror("保存失败", f"写入文件失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 保存映射表失败: {e}", "error")
            return
        # M-07 边界修复：保存成功后把「保存后」状态重置为撤销基线，
        # 清空删除行等陈旧撤销项，避免保存后再 undo 还原出已删除的空行。
        try:
            undo_stack.reset(_snapshot_rows())
        except Exception:
            pass
        messagebox.showinfo("保存成功", f"已保存 {len(new_dict)} 条映射到：\n{out_path}", parent=dialog)
        if fuzzy:
            app.helpers.on_log(f"⚠️ 映射表保存发现 {len(fuzzy)} 对近似中文名：{fuzzy_ex}", 'warning')
        controller.scan_files()

    def _gen_template():
        # M-06：生成空白映射模板（含现有英文名占位 + 若干空行），方便批量填写
        out = filedialog.asksaveasfilename(
            initialdir=str(model.work_dir),
            initialfile="mapping_template.tsv",
            filetypes=[("TSV files", "*.tsv"), ("CSV files", "*.csv"), ("All files", "*.*")],
            defaultextension=".tsv",
        )
        if not out:
            return
        delim = "\t" if str(out).lower().endswith(".tsv") else ","
        try:
            n = generate_blank_template(
                out, existing_english=list(model.mapping.keys()), blank_rows=10, delimiter=delim
            )
            messagebox.showinfo(
                "模板已生成",
                f"已生成 {n} 行空白模板：\n{out}\n"
                f"（含现有 {len(model.mapping)} 个英文名占位 + 10 个空行）",
                parent=dialog,
            )
            app.helpers.on_log(f"📝 生成映射空白模板: {n} 行 → {Path(out).name}", 'success')
        except Exception as e:
            messagebox.showerror("生成失败", f"生成模板失败：{e}", parent=dialog)
            app.helpers.on_log(f"❌ 生成映射模板失败: {e}", 'error')

    def _suggest_from_files():
        # M-02：扫描工作目录，把尚未映射的文件名词干作为候选英文名建议给用户批量添加。
        # 中文名留空，由用户后续填写（这是「建议」不是「自动应用」，避免误映射）。
        try:
            sug = suggest_mapping_from_dir(
                str(model.work_dir),
                existing_english=list(model.mapping.keys()),
                recursive=False,
                max_items=1000,
            )
        except Exception as e:
            messagebox.showerror("扫描失败", f"扫描工作目录失败：{e}", parent=dialog)
            return
        if not sug:
            messagebox.showinfo(
                "无新建议",
                "工作目录下没有尚未映射的文件名候选。\n"
                "（已映射的英文名会自动跳过；限定符如 _opt/_conf 会被剥离）",
                parent=dialog,
            )
            return

        pick = tk.Toplevel(dialog)
        pick.title(f"📂 从文件名建议（共 {len(sug)} 条候选）")
        pick.geometry(fit_dialog_geometry(pick, 420, 460))
        pick.transient(dialog)
        pick.grab_set()

        ttk.Label(
            pick,
            text="勾选要加入映射表的英文名（中文名留空，稍后填写）：",
            foreground="blue",
        ).pack(anchor=tk.W, padx=10, pady=(10, 4))

        list_frame = ttk.Frame(pick)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        lb_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        lb = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, yscrollcommand=lb_scroll.set,
                        exportselection=False)
        lb_scroll.config(command=lb.yview)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for eng, _ in sug:
            lb.insert(tk.END, eng)
        lb.selection_set(0, tk.END)  # 默认全选

        btn_row = ttk.Frame(pick)
        btn_row.pack(fill=tk.X, padx=10, pady=8)

        def _sel_all():
            lb.selection_set(0, tk.END)

        def _sel_none():
            lb.selection_clear(0, tk.END)

        def _add_picked():
            idxs = list(lb.curselection())
            if not idxs:
                messagebox.showinfo("提示", "请至少勾选一条候选", parent=pick)
                return
            added = 0
            for i in idxs:
                eng = lb.get(i)
                if not eng:
                    continue
                new_iid = tv.insert("", tk.END, values=(eng, ""))
                all_iids.append(new_iid)
                added += 1
            _commit("从文件名添加")
            messagebox.showinfo("已添加", f"已向映射表添加 {added} 条候选（中文名留空）。",
                                parent=pick)
            pick.destroy()

        ttk.Button(btn_row, text="全选", command=_sel_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="清空", command=_sel_none).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="✅ 添加选中", command=_add_picked).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_row, text="取消", command=pick.destroy).pack(side=tk.RIGHT, padx=5)

    ttk.Button(btn_top, text="➕ 添加行", command=_add_row).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_top, text="🗑️ 删除选中行", command=_del_selected).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_top, text="💾 保存到配置文件", command=_save_mapping).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_top, text="📝 生成空白模板", command=_gen_template).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_top, text="📂 从文件名建议", command=_suggest_from_files).pack(side=tk.LEFT, padx=5)

    # ---- M-04：搜索/过滤框（实时按英文名/中文名过滤 treeview）----
    def _apply_filter(*_args):
        # 基于主行清单 all_iids（含被隐藏/新增行），避免清除搜索时无法还原已隐藏行
        kw = search_var.get()
        for iid in all_iids:
            vals = tv.item(iid, "values")
            eng = str(vals[0]).lower() if len(vals) > 0 else ""
            chn = str(vals[1]).lower() if len(vals) > 1 else ""
            if (not kw) or (kw in eng) or (kw in chn):
                tv.reattach(iid, "", tk.END)
            else:
                tv.detach(iid)

    search_frame = ttk.Frame(dialog)
    search_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
    ttk.Label(search_frame, text="🔍 搜索:").pack(side=tk.LEFT)
    search_entry = ttk.Entry(search_frame, textvariable=search_var)
    search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
    search_entry.bind("<KeyRelease>", _apply_filter)
    ttk.Button(search_frame, text="清除", command=lambda: (search_var.set(""), _apply_filter())).pack(side=tk.LEFT)

    tree_frame = ttk.Frame(dialog)
    tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    h_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
    v_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
    tv = ttk.Treeview(
        tree_frame,
        columns=["英文名", "中文名"],
        show="headings",
        selectmode=tk.EXTENDED,
        yscrollcommand=v_scroll.set,
        xscrollcommand=h_scroll.set
    )
    v_scroll.config(command=tv.yview)
    h_scroll.config(command=tv.xview)
    v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
    tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    import ui.ui_theme as _ut; _ut.bind_treeview_hover(tv)
    tv.heading("英文名", text="英文名", command=lambda: _tv_sort_column(tv, "英文名", False))
    tv.heading("中文名", text="中文名", command=lambda: _tv_sort_column(tv, "中文名", False))
    tv.column("英文名", width=280, anchor=tk.W, stretch=True)
    tv.column("中文名", width=280, anchor=tk.W, stretch=True)
    tv.bind("<Double-Button-1>", _on_double_click)

    for eng in sorted(model.mapping.keys()):
        chn = model.mapping[eng]
        all_iids.append(tv.insert("", tk.END, values=(eng, chn)))

    # ---- M-07：逐条撤销/重做（快照式 UndoStack，绑定 _commit）----
    def _undo_available_hint():
        return "撤销功能不可用（utils.undo_stack 加载失败）"

    try:
        from utils.undo_stack import UndoStack
        undo_stack = UndoStack(maxlen=100)

        def _snapshot_rows():
            rows = []
            for iid in all_iids:
                vals = tv.item(iid, "values")
                eng = str(vals[0]) if vals else ""
                chn = str(vals[1]) if len(vals) > 1 else ""
                rows.append((eng, chn))
            return rows

        def _restore_rows(rows):
            for iid in list(tv.get_children("")):
                tv.delete(iid)
            all_iids.clear()
            for eng, chn in rows:
                all_iids.append(tv.insert("", tk.END, values=(eng, chn)))
            _apply_filter()

        def _commit(label):
            undo_stack.push(_snapshot_rows(), label=label)

        def _undo():
            prev = undo_stack.undo()
            if prev is None:
                messagebox.showinfo("撤销", "没有可撤销的操作", parent=dialog)
                return
            _restore_rows(prev)

        def _redo():
            nxt = undo_stack.redo()
            if nxt is None:
                messagebox.showinfo("重做", "没有可重做的操作", parent=dialog)
                return
            _restore_rows(nxt)

        # 以初始映射为基线（不计入可撤销步骤）
        undo_stack.reset(_snapshot_rows())
    except Exception:
        def _undo():
            messagebox.showinfo("撤销", _undo_available_hint(), parent=dialog)
        def _redo():
            messagebox.showinfo("重做", _undo_available_hint(), parent=dialog)

    bottom_frame = ttk.Frame(dialog)
    bottom_frame.pack(fill=tk.X, padx=10, pady=(5, 12))
    ttk.Button(bottom_frame, text="↩ 撤销", command=_undo).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text="↪ 重做", command=_redo).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)


def show_mapping_diff_preview(app, old: dict, new: dict, diff: dict) -> bool:
    """
    M-05：映射表「加载前」Diff 预览弹窗。

    展示新文件相对当前内存映射的三类变更（新增 / 修改 / 删除），让用户确认后再真正 apply。
    返回 True 表示用户点「应用载入」，False 表示「取消」。

    注意：本函数只负责渲染 diff（纯数据），真正的写入由 controller 调用
    model.load_mapping_file 完成；这样即使本弹窗出错，controller 的 try/except 也能
    回退为「直接加载」，保证 📥 加载 按钮永远不会变死（零回归）。
    """
    cont = {"apply": False}
    top = tk.Toplevel(app)
    top.title("📥 映射表加载预览 (Diff)")
    top.geometry(fit_dialog_geometry(top, 580, 480))
    top.transient(app)
    top.grab_set()

    c = diff.get("counts", {})
    ttk.Label(
        top,
        text=(
            f"将载入 {len(new)} 条映射   ·   新增 {c.get('added', 0)} ／ "
            f"修改 {c.get('changed', 0)} ／ 删除 {c.get('removed', 0)} ／ 不变 {c.get('unchanged', 0)}"
        ),
        font=('Arial', 10, 'bold'),
    ).pack(pady=(10, 4))

    txt = tk.Text(top, wrap=tk.WORD, font=('Consolas', 9))
    sb = ttk.Scrollbar(top, orient=tk.VERTICAL, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=4)
    sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=4)

    lines = []
    for eng, chn in diff.get("added", {}).items():
        lines.append(f"+ 新增   {eng}  →  {chn}")
    for eng, pair in diff.get("changed", {}).items():
        old_chn, new_chn = pair
        lines.append(f"~ 修改   {eng}:  {old_chn}  →  {new_chn}")
    for eng, chn in diff.get("removed", {}).items():
        lines.append(f"- 删除   {eng}  →  {chn}")
    if not lines:
        lines.append("（无变更）")
    txt.insert("1.0", "\n".join(lines))
    txt.config(state=tk.DISABLED)

    def _apply():
        cont["apply"] = True
        top.destroy()

    def _cancel():
        cont["apply"] = False
        top.destroy()

    btn_row = ttk.Frame(top)
    btn_row.pack(fill=tk.X, pady=(4, 10))
    ttk.Button(btn_row, text="✅ 应用载入", command=_apply).pack(side=tk.RIGHT, padx=10)
    ttk.Button(btn_row, text="❌ 取消", command=_cancel).pack(side=tk.RIGHT, padx=10)

    top.wait_window(top)
    return cont["apply"]
