"""兼容 shim：本模块已拆分为 ``ui.pages`` 各页面模块，此处仅做纯转发。

保持 ``ui_builder/__init__.py`` 与 ``_statusbar.py`` 的既有 import 零改动；
新代码请直接 ``from ui.pages import ...``。
"""

from ui.pages._action_tips import _inject_action_tips
from ui.pages.advanced_tools import _adv_grid_of_buttons, build_tab_advanced_tools
from ui.pages.compute_animation import build_tab_compute_and_animation
from ui.pages.compute_queue import (
    _open_queue_log_drawer,
    _status_cn,
    build_tab_compute_queue,
)
from ui.pages.dashboard import build_tab_dashboard
from ui.pages.file_management import build_tab_file_management
from ui.pages.mapping import build_tab_mapping
from ui.pages.paned_file_log import _build_paned_file_and_log

__all__ = [
    "_adv_grid_of_buttons",
    "_build_paned_file_and_log",
    "_inject_action_tips",
    "_open_queue_log_drawer",
    "_status_cn",
    "build_tab_advanced_tools",
    "build_tab_compute_and_animation",
    "build_tab_compute_queue",
    "build_tab_dashboard",
    "build_tab_file_management",
    "build_tab_mapping",
]
