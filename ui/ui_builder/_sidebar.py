import tkinter as tk

from ui.ui_theme import (
    COLORS,
)


# ------------------------- 🎨 主题颜色常量 -------------------------

def build_sidebar(app, body):
    """左侧图标导航栏：文件管理 / 计算与动画 / 高级工具 / 任务队列（取代原顶部 Notebook 标签）。"""
    F = getattr(app, "_fonts", {})
    NAV = (("🏠", "工作台"), ("📁", "文件管理"), ("🧬", "分子映射"), ("🔬", "计算与动画"), ("⚙️", "高级工具"), ("📊", "任务队列"))
    nav = tk.Frame(body, bg=COLORS["surface"], relief=tk.FLAT, bd=0,
                   highlightbackground=COLORS["border"], highlightthickness=1)
    nav.grid(row=0, column=0, sticky="ns")
    nav.grid_rowconfigure(len(NAV), weight=1)  # 底部留白，导航项顶部对齐
    app._nav_btns = []
    app._nav_indicators = []

    def _go(i):
        app._show_page(i)

    for i, (icon, label) in enumerate(NAV):
        # 每个导航项外包一个 cell：左侧 accent 强调条（激活时显现）+ 按钮
        cell = tk.Frame(nav, bg=COLORS["surface"], bd=0, relief=tk.FLAT, highlightthickness=0)
        cell.grid(row=i, column=0, sticky="ew", padx=6, pady=3)
        ind = tk.Frame(cell, width=0, bg=COLORS["accent"], bd=0, relief=tk.FLAT, highlightthickness=0)
        ind.grid(row=0, column=0, sticky="ns")
        b = tk.Button(cell, text=f"{icon}  {label}", command=lambda i=i: _go(i),
                      bg=COLORS["surface"], fg=COLORS["text_secondary"],
                      activebackground=COLORS["border"], activeforeground=COLORS["accent"],
                      relief=tk.FLAT, bd=0, anchor="w",
                      font=F.get("BOLD", ("Microsoft YaHei", 13, "bold")),
                      cursor="hand2", padx=20, pady=12,
                      highlightthickness=1, highlightbackground=COLORS["border"])
        b.grid(row=0, column=1, sticky="ew")
        cell.grid_columnconfigure(1, weight=1)
        app._nav_btns.append(b)
        app._nav_indicators.append(ind)

    def _update_nav(i):
        for j, (b, ind) in enumerate(zip(app._nav_btns, app._nav_indicators)):
            if j == i:
                b.config(fg=COLORS["accent"], bg=COLORS["elevated"],
                         highlightbackground=COLORS["accent"], highlightthickness=2)
                ind.config(width=4)
            else:
                b.config(fg=COLORS["text_secondary"], bg=COLORS["surface"],
                         highlightbackground=COLORS["border"], highlightthickness=1)
                ind.config(width=0)
    app._update_nav = _update_nav
    _update_nav(0)
    return nav
