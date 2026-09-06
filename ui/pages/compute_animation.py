"""🔬 计算与动画页（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import tkinter as tk
from tkinter import ttk

from ui.pages.paned_file_log import _build_paned_file_and_log
from ui.ui_theme import COLORS, dark_card, section_title, themed_button
from ui.ui_builder._theme import CollapsibleFrame, add_tooltip

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
