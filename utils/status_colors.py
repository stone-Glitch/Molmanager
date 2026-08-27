#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U-05 状态列彩色圆点 Tag（纯逻辑层：状态 → 颜色映射）

把 MolManager 文件列表的「状态」字符串映射到语义色（success/warning/
error/muted/info），UI 据此把原来的 emoji 文本渲染成彩色圆点 Tag。

只做「状态 → 色名/色值」的纯映射，不碰 tkinter；色值随 IDE 主题
由 UI 侧最终套用（这里给暗色友好默认值）。
"""


# 语义色名 → 默认 hex（暗色主题友好；UI 可按主题覆盖）
COLOR_HEX: dict[str, str] = {
    "success": "#3fb950",   # 绿
    "warning": "#d29922",   # 橙
    "error": "#f85149",     # 红
    "muted": "#8b949e",     # 灰
    "info": "#4c9aff",      # 蓝
}

# 精确状态 → 色名（对齐 core/model.py scan_files 产出的状态字符串）
STATUS_COLORS: dict[str, str] = {
    "✅ 已正确命名": "success",
    "⏳ 待重命名": "warning",
    "⏳ 纯中文，待修复": "error",
    "❌ 无映射": "error",
    "📄 计算文件": "muted",
}


def status_color(status: str) -> str:
    """
    返回状态对应的语义色名。精确匹配优先，其次按关键词兜底（
    即便状态字符串的 emoji/措辞微调也不至于误判），最终回退 info。
    """
    if not status:
        return "info"
    if status in STATUS_COLORS:
        return STATUS_COLORS[status]
    # 关键词兜底
    if "正确" in status or "成功" in status or "已完成" in status:
        return "success"
    if "失败" in status or "错误" in status or "无映射" in status or "待修复" in status:
        return "error"
    if "待" in status:
        return "warning"
    if "计算文件" in status:
        return "muted"
    return "info"


def status_hex(status: str, palette: dict[str, str] | None = None) -> str:
    """返回状态对应的 hex 色值；palette 可传主题覆盖表。"""
    p = palette or COLOR_HEX
    return p.get(status_color(status), p.get("info", "#4c9aff"))


__all__ = ["COLOR_HEX", "STATUS_COLORS", "status_color", "status_hex"]
