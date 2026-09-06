"""ui.pages：主界面各页面构建模块（自 ui_builder._tabs.py 机械拆分，行为不变）。"""

from ._action_tips import _inject_action_tips
from .advanced_tools import _adv_grid_of_buttons, build_tab_advanced_tools
from .compute_animation import build_tab_compute_and_animation
from .compute_queue import _open_queue_log_drawer, _status_cn, build_tab_compute_queue
from .dashboard import build_tab_dashboard
from .file_management import build_tab_file_management
from .mapping import build_tab_mapping
from .paned_file_log import _build_paned_file_and_log

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
