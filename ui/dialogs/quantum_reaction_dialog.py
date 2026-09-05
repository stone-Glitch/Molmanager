#!/usr/bin/env python3
"""量子反应能计算对话框（Quantum Reaction，融合自独立项目 Quantum Reaction Visualizer）。

功能（对应原网页版，桌面化为 Tkinter）：
- 预设 8 个经典反应 / 自定义 SMILES 反应（支持 ``O=O:3`` 多重度语法）
- Psi4 优化 + 频率热化学 → ΔE / ΔE₀ / ΔH° / ΔG°（298.15 K、1 bar）
- Kabsch 插值轨迹 → IQmol 兼容多帧 XYZ + MP4
- 可选逐帧单点能量 → 能量曲线（matplotlib Agg 渲染 PNG 内嵌，零 TkAgg 依赖）
- 计算跑在 TaskManager 工作线程（进度上报 + 协作式取消，与全局任务队列一致）

契约：
- import 本模块不依赖 psi4/rdkit；打开对话框时做依赖预检，缺失给出可操作提示；
- psi4 计算内部有全局锁与缓存（chem.quantum_reaction.quantum），重复分子秒回。
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from utils.dialog_geom import fit_dialog_geometry

try:  # matplotlib 仅用于结果曲线，缺失时隐藏曲线区
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    MPL_AVAILABLE = True
except Exception:  # pragma: no cover - 环境差异分支
    plt = None
    MPL_AVAILABLE = False

from ui.ui_theme import COLORS

# 计算方法 → (psi4 method, basis)
METHOD_CHOICES = [
    ("HF/STO-3G（最快）", "hf", "sto-3g"),
    ("HF/6-31G*", "hf", "6-31g*"),
    ("B3LYP/6-31G*", "b3lyp", "6-31g*"),
    ("MP2/6-31G*", "mp2", "6-31g*"),
]


def _psi4_ok() -> bool:
    try:
        from chem.quantum_reaction import psi4_available

        return psi4_available()
    except Exception:
        return False


def _runs_root(app) -> Path:
    """runs 输出根目录：工作目录/quantum_runs，工作目录不可用回退用户文档。"""
    try:
        wd = Path(app.controller.model.work_dir)
        if str(wd) and wd.is_dir():
            return wd / "quantum_runs"
    except Exception:
        pass
    home = Path(os.path.expanduser("~"))
    docs = home / "Documents" if (home / "Documents").exists() else home
    return docs / "MolManager" / "quantum_runs"


def _open_in_os(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", path])  # noqa: S603,S607


def show_quantum_reaction_dialog(app, controller=None) -> None:
    dlg = tk.Toplevel(app)
    dlg.title("⚗️  量子反应能计算（Psi4 · ΔE/ΔH°/ΔG° · 轨迹动画）")
    dlg.configure(bg=COLORS["bg"])
    dlg.geometry(fit_dialog_geometry(dlg, 860, 720, min_w=760, min_h=620))
    dlg.minsize(760, 620)
    dlg.resizable(True, True)
    dlg.transient(app)
    dlg.grab_set()

    F = getattr(app, "_fonts", {})
    TITLE_F = F.get("H2", ("Microsoft YaHei", 15, "bold"))
    BASE_F = F.get("BASE", ("Microsoft YaHei", 12))
    SMALL_F = F.get("SMALL", ("Microsoft YaHei", 11))
    MONO_F = F.get("MONO", ("Consolas", 10))

    # ---------- 顶部：标题 + 依赖预检 ----------
    tk.Label(dlg, text="⚗️  量子反应能计算", bg=COLORS["bg"], fg=COLORS["text"], font=TITLE_F, anchor="w").pack(
        fill=tk.X, padx=18, pady=(14, 2)
    )
    tk.Label(
        dlg,
        text="选择预设反应或输入 SMILES（如 O=O:3 表示三线态 O₂）→ Psi4 优化 + 频率热化学 → "
        "ΔE / ΔE₀ / ΔH° / ΔG°、能量曲线与 IQmol 轨迹动画。",
        bg=COLORS["bg"],
        fg=COLORS["text_secondary"],
        font=SMALL_F,
        anchor="w",
        wraplength=800,
        justify="left",
    ).pack(fill=tk.X, padx=18, pady=(0, 8))

    if not _psi4_ok():
        warn = tk.Frame(dlg, bg=COLORS.get("warning", "#F2B75C"))
        warn.pack(fill=tk.X, padx=18, pady=(0, 8))
        tk.Label(
            warn,
            text="⚠ 未检测到 psi4：计算需要先安装（conda install -c conda-forge psi4）。"
            "本环境为 mol_manager_312 时可用 conda activate mol_manager_312 后安装。",
            bg=COLORS.get("warning", "#F2B75C"),
            fg="#1A2142",
            font=SMALL_F,
            wraplength=780,
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, padx=10, pady=6)

    # ---------- 主体 ----------
    body = tk.Frame(dlg, bg=COLORS["bg"])
    body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 8))
    body.grid_columnconfigure(0, weight=1)

    r = 0

    def _row():
        nonlocal r
        fr = tk.Frame(body, bg=COLORS["bg"])
        fr.grid(row=r, column=0, sticky="ew", pady=3)
        r += 1
        return fr

    # 预设反应
    fr = _row()
    tk.Label(fr, text="预设反应:", bg=COLORS["bg"], fg=COLORS["text"], font=BASE_F).pack(side=tk.LEFT)
    preset_var = tk.StringVar()
    from chem.quantum_reaction import list_reactions

    presets = list_reactions()
    preset_map = {f"{p['name']}（{p['equation']}）": p["id"] for p in presets}
    cb = ttk.Combobox(fr, textvariable=preset_var, values=list(preset_map.keys()), state="readonly", width=46)
    cb.pack(side=tk.LEFT, padx=8)
    cb.set(list(preset_map.keys())[0])

    # 自定义反应
    fr = _row()
    tk.Label(fr, text="或 自定义:", bg=COLORS["bg"], fg=COLORS["text"], font=BASE_F).pack(side=tk.LEFT)
    tk.Label(
        fr,
        text="反应物 / 产物各一栏，逗号分隔 SMILES 或分子名；多重度用 :N（如 O=O:3、[O]:2）",
        bg=COLORS["bg"],
        fg=COLORS["text_hint"],
        font=SMALL_F,
    ).pack(side=tk.LEFT, padx=6)
    custom_r_var = tk.StringVar()
    custom_p_var = tk.StringVar()
    fr2 = _row()
    tk.Label(fr2, text="反应物:", bg=COLORS["bg"], fg=COLORS["text"], font=BASE_F).pack(side=tk.LEFT)
    ttk.Entry(fr2, textvariable=custom_r_var, font=MONO_F, width=40).pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
    fr3 = _row()
    tk.Label(fr3, text="产　物:", bg=COLORS["bg"], fg=COLORS["text"], font=BASE_F).pack(side=tk.LEFT)
    ttk.Entry(fr3, textvariable=custom_p_var, font=MONO_F, width=40).pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

    # 方法 / 帧数 / 选项
    fr = _row()
    tk.Label(fr, text="计算方法:", bg=COLORS["bg"], fg=COLORS["text"], font=BASE_F).pack(side=tk.LEFT)
    method_var = tk.StringVar(value=METHOD_CHOICES[0][0])
    ttk.Combobox(fr, textvariable=method_var, values=[c[0] for c in METHOD_CHOICES], state="readonly", width=20).pack(
        side=tk.LEFT, padx=6
    )
    tk.Label(fr, text="帧数:", bg=COLORS["bg"], fg=COLORS["text"], font=BASE_F).pack(side=tk.LEFT, padx=(14, 0))
    frames_var = tk.IntVar(value=15)
    ttk.Spinbox(fr, from_=4, to=40, increment=1, textvariable=frames_var, width=6).pack(side=tk.LEFT, padx=6)

    fr = _row()
    thermo_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        fr,
        text="计算热化学 ΔE₀/ΔH°/ΔG°（频率分析，失败自动降级仅 ΔE）",
        variable=thermo_var,
    ).pack(side=tk.LEFT)
    traj_e_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(fr, text="逐帧能量曲线（≤8 原子，计算量 ×N）", variable=traj_e_var).pack(side=tk.LEFT, padx=(16, 0))

    # 运行 + 进度
    fr = _row()
    run_btn = ttk.Button(fr, text="⚗️  开始计算", style="Aurora.Primary.TButton")
    run_btn.pack(side=tk.LEFT)
    open_dir_btn = ttk.Button(fr, text="📁 结果目录", state=tk.DISABLED)
    open_dir_btn.pack(side=tk.LEFT, padx=8)
    progress = ttk.Progressbar(fr, mode="determinate", maximum=100, length=280)
    progress.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
    stage_var = tk.StringVar(value="就绪")
    tk.Label(fr, textvariable=stage_var, bg=COLORS["bg"], fg=COLORS["text_hint"], font=SMALL_F).pack(
        side=tk.LEFT, padx=6
    )

    # 日志
    fr = _row()
    fr.grid_rowconfigure(0, weight=1)
    log_txt = tk.Text(
        fr,
        height=10,
        bg=COLORS["input"],
        fg=COLORS["text"],
        font=MONO_F,
        relief=tk.SOLID,
        bd=1,
        highlightbackground=COLORS["card_border"],
        state=tk.DISABLED,
    )
    log_txt.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(fr, command=log_txt.yview)
    sb.grid(row=0, column=1, sticky="ns")
    log_txt.configure(yscrollcommand=sb.set)
    from ui.dialogs.base import _append_text

    # 结果区
    result_frame = tk.Frame(
        body,
        bg=COLORS["card_bg"],
        bd=1,
        relief=tk.SOLID,
        highlightbackground=COLORS["card_border"],
        highlightthickness=1,
    )
    result_frame.grid(row=r, column=0, sticky="ew", pady=(8, 0))
    r += 1
    result_frame.grid_columnconfigure(0, weight=1)
    curve_label: list = []  # [(Label, PhotoImage)] 保持引用防 GC

    last_run_dir: list = []

    def _on_open_dir():
        if last_run_dir:
            try:
                _open_in_os(last_run_dir[0])
            except Exception as e:
                messagebox.showerror("打开失败", f"无法打开目录:\n{e}", parent=dlg)

    open_dir_btn.configure(command=_on_open_dir)

    # ---------- 计算线程 ----------
    running = {"flag": False}

    def _run():
        if running["flag"]:
            return
        if not _psi4_ok():
            messagebox.showwarning(
                "缺少 psi4",
                "本机未检测到 psi4，无法进行量子化学计算。\n\n"
                "安装：conda install -c conda-forge psi4\n"
                '（libint 需 2.9.0：conda install -c conda-forge "libint=2.9.0"）',
                parent=dlg,
            )
            return
        payload: dict = {}
        use_custom = bool(custom_r_var.get().strip() or custom_p_var.get().strip())
        if use_custom:
            if not (custom_r_var.get().strip() and custom_p_var.get().strip()):
                messagebox.showwarning("信息不完整", "自定义反应需要同时填写反应物与产物。", parent=dlg)
                return
            payload["custom"] = {
                "reactants": [t for t in custom_r_var.get().split(",") if t.strip()],
                "products": [t for t in custom_p_var.get().split(",") if t.strip()],
            }
        else:
            rid = preset_map.get(preset_var.get())
            if not rid:
                messagebox.showwarning("请选择", "请先选择一个预设反应，或填写自定义反应物/产物。", parent=dlg)
                return
            payload["reaction_id"] = rid
        mi = (
            [c[0] for c in METHOD_CHOICES].index(method_var.get())
            if method_var.get() in [c[0] for c in METHOD_CHOICES]
            else 0
        )
        payload["method"] = METHOD_CHOICES[mi][1]
        payload["basis"] = METHOD_CHOICES[mi][2]
        payload["n_frames"] = int(frames_var.get())
        payload["do_thermo"] = bool(thermo_var.get())
        payload["do_traj_energy"] = bool(traj_e_var.get())

        run_id = "r" + datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = _runs_root(app) / run_id
        last_run_dir.clear()
        last_run_dir.append(str(run_dir))
        open_dir_btn.configure(state=tk.NORMAL)

        running["flag"] = True
        run_btn.configure(state=tk.DISABLED)
        progress.configure(value=2)
        stage_var.set("准备中…")

        def _task(progress_callback=None):
            def _log(msg):
                app.after(0, lambda m=str(msg): _append_text(app, log_txt, m + "\n"))

            def _stage(name, p):
                app.after(0, lambda n=name, pp=p: (progress.configure(value=max(2, int(pp * 100))), stage_var.set(n)))

            def _cancel():
                try:
                    return bool(app.task_manager.is_cancelled())
                except Exception:
                    return False

            try:
                from chem.quantum_reaction import run_reaction

                result = run_reaction(
                    payload,
                    run_dir=run_dir,
                    on_log=_log,
                    on_stage=_stage,
                    should_cancel=_cancel,
                )
            except Exception as e:
                app.after(0, lambda err=e: _finish_error(err))
                return
            app.after(0, lambda res=result: _finish_ok(res))

        def _restore():
            running["flag"] = False
            run_btn.configure(state=tk.NORMAL)

        def _finish_error(err):
            _restore()
            progress.configure(value=0)
            stage_var.set("失败")
            from ui.dialogs.base import show_friendly_error

            show_friendly_error(app, err, parent=dlg, title="计算失败")

        def _finish_ok(result):
            _restore()
            progress.configure(value=100)
            stage_var.set(f"完成（{result.get('elapsed_s', '?')}s）")
            _show_result(result)

        app.helpers.run_task(_task)

    run_btn.configure(command=_run)

    # ---------- 结果展示 ----------
    def _show_result(result):
        for w in result_frame.winfo_children():
            w.destroy()
        thermo = result.get("thermo") or {}
        de = result.get("delta_e_kjmol")
        direction = "放热" if (de is not None and de < 0) else "吸热"
        dcolor = COLORS.get("success", "#3FB950") if de is not None and de < 0 else COLORS.get("danger", "#F85149")

        head = tk.Frame(result_frame, bg=COLORS["card_bg"])
        head.pack(fill=tk.X, padx=14, pady=(10, 4))
        tk.Label(
            head,
            text=f"ΔE = {de:.2f} kJ/mol（{direction}）" if de is not None else "ΔE 未知",
            bg=COLORS["card_bg"],
            fg=dcolor,
            font=("Microsoft YaHei", 16, "bold"),
        ).pack(side=tk.LEFT)

        info = tk.Frame(result_frame, bg=COLORS["card_bg"])
        info.pack(fill=tk.X, padx=14, pady=(0, 6))
        lines = [
            f"方法: {result.get('method')}/{result.get('basis')}    "
            f"原子数: {result.get('n_atoms')}    帧数: {result.get('n_frames')}    "
            f"耗时: {result.get('elapsed_s')}s",
        ]
        if thermo:
            spont = "标准态自发" if thermo.get("delta_g_kjmol", 0) < 0 else "标准态非自发"
            lines.append(
                f"ΔE₀(含零点) = {thermo['delta_e0_kjmol']:.2f}    "
                f"ΔH°(298K) = {thermo['delta_h_kjmol']:.2f}    "
                f"ΔG°(298K) = {thermo['delta_g_kjmol']:.2f} kJ/mol（{spont}）"
            )
        elif thermo_var.get():
            lines.append("ΔE₀/ΔH°/ΔG°：未计算（某分子频率分析失败，已降级为仅 ΔE）")
        tk.Label(
            info,
            text="\n".join(lines),
            bg=COLORS["card_bg"],
            fg=COLORS["text_secondary"],
            font=SMALL_F,
            anchor="w",
            justify="left",
        ).pack(fill=tk.X)

        btns = tk.Frame(result_frame, bg=COLORS["card_bg"])
        btns.pack(fill=tk.X, padx=14, pady=(0, 8))
        run_dir = result.get("run_dir")

        def _add(text, cmd):
            ttk.Button(btns, text=text, command=cmd).pack(side=tk.LEFT, padx=(0, 6))

        _add("📂 打开结果目录", lambda: _open_in_os(run_dir))
        _add("🧬 trajectory.xyz（IQmol 播放）", lambda: _open_in_os(result["trajectory_xyz"]))
        if result.get("mp4"):
            _add("🎬 播放 MP4", lambda: _open_in_os(result["mp4"]))
        _add("✅ IQmol 兼容性校验", lambda: _run_iqmol_check(result["trajectory_xyz"], parent=dlg))

        # 能量曲线（Agg 渲染 PNG → tk.PhotoImage，零 TkAgg 依赖）
        if MPL_AVAILABLE:
            try:
                curve = result.get("energy_curve") or {}
                eh = [e for e in (curve.get("energies_eh") or []) if e == e]
                if len(eh) >= 2:
                    fig = plt.Figure(figsize=(5.2, 2.6), dpi=100)
                    ax = fig.add_subplot(111)
                    kj = [e * 2625.499638 for e in eh]
                    ax.plot(range(1, len(kj) + 1), kj, marker="o", color="#3B6EFF", linewidth=1.8)
                    ax.set_xlabel("frame")
                    ax.set_ylabel("E (kJ/mol)")
                    ax.grid(alpha=0.3)
                    fig.tight_layout()
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png")
                    buf.seek(0)
                    photo = tk.PhotoImage(data=base64.b64encode(buf.read()).decode("ascii"))
                    lbl = tk.Label(result_frame, image=photo, bg=COLORS["card_bg"])
                    lbl.pack(padx=14, pady=(0, 12))
                    curve_label.append((lbl, photo))
            except Exception:
                pass

    def _run_iqmol_check(xyz_path, parent=None):
        try:
            from chem.quantum_reaction.iqmol_check import parse_like_iqmol

            frames = parse_like_iqmol(xyz_path)
            messagebox.showinfo(
                "IQmol 兼容性校验",
                f"✓ 通过：{len(frames)} 帧全部完整解析。\n"
                f"前 3 帧能量: " + ", ".join((f"{e:.6f}" if e is not None else "(无)") for _, e, _, _ in frames[:3]),
                parent=parent,
            )
        except Exception as e:
            messagebox.showerror("校验失败", f"该文件不符合 IQmol 解析规则:\n{e}", parent=parent)

    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.after(0, lambda: cb.focus_set())
