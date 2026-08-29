#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 计算设置对话框
"""
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

from chem.psi4_compute import (
    check_psi4_installed,
    run_psi4_task_cancellable,
)
from utils.constants import PSI4_PRESETS, PSI4_TASKS, RUN_PRESETS
from utils.dialog_geom import fit_dialog_geometry

from .base import _append_text


def _request_cancel(app):
    """U-02：请求取消正在运行的 PSI4 任务（协作式，下次进度上报时安全中止）。"""
    try:
        app.task_manager.request_cancel()
    except Exception:
        pass


def _safe_close(dialog):
    """U-02：安全的关闭逻辑。任务进行中不立即销毁窗口（避免回调写已销毁控件报错），
    而是请求取消并标记「结束后自动关闭」，由 _on_done 负责真正销毁。"""
    st = getattr(dialog, "_psi4_state", None)
    if st and st.get("running"):
        try:
            dialog._app.task_manager.request_cancel()
        except Exception:
            pass
        try:
            st["close_after"] = True
        except Exception:
            pass
        try:
            _append_text(dialog._app, dialog._result_text,
                         "\n⏹ 已请求取消，任务结束后自动关闭窗口…\n", "warn")
        except Exception:
            pass
        return
    try:
        dialog.destroy()
    except Exception:
        pass


def show_psi4_dialog(app, controller):
    selected = app.helpers.get_selected_filenames()
    if not selected and app.fix_mode_var.get() != "scan":
        app.helpers.on_log("⚠️ 请先在文件列表中选择一个或多个文件", 'warning')
        return

    _ok, _msg, _det = check_psi4_installed()
    if not _ok:
        app.helpers.on_log(f"❌ {_msg}", 'error')
        return
    for _w in _det.get("warnings", []):
        app.helpers.on_log(f"⚠️ {_w}", 'warning')
    app.helpers.on_log(f"✅ {_msg}", 'info')

    dialog = tk.Toplevel(app)
    dialog.title("⚡ PSI4 计算设置")
    dialog.geometry(fit_dialog_geometry(dialog, 600, 650))
    dialog.resizable(True, True)
    dialog.transient(app)
    dialog.grab_set()

    ttk.Label(dialog, text=f"已选 {len(selected)} 个文件", font=('Arial', 10, 'bold')).pack(pady=10)

    # 任务类型
    frame1 = ttk.Frame(dialog)
    frame1.pack(pady=5, fill=tk.X, padx=10)
    ttk.Label(frame1, text="任务类型:").pack(side=tk.LEFT, padx=5)
    TASK_DISPLAY_TO_KEY = {v: k for k, v in PSI4_TASKS.items()}
    TASK_KEY_TO_DISPLAY = dict(PSI4_TASKS)
    initial_key = app.psi4_last_task
    if initial_key not in TASK_KEY_TO_DISPLAY:
        initial_key = "energy"
    task_var = tk.StringVar(value=initial_key)
    task_menu_var = tk.StringVar(value=TASK_KEY_TO_DISPLAY[initial_key])
    task_menu = ttk.Combobox(
        frame1,
        textvariable=task_menu_var,
        values=list(TASK_DISPLAY_TO_KEY.keys()),
        state="readonly",
        width=15,
    )
    task_menu.pack(side=tk.LEFT, padx=5)
    task_desc_var = tk.StringVar(value=TASK_KEY_TO_DISPLAY[initial_key])
    ttk.Label(frame1, textvariable=task_desc_var, foreground="gray").pack(side=tk.LEFT, padx=10)

    def _sync_from_display(*_args, **_kwargs):
        disp = task_menu_var.get()
        if disp in TASK_DISPLAY_TO_KEY:
            real_key = TASK_DISPLAY_TO_KEY[disp]
            task_var.set(real_key)
            task_desc_var.set(disp)

    def _sync_from_key(*_args, **_kwargs):
        real_key = task_var.get()
        if real_key in TASK_KEY_TO_DISPLAY:
            disp = TASK_KEY_TO_DISPLAY[real_key]
            task_menu_var.set(disp)
            task_desc_var.set(disp)

    task_menu_var.trace_add("write", _sync_from_display)
    task_menu.bind("<<ComboboxSelected>>", lambda e: _sync_from_display())
    task_var.trace_add("write", _sync_from_key)

    # 运行级别
    frame_runlevel = ttk.Frame(dialog)
    frame_runlevel.pack(pady=5, fill=tk.X, padx=10)
    runlevel_grid = ttk.Frame(frame_runlevel)
    runlevel_grid.pack(fill=tk.X)
    ttk.Label(runlevel_grid, text="🎯 运行级别：").grid(row=0, column=0, padx=5, sticky=tk.W)
    runlevel_var = tk.StringVar(value="")
    runlevel_combo = ttk.Combobox(runlevel_grid, textvariable=runlevel_var, values=list(RUN_PRESETS.keys()), state="readonly", width=40)
    runlevel_combo.grid(row=0, column=1, padx=5, sticky=tk.W)

    ff_hint_label = ttk.Label(frame_runlevel, text="快速模式：会跳过 PSI4，直接使用 MMFF94/UFF 力场优化", foreground="red")
    ff_hint_label.pack_forget()

    # 预设
    frame2 = ttk.Frame(dialog)
    frame2.pack(pady=5, fill=tk.X, padx=10)
    ttk.Label(frame2, text="预设:").pack(side=tk.LEFT, padx=5)
    preset_var = tk.StringVar(value="标准 (B3LYP/6-31G*)")
    preset_combo = ttk.Combobox(frame2, textvariable=preset_var, values=list(PSI4_PRESETS.keys()), state="readonly", width=25)
    preset_combo.pack(side=tk.LEFT, padx=5)
    ttk.Button(frame2, text="应用", command=lambda: _apply_preset(preset_var, method_var, basis_var)).pack(side=tk.LEFT, padx=5)

    # 方法/基组
    frame3 = ttk.Frame(dialog)
    frame3.pack(pady=5, fill=tk.X, padx=10)
    ttk.Label(frame3, text="方法:").pack(side=tk.LEFT, padx=5)
    method_var = tk.StringVar(value=app.psi4_last_method)
    ttk.Entry(frame3, textvariable=method_var, width=12).pack(side=tk.LEFT, padx=5)
    ttk.Label(frame3, text="基组:").pack(side=tk.LEFT, padx=10)
    basis_var = tk.StringVar(value=app.psi4_last_basis)
    ttk.Entry(frame3, textvariable=basis_var, width=12).pack(side=tk.LEFT, padx=5)

    # 扫描参数
    scan_frame = ttk.LabelFrame(dialog, text="扫描参数 (仅扫描任务)", padding="5")

    ttk.Label(scan_frame, text="模式:").pack(side=tk.LEFT, padx=5)
    scan_mode_var = tk.StringVar(value="线性插值（反应物→产物）")
    scan_mode_menu = ttk.Combobox(scan_frame, textvariable=scan_mode_var,
                                  values=["线性插值（反应物→产物）", "刚性扫描（原子对）"],
                                  state="readonly", width=25)
    scan_mode_menu.pack(side=tk.LEFT, padx=5)

    # 反应物列表
    react_frame = ttk.LabelFrame(scan_frame, text="反应物文件 (多选)", padding="3")
    reactant_listbox = tk.Listbox(react_frame, height=3, selectmode=tk.EXTENDED, width=20)
    scroll_r = ttk.Scrollbar(react_frame, orient=tk.VERTICAL, command=reactant_listbox.yview)
    reactant_listbox.configure(yscrollcommand=scroll_r.set)
    reactant_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll_r.pack(side=tk.RIGHT, fill=tk.Y)
    btn_r_add = ttk.Button(react_frame, text="添加", command=lambda: _add_reactant(reactant_listbox, controller))
    btn_r_del = ttk.Button(react_frame, text="删除选中", command=lambda: _del_from_listbox(reactant_listbox))
    btn_r_add.pack(side=tk.LEFT, padx=2)
    btn_r_del.pack(side=tk.LEFT, padx=2)
    react_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

    # 产物列表
    prod_frame = ttk.LabelFrame(scan_frame, text="产物文件 (多选)", padding="3")
    product_listbox = tk.Listbox(prod_frame, height=3, selectmode=tk.EXTENDED, width=20)
    scroll_p = ttk.Scrollbar(prod_frame, orient=tk.VERTICAL, command=product_listbox.yview)
    product_listbox.configure(yscrollcommand=scroll_p.set)
    product_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll_p.pack(side=tk.RIGHT, fill=tk.Y)
    btn_p_add = ttk.Button(prod_frame, text="添加", command=lambda: _add_product(product_listbox, controller))
    btn_p_del = ttk.Button(prod_frame, text="删除选中", command=lambda: _del_from_listbox(product_listbox))
    btn_p_add.pack(side=tk.LEFT, padx=2)
    btn_p_del.pack(side=tk.LEFT, padx=2)
    prod_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

    # 步数
    step_frame = ttk.Frame(scan_frame)
    ttk.Label(step_frame, text="步数:").pack(side=tk.LEFT, padx=5)
    interp_steps_var = tk.StringVar(value="20")
    ttk.Entry(step_frame, textvariable=interp_steps_var, width=6).pack(side=tk.LEFT, padx=5)
    step_frame.pack(side=tk.BOTTOM, pady=5)

    # 刚性扫描参数
    rigid_frame = ttk.Frame(scan_frame)
    ttk.Label(rigid_frame, text="原子对 (如 1-2):").pack(side=tk.LEFT, padx=2)
    scan_atoms_var = tk.StringVar(value="1-2")
    ttk.Entry(rigid_frame, textvariable=scan_atoms_var, width=6).pack(side=tk.LEFT, padx=2)
    ttk.Label(rigid_frame, text="起始(Å):").pack(side=tk.LEFT, padx=2)
    scan_start_var = tk.StringVar(value="1.5")
    ttk.Entry(rigid_frame, textvariable=scan_start_var, width=6).pack(side=tk.LEFT, padx=2)
    ttk.Label(rigid_frame, text="终止(Å):").pack(side=tk.LEFT, padx=2)
    scan_end_var = tk.StringVar(value="4.0")
    ttk.Entry(rigid_frame, textvariable=scan_end_var, width=6).pack(side=tk.LEFT, padx=2)
    rigid_frame.pack_forget()

    def on_mode_change(event):
        if scan_mode_var.get() == "线性插值（反应物→产物）":
            react_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
            prod_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
            rigid_frame.pack_forget()
        else:
            react_frame.pack_forget()
            prod_frame.pack_forget()
            rigid_frame.pack(fill=tk.X, pady=5)
    scan_mode_menu.bind("<<ComboboxSelected>>", on_mode_change)
    on_mode_change(None)

    # 高级选项
    advanced_frame = ttk.LabelFrame(dialog, text="高级选项", padding="5")
    advanced_frame.pack(pady=5, fill=tk.X, padx=10)

    ttk.Label(advanced_frame, text="电荷:").pack(side=tk.LEFT, padx=5)
    charge_var = tk.StringVar(value="0")
    ttk.Entry(advanced_frame, textvariable=charge_var, width=5).pack(side=tk.LEFT, padx=5)
    ttk.Label(advanced_frame, text="多重度:").pack(side=tk.LEFT, padx=10)
    mult_var = tk.StringVar(value="1")
    ttk.Entry(advanced_frame, textvariable=mult_var, width=5).pack(side=tk.LEFT, padx=5)
    ttk.Label(advanced_frame, text="溶剂:").pack(side=tk.LEFT, padx=10)
    solvent_var = tk.StringVar(value="")
    ttk.Combobox(advanced_frame, textvariable=solvent_var, values=["", "water", "ethanol", "methanol", "acetone", "thf"], state="readonly", width=10).pack(side=tk.LEFT, padx=5)
    d3_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(advanced_frame, text="DFT-D3", variable=d3_var).pack(side=tk.LEFT, padx=10)
    ttk.Label(advanced_frame, text="内存(GB):").pack(side=tk.LEFT, padx=10)
    _psi4_cfg = ((getattr(app, "config_data", {}) or {}).get("psi4_config", {}) or {})
    memory_var = tk.IntVar(value=int(_psi4_cfg.get("memory_gb", 4)))
    ttk.Spinbox(advanced_frame, from_=1, to=128, textvariable=memory_var, width=5).pack(side=tk.LEFT, padx=5)

    # 输出目录
    frame4 = ttk.Frame(dialog)
    frame4.pack(pady=5, fill=tk.X, padx=10)
    ttk.Label(frame4, text="输出目录:").pack(side=tk.LEFT, padx=5)
    out_dir_var = tk.StringVar(value="")
    ttk.Entry(frame4, textvariable=out_dir_var, width=30).pack(side=tk.LEFT, padx=5)
    ttk.Button(frame4, text="浏览", command=lambda: app.helpers.browse_dir(out_dir_var)).pack(side=tk.LEFT, padx=5)
    ttk.Label(frame4, text="(留空使用源目录)", foreground="gray").pack(side=tk.LEFT, padx=5)

    # 结果显示
    result_text = scrolledtext.ScrolledText(dialog, height=8, wrap=tk.WORD, font=('Consolas', 9))
    # 科学红线 S-04 / S-05：用红色醒目标签标注溶剂回退 / 热校正失败，绝不静默。
    result_text.tag_configure("warn", foreground="#ff5c5c", font=('Consolas', 9, 'bold'))
    result_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=10)
    start_btn = ttk.Button(btn_frame, text="▶ 开始计算", command=lambda: _run_psi4_batch(
        app, controller, selected, task_var, method_var, basis_var, charge_var, mult_var,
        solvent_var, d3_var, out_dir_var, preset_var, runlevel_var, result_text, dialog,
        scan_frame, scan_mode_var, reactant_listbox, product_listbox,
        interp_steps_var, scan_atoms_var, scan_start_var, scan_end_var,
        memory_var, ff_hint_label
    ))
    start_btn.pack(side=tk.LEFT, padx=10)
    # U-02：对话框内「取消计算」按钮——任务进行中可协作式取消（不再只能靠主窗口按钮）
    cancel_run_btn = ttk.Button(
        btn_frame, text="⏹ 取消计算", state="disabled",
        command=lambda: _request_cancel(app))
    cancel_run_btn.pack(side=tk.LEFT, padx=10)
    # U-02：关闭逻辑安全化——任务进行中先请求取消并等结束后自动关闭，避免销毁控件导致回调报错
    close_btn = ttk.Button(btn_frame, text="关闭", command=lambda: _safe_close(dialog))
    close_btn.pack(side=tk.LEFT, padx=10)
    # 供 _run_psi4_batch 跨函数访问的运行状态与控件引用
    dialog._app = app
    dialog._start_btn = start_btn
    dialog._cancel_run_btn = cancel_run_btn
    dialog._result_text = result_text
    dialog._psi4_state = {"running": False, "close_after": False}

    # 根据任务类型显示扫描参数
    def on_task_var_changed(*_args, **_kwargs):
        if task_var.get() == 'scan':
            scan_frame.pack(pady=5, fill=tk.X, padx=10, before=advanced_frame)
        else:
            scan_frame.pack_forget()

    task_var.trace_add("write", on_task_var_changed)
    on_task_var_changed()

    def on_runlevel_change(event):
        value = runlevel_var.get()
        if not value:
            ff_hint_label.pack_forget()
            return
        preset_info = RUN_PRESETS.get(value)
        if not preset_info:
            return
        task_type = preset_info.get("task_type", "")
        method = preset_info.get("method", "")
        basis = preset_info.get("basis", "")
        preset_name = preset_info.get("preset_name", "")
        solvent = preset_info.get("solvent", None)
        d3 = preset_info.get("d3", False)
        memory_gb = preset_info.get("memory_gb", 4)

        if task_type == "_ff_optimize":
            task_var.set("optimize")
            task_desc_var.set(PSI4_TASKS.get("optimize", ""))
            ff_hint_label.pack(pady=5, padx=5, anchor=tk.W)
        else:
            task_var.set(task_type)
            task_desc_var.set(PSI4_TASKS.get(task_type, ""))
            ff_hint_label.pack_forget()

        method_var.set(method)
        basis_var.set(basis)

        if preset_name in PSI4_PRESETS:
            preset_var.set(preset_name)

        solvent_var.set(solvent if solvent else "")
        d3_var.set(d3)
        memory_var.set(memory_gb)

        if task_var.get() == 'scan':
            scan_frame.pack(pady=5, fill=tk.X, padx=10, before=advanced_frame)
        else:
            scan_frame.pack_forget()

    runlevel_combo.bind("<<ComboboxSelected>>", on_runlevel_change)


def _apply_preset(preset_var, method_var, basis_var):
    preset = preset_var.get()
    if preset in PSI4_PRESETS:
        info = PSI4_PRESETS[preset]
        method_var.set(info.get("method", ""))
        basis_var.set(info.get("basis", ""))


def _add_reactant(listbox, controller):
    files = filedialog.askopenfilenames(
        initialdir=str(controller.model.work_dir),
        filetypes=[("XYZ files", "*.xyz"), ("All files", "*.*")],
    )
    for f in files:
        listbox.insert(tk.END, f)


def _add_product(listbox, controller):
    files = filedialog.askopenfilenames(
        initialdir=str(controller.model.work_dir),
        filetypes=[("XYZ files", "*.xyz"), ("All files", "*.*")],
    )
    for f in files:
        listbox.insert(tk.END, f)


def _del_from_listbox(listbox):
    selected = listbox.curselection()
    for idx in reversed(selected):
        listbox.delete(idx)


def _run_psi4_batch(app, controller, files, task_var, method_var, basis_var, charge_var,
                    mult_var, solvent_var, d3_var, out_dir_var, preset_var, runlevel_var,
                    result_text, dialog, scan_frame, scan_mode_var, reactant_listbox,
                    product_listbox, interp_steps_var, scan_atoms_var, scan_start_var,
                    scan_end_var, memory_var, ff_hint_label):
    from chem.psi4_compute import run_rigid_scan

    # U-02：运行状态管理。开始运行时锁定「开始」按钮、启用「取消计算」；
    # 任务结束（含取消）后复位；若用户在运行中点了「关闭」则自动关闭窗口。
    def _begin_run():
        try:
            dialog._psi4_state["running"] = True
        except Exception:
            pass
        try:
            dialog._start_btn.configure(state="disabled")
        except Exception:
            pass
        try:
            dialog._cancel_run_btn.configure(state="normal")
        except Exception:
            pass

    def _on_done():
        try:
            dialog._psi4_state["running"] = False
        except Exception:
            pass
        try:
            dialog._start_btn.configure(state="normal")
        except Exception:
            pass
        try:
            dialog._cancel_run_btn.configure(state="disabled")
        except Exception:
            pass
        try:
            if dialog._psi4_state.get("close_after"):
                dialog.destroy()
        except Exception:
            pass

    task = task_var.get()
    method = method_var.get().strip()
    basis = basis_var.get().strip()
    charge = int(charge_var.get() or 0)
    mult = int(mult_var.get() or 1)
    solvent = solvent_var.get().strip() or None
    d3 = d3_var.get()
    out_dir = out_dir_var.get().strip() or None
    preset = preset_var.get()
    # memory_var 是 IntVar，str(...) 得到裸数字（如 "4"）；
    # `or "4 GB"` 兜底永远不触发，直接传给 psi4.set_memory 会抛 ValidationError。
    # 统一走归一化函数补全单位。
    from chem.psi4.core import normalize_psi4_memory
    memory = normalize_psi4_memory(memory_var.get())
    # F07 修复引擎可能写入的 SCF 收敛辅助选项，注入到本次计算（run_psi4_task 原生支持 extra_options）
    scf_options = ((getattr(app, "config_data", {}) or {}).get("psi4_config", {}) or {}).get("scf_options", {}) or {}

    if not method or not basis:
        result_text.insert(tk.END, "❌ 方法和基组不能为空\n")
        return

    # 记忆配置（合并而非整体覆盖，保留 F07 修复引擎写入的 scf_options 等字段）
    _psi4_cfg = ((getattr(app, "config_data", {}) or {}).get("psi4_config", {}) or {}).copy()
    _psi4_cfg.update({
        "last_method": method,
        "last_basis": basis,
        "last_task": task,
        "memory_gb": memory_var.get(),
    })
    app.config_data["psi4_config"] = _psi4_cfg
    from utils.config import save_config
    save_config(app.config_data)

    # 扫描任务特殊处理
    if task == 'scan':
        mode = scan_mode_var.get()
        if mode == "线性插值（反应物→产物）":
            reactant_files = list(reactant_listbox.get(0, tk.END))
            product_files = list(product_listbox.get(0, tk.END))
            if not reactant_files or not product_files:
                result_text.insert(tk.END, "❌ 请添加反应物和产物文件\n")
                return
            try:
                steps = int(str(interp_steps_var.get()).strip())
                if steps < 2:
                    raise ValueError
            except (ValueError, TypeError):
                result_text.insert(tk.END, "❌ 步数必须为大于1的整数\n")
                return

            def task_process(**kwargs):
                _append_text(app, result_text, "🔬 开始线性插值扫描\n")
                _append_text(app, result_text, f"   反应物: {len(reactant_files)} 个文件\n")
                _append_text(app, result_text, f"   产物: {len(product_files)} 个文件\n")
                _append_text(app, result_text, f"   步数: {steps}, 方法: {method}, 基组: {basis}\n")

                res = controller.model.run_linear_scan(
                    reactant_files, product_files, steps, method, basis, out_dir,
                    preset, solvent, d3, charge, mult,
                    progress_callback=kwargs.get('_progress_callback')
                )
                _display_scan_result(app, res, result_text)
                controller.scan_files()
                app.after(0, _on_done)

            _begin_run()
            app.helpers.run_task(task_process)
            return

        else:  # 刚性扫描
            if not files:
                result_text.insert(tk.END, "❌ 请选择分子文件\n")
                return
            fname = files[0]
            file_path = Path(controller.model.work_dir) / fname
            try:
                _raw_atoms = str(scan_atoms_var.get()).strip().split('-')
                idx1, idx2 = map(int, _raw_atoms)
                idx1 -= 1
                idx2 -= 1
            except (ValueError, TypeError):
                result_text.insert(tk.END, "❌ 原子对格式错误，请使用如 '1-2'\n")
                return
            try:
                start = float(str(scan_start_var.get()).strip())
                end = float(str(scan_end_var.get()).strip())
                steps = int(str(interp_steps_var.get()).strip())
                if steps <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                result_text.insert(tk.END, "❌ 距离范围或步数格式错误\n")
                return

            def task_process(**kwargs):
                _append_text(app, result_text, f"🔬 开始刚性扫描: {fname}\n")
                _append_text(app, result_text, f"   方法: {method}, 基组: {basis}\n")
                _append_text(app, result_text, f"   原子对: {idx1+1}-{idx2+1}, 距离: {start}~{end} Å, 步数: {steps}\n")

                res = run_rigid_scan(
                    str(file_path), (idx1, idx2), (start, end, steps),
                    method, basis, out_dir, preset, solvent, d3,
                    charge, mult, memory, _progress_callback=kwargs.get('_progress_callback')
                )
                _display_scan_result(app, res, result_text)
                controller.scan_files()
                app.after(0, _on_done)

            _begin_run()
            app.helpers.run_task(task_process)
            return

    # 非扫描任务：批量计算
    total = len(files)
    result_text.insert(tk.END, f"🔬 开始批量计算，共 {total} 个文件\n")
    result_text.insert(tk.END, f"   任务: {task}, 方法: {method}, 基组: {basis}\n")
    if solvent:
        result_text.insert(tk.END, f"   溶剂: {solvent}\n")
    if d3:
        result_text.insert(tk.END, "   DFT-D3 已启用\n")
    result_text.see(tk.END)

    def task_process(**kwargs):
        cancelled_any = False
        for idx, fname in enumerate(files):
            file_path = Path(controller.model.work_dir) / fname
            _append_text(app, result_text, f"\n--- ({idx+1}/{total}) {fname} ---\n")

            try:
                res = run_psi4_task_cancellable(
                    str(file_path), task, method, basis, out_dir, preset,
                    solvent, d3, charge, mult, memory,
                    _progress_callback=kwargs.get('_progress_callback'),
                    cancel_check=app.task_manager.is_cancelled,
                    extra_options=scf_options,
                )
                if res.get("cancelled"):
                    _append_text(app, result_text, "⏹ 该任务已取消\n")
                    app.helpers.on_log(f"⏹ PSI4 计算已取消: {fname}", 'warning')
                    cancelled_any = True
                    break
                def update_result(r=res, fname=fname):
                    if r["success"]:
                        _append_text(app, result_text, "✅ 成功!\n")
                        _append_plain_conclusion(app, result_text, r)
                        if r.get("energy") is not None:
                            _append_text(app, result_text, f"   能量: {r['energy']:.6f} Hartree\n")
                        if r.get("optimized_xyz"):
                            _append_text(app, result_text, "   优化结构已保存\n")
                        if r.get("fchk_file"):
                            _append_text(app, result_text, f"   .fchk: {os.path.basename(r['fchk_file'])}\n")
                        # 科学红线 S-04：PCM 溶剂不可用已静默回退为气相 → 必须醒目告知
                        if r.get("pcm_rolled_back"):
                            _append_text(
                                app, result_text,
                                "   ⚠️ 溶剂模型不可用，已自动回退为气相计算（PCM 未生效）！\n"
                                f"      原因：{r.get('solvent_rollback_reason', '未知')}\n"
                                "      请检查溶剂名拼写 / PSI4 编译是否含 PCM，否则溶剂效应被完全忽略。\n",
                                "warn"
                            )
                        # 科学红线 S-05：热化学校正失败，仅电子能（无热校正）→ 必须醒目告知
                        if r.get("thermo_fallback"):
                            _append_text(
                                app, result_text,
                                f"   ⚠️ 热化学校正失败，该点仅电子能（无热校正），自由能不可靠："
                                f"{', '.join(r['thermo_fallback'])}\n",
                                "warn"
                            )
                        app.helpers.on_log(f"✅ PSI4 计算完成: {fname}", 'success')
                    else:
                        _append_text(app, result_text, f"❌ 失败: {r.get('error', '未知错误')}\n")
                        app.helpers.on_log(f"❌ PSI4 计算失败: {fname}", 'error')
                if threading.current_thread() is threading.main_thread():
                    update_result()
                else:
                    app.after(0, update_result)
            except Exception as e:
                _append_text(app, result_text, f"❌ 异常: {e}\n")
                app.helpers.on_log(f"❌ PSI4 异常: {e}", 'error')

        if cancelled_any:
            _append_text(app, result_text, "\n⏹ 计算已被取消，未完成的任务已停止。\n")
        else:
            _append_text(app, result_text, "\n🎉 所有任务处理完成！\n")
        controller.scan_files()
        app.after(0, _on_done)

    _begin_run()
    app.helpers.run_task(task_process)


def _append_plain_conclusion(app, result_text, res):
    """U-09：把结果 dict 翻译成通俗结论，置顶显示（失败静默，不影响结果展示）。"""
    try:
        from utils.plain_conclusion import conclusion_for
        _append_text(app, result_text, f"💬 通俗结论：{conclusion_for(res)}\n")
    except Exception:
        pass


def _display_scan_result(app, res, result_text):
    if res["success"]:
        _append_text(app, result_text, "✅ 扫描完成!\n")
        _append_plain_conclusion(app, result_text, res)
        _append_text(app, result_text, f"   XYZ动画: {os.path.basename(res.get('xyz_file', ''))}\n")
        if res.get('plot_file'):
            _append_text(app, result_text, f"   能量曲线: {os.path.basename(res['plot_file'])}\n")
        if res.get('ts_file'):
            _append_text(app, result_text, f"   TS初猜: {os.path.basename(res['ts_file'])}\n")
        # 科学红线 S-04：扫描中 PCM 溶剂回退（scans.py 已写入 res["warning"]）必须显式告知
        if res.get("warning"):
            _append_text(app, result_text, f"   ⚠️ {res['warning']}\n", "warn")
        if res.get("pcm_rolled_back"):
            _append_text(
                app, result_text,
                "   ⚠️ 扫描中部分帧 PCM 溶剂不可用，已回退为气相（溶剂效应缺失）！\n",
                "warn"
            )
        app.helpers.on_log("✅ 扫描完成", 'success')
    else:
        _append_text(app, result_text, f"❌ 扫描失败: {res.get('error', '未知错误')}\n")
        app.helpers.on_log("❌ 扫描失败", 'error')
