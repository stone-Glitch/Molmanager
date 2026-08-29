import tkinter as tk
from tkinter import ttk

from ui.ui_theme import (
    COLORS,
)

# ------------------------- 🎨 主题颜色常量 -------------------------
from ._tabs import _inject_action_tips
from ._theme import add_tooltip


def build_status_bar_new(app):
    """
    替换旧 build_status_bar：
    - 左侧：status_var（就绪/处理中）
    - 中左：操作提示 tip_var（上一个按钮做了什么、下一步建议）
    - 右侧：进度条 + 清除日志按钮 + OB 状态指示灯（绿/红圆点，点击看诊断）
    """
    # 字体（问题一：字太小）
    F = getattr(app, "_fonts", {})
    STATUS_F = F.get("STATUS", ("Microsoft YaHei", 11))
    TIP_F = F.get("BASE", ("Microsoft YaHei", 12))
    F.get("BTN2", ("Microsoft YaHei", 12))
    IND_BOLD = F.get("BOLD", ("Microsoft YaHei", 12, "bold"))

    status_frame = tk.Frame(app, bg=COLORS["surface"], bd=0, relief=tk.FLAT)
    status_frame.pack(side=tk.BOTTOM, fill=tk.X)

    app.status_var = getattr(app, "status_var", None) or tk.StringVar(value="就绪")
    status_label = tk.Label(
        status_frame,
        textvariable=app.status_var,
        relief=tk.SUNKEN,
        anchor=tk.W,
        font=STATUS_F,
        bg=COLORS["card_bg"],
        fg=COLORS["text"],
        padx=10,
        pady=4,
    )
    status_label.pack(side=tk.LEFT, fill=tk.X, expand=False, padx=(8, 6), pady=4)
    try:
        status_label.configure(width=28)
    except Exception:
        pass

    # 新增：操作提示 label（「按钮点击后给用户看下一步做什么」）
    app.action_tip_var = tk.StringVar(value="💡 新手推荐：先在左侧工作目录点「浏览」选文件夹 → 点「🔧 一键修复全部」")
    tip_label = tk.Label(
        status_frame,
        textvariable=app.action_tip_var,
        anchor=tk.W,
        font=TIP_F,
        bg=COLORS["surface"],
        fg=COLORS["accent"],
        padx=8,
    )
    tip_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=4)

    # —— 问题三：OpenBabel 指示灯（绿点 = 可用 / 红点 = 不可用，悬停显示摘要，点击 = 环境诊断）——
    app.ob_status_var = tk.StringVar(value="OB: 检测中…")
    app.ob_dot_canvas: tk.Canvas | None = None  # 后面 MainView 写状态会 set 颜色
    ob_frame = tk.Frame(status_frame, bg=COLORS["surface"], bd=0)
    ob_frame.pack(side=tk.RIGHT, padx=(0, 6), pady=4)
    # 圆点画布（18x18，直径 14）
    dot_c = tk.Canvas(ob_frame, width=18, height=18, bg=COLORS["surface"], highlightthickness=0, bd=0, cursor="hand2")
    dot_c.pack(side=tk.LEFT, padx=(0, 4))
    dot_c.create_oval(
        2, 2, 16, 16, fill=COLORS["text_hint"], outline=COLORS["text_hint"], tags="dot"
    )  # 灰色 = 还未检测
    app.ob_dot_canvas = dot_c
    ob_text = tk.Label(
        ob_frame, textvariable=app.ob_status_var, bg=COLORS["surface"], fg=COLORS["text"], font=IND_BOLD, cursor="hand2"
    )
    ob_text.pack(side=tk.LEFT)

    # 点击画布 or 文本 → 打开环境诊断（helpers 里提供该方法）
    def _on_click_ob(_evt=None):
        try:
            if hasattr(app, "helpers") and hasattr(app.helpers, "show_env_diagnosis_dialog"):
                app.helpers.show_env_diagnosis_dialog()
        except Exception as _e:
            try:
                from tkinter import messagebox as _mb

                _mb.showinfo("环境诊断", f"环境诊断调用失败：{_e}")
            except Exception:
                pass

    dot_c.bind("<Button-1>", _on_click_ob)
    ob_text.bind("<Button-1>", _on_click_ob)
    add_tooltip(ob_frame, "OpenBabel 状态（只读指示）：\n  ● 绿色 = 可用\n  ● 红色 = 不可用\n点击查看环境诊断")

    # —— UX1：拖放状态指示灯（绿/红圆点，点击看 tkinterdnd2 依赖说明）——
    def _set_dnd_status(_app):
        _ok = bool(getattr(_app, "dnd_available", False))
        try:
            _app.dnd_status_var.set("🖱️ 拖放就绪" if _ok else "🖱️ 拖放不可用（需 tkinterdnd2）")
            _color = COLORS.get("success", "#3fb950") if _ok else COLORS.get("danger", "#f85149")
            if getattr(_app, "dnd_dot_canvas", None) is not None:
                _app.dnd_dot_canvas.itemconfig("dot", fill=_color, outline=_color)
        except Exception:
            pass

    app.dnd_status_var = tk.StringVar(value="🖱️ 拖放：检测中…")
    dnd_frame = tk.Frame(status_frame, bg=COLORS["surface"], bd=0)
    dnd_frame.pack(side=tk.RIGHT, padx=(0, 6), pady=4)
    dnd_dot = tk.Canvas(
        dnd_frame, width=14, height=14, bg=COLORS["surface"], highlightthickness=0, bd=0, cursor="hand2"
    )
    dnd_dot.pack(side=tk.LEFT, padx=(0, 3))
    dnd_dot.create_oval(1, 1, 13, 13, fill=COLORS["text_hint"], outline=COLORS["text_hint"], tags="dot")
    app.dnd_dot_canvas = dnd_dot
    dnd_text = tk.Label(
        dnd_frame,
        textvariable=app.dnd_status_var,
        bg=COLORS["surface"],
        fg=COLORS["text"],
        font=IND_BOLD,
        cursor="hand2",
    )
    dnd_text.pack(side=tk.LEFT)

    def _on_click_dnd(_evt=None):
        # 点击打开 tkinterdnd2 依赖说明（无论可用与否都可点，便于排查）
        try:
            from tkinter import messagebox as _mb

            _mb.showinfo(
                "拖放导入依赖",
                "拖放导入需要 tkinterdnd2 组件。\n\n"
                "若状态为「不可用」，请在该程序使用的 Python 环境中执行：\n"
                "    pip install tkinterdnd2\n\n"
                "安装后重启程序即可从文件管理器直接拖入文件/文件夹。\n"
                "（也可通过菜单「文件 → 导入」按钮兜底导入，功能不受影响）",
            )
        except Exception:
            pass

    dnd_dot.bind("<Button-1>", _on_click_dnd)
    dnd_text.bind("<Button-1>", _on_click_dnd)
    add_tooltip(
        dnd_frame,
        "拖放导入状态：\n  ● 绿色 = 可用（可直接拖入文件）\n  ● 红色 = 不可用（需 pip install tkinterdnd2，或改用菜单导入）",
    )
    _set_dnd_status(app)

    # 进度条
    app.progress_var = getattr(app, "progress_var", None) or tk.DoubleVar(value=0.0)
    app.progress_bar = ttk.Progressbar(status_frame, variable=app.progress_var, maximum=100, length=220)
    app.progress_bar.pack(side=tk.RIGHT, padx=8, pady=4)

    # —— P1：长任务「取消」按钮（默认隐藏，任务进行中由 helpers 显示）——
    app.cancel_button = ttk.Button(
        status_frame, text="⏹ 取消", command=lambda: getattr(app.task_manager, "request_cancel", lambda: None)()
    )
    # 先 pack 拿到布局参数，再立刻隐藏；helpers 用 set_cancel_visible 重新 pack / pack_forget
    app.cancel_button.pack(side=tk.RIGHT, padx=4, pady=4)
    app.cancel_button.pack_forget()

    # —— UX5：结果浏览器常驻入口（点击查看最新计算结果，避免入口埋在菜单深层）——
    def _open_results():
        try:
            if hasattr(app, "controller") and hasattr(app.controller, "show_results_browser_dialog"):
                app.controller.show_results_browser_dialog()
        except Exception as _e:
            try:
                from tkinter import messagebox as _mb

                _mb.showerror("结果浏览", f"打开结果浏览器失败：{_e}")
            except Exception:
                pass

    ttk.Button(
        status_frame,
        text="📂 结果",
        command=_open_results,
    ).pack(side=tk.RIGHT, padx=(0, 4), pady=4)

    ttk.Button(
        status_frame,
        text="清除日志",
        command=app.helpers.clear_log,
    ).pack(side=tk.RIGHT, padx=(0, 8), pady=4)

    # —— 便捷：把常用按钮的动作提示写出来（通过 monkey-patch helpers.on_log 很危险，不如在几个常用函数包一层）——
    _inject_action_tips(app)
