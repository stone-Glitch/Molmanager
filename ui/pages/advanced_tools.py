"""⚙️ 高级工具页：子 Notebook 4 页（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

import tkinter as tk
from tkinter import ttk

from ui.ui_builder._theme import add_tooltip
from ui.ui_theme import COLORS, dark_card, themed_button

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
