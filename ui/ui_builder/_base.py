"""

UI 构建器 - 大字体、扁平卡片风格，无 Canvas 装饰

- 顶部保留 Aurora 辅助类（AuroraTheme / apply_aurora_theme / AuroraGradientCanvas /
  make_aurora_card / ToolTip / add_tooltip），供 dialogs.py 等复用

- 底部主界面 build_ui 系列函数：纯 tk.Frame + ttk，零 Canvas 嵌套，稳定显示

"""


# ------------------------- 🎨 主题颜色常量 -------------------------
