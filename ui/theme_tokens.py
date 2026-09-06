#!/usr/bin/env python3
"""
Aurora Frost 设计令牌（Design Tokens）· 几何/间距层
═══════════════════════════════════════════════════

与**颜色无关**的几何常量统一定义在此，供深/浅两套主题共享：
间距、圆角、描边、控件尺寸。颜色仍以 ``ui/ui_theme.py`` 的
DARK/LIGHT 调色板为唯一真相源——本模块**禁止**出现任何 hex。

用法::

    from ui.theme_tokens import SPACING, RADIUS, STROKE
    ttk.Button(parent, text="确定").pack(padx=SPACING["md"], pady=SPACING["sm"])

为什么要 token 化：
  1. 精修视觉时改一处生效全局，避免「这里 8 那里 10」的漂移；
  2. 深/浅主题切换只换颜色不换几何，两层解耦互不干扰；
  3. 语义命名（sm/md/lg）让 code review 一眼看出布局意图。

tkinter 诚实约束（见架构文档）：
  ttk 原生控件**没有真圆角**——RADIUS 仅对自绘 Canvas 控件生效
  （dark_card / AuroraGradientCanvas / 侧边栏指示条 / 浮动批量条等）。
  原生控件的层级感由「间距 + 扁平化 + 1px 描边」表达。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 间距
#: 间距刻度（px）。全 UI 的 padx/pady 应从这里取值，禁止魔法数字。
#: 刻度节奏：4 的倍数，与主流设计系统（Material/HIG）对齐。
SPACING: dict[str, int] = {
    "xs": 4,    # 图标↔文字、紧凑行内
    "sm": 8,    # 控件组内间距、按钮内边距
    "md": 12,   # 卡片内边距、表单行距
    "lg": 16,   # 卡片间距离、分区块
    "xl": 24,   # 页面级留白、章节间隔
    "xxl": 32,  # 大分区（极少用，如欢迎页）
}

#: 页面主标题与其内容区的垂直间隔（章节呼吸感）
SECTION_GAP: int = 24

# ---------------------------------------------------------------- 圆角
#: 圆角半径（px）。⚠️ 仅自绘 Canvas 控件可用（tkinter 原生控件无真圆角）。
RADIUS: dict[str, int] = {
    "sm": 6,    # 小按钮、输入框、标签胶囊
    "md": 10,   # 卡片、面板
    "lg": 14,   # 浮动批量条、大对话框圆角
}

# ---------------------------------------------------------------- 描边
#: 描边宽度（px）。
STROKE: dict[str, int] = {
    "hair": 1,    # 分隔线、卡片边框（最常用）
    "strong": 2,  # 强调框、聚焦态
}

# ---------------------------------------------------------------- 控件尺寸
#: 控件高度基准（px）。保证侧边栏/工具栏/状态栏的行高节奏一致。
ROW_H: int = 28          # 文件树/列表行高感
CONTROL_H: int = 34      # 标准按钮/输入框高度
NAV_ITEM_H: int = 44     # 侧边栏导航项高度
STATUSBAR_H: int = 30    # 状态栏高度
TOOLBAR_H: int = 48      # 工具栏高度

# ---------------------------------------------------------------- 侧边栏
#: 侧边栏几何
SIDEBAR_WIDTH: int = 180
NAV_INDICATOR_W: int = 3   # 选中态左侧强调指示条宽度

__all__ = [
    "SPACING",
    "SECTION_GAP",
    "RADIUS",
    "STROKE",
    "ROW_H",
    "CONTROL_H",
    "NAV_ITEM_H",
    "STATUSBAR_H",
    "TOOLBAR_H",
    "SIDEBAR_WIDTH",
    "NAV_INDICATOR_W",
]
