#!/usr/bin/env python3
"""
对话框模块 - 路由
保持原 Dialogs 类接口不变，实际实现拆分到各子模块。
"""

from .advanced_tools_dialog import show_advanced_tools_dialog
from .analytics_dialog import export_geometry_csv, show_formula_dialog
from .backup_dialog import show_backup_manager_dialog
from .base import (
    _append_text,
    _clear_text,
    force_cleanup_dialog_temp_dirs,
    friendly_error,
    register_dialog_temp_dir,
    unregister_dialog_temp_dir,
)
from .common import (
    show_environment_dialog,
    show_ext_filter_dialog,
    show_font_size_dialog,
    show_obabel_path_dialog,
    show_recent_dirs_dialog,
)
from .history_dialog import show_history_dialog
from .mapping_dialog import show_mapping_editor_dialog, show_mapping_manager_dialog
from .openbabel_dialog import show_openbabel_dialog
from .psi4_dialog import show_psi4_dialog
from .reaction_dialog import show_reaction_animation_dialog
from .results_dialog import show_results_browser_dialog
from .sync_dialog import show_diff_sync_dialog
from .update_dialog import (
    notify_update_result,
    show_check_failed_dialog,
    show_no_update_dialog,
    show_update_dialog,
)


class Dialogs:
    """保持原接口，所有方法转发到子模块函数"""

    def __init__(self, app, controller):
        self.app = app
        self.controller = controller

    def _get_app(self):
        return self.app

    def _get_controller(self):
        return self.controller

    # ---- 转发所有对话框方法 ----
    def show_ext_filter_dialog(self):
        show_ext_filter_dialog(self.app, self.controller)

    def show_font_size_dialog(self, parent=None):
        show_font_size_dialog(self.app, parent=parent)

    def show_environment_dialog(self, parent=None, ob_details=None, psi4_details=None):
        show_environment_dialog(self.app, parent=parent, ob_details=ob_details, psi4_details=psi4_details)

    def show_obabel_path_dialog(self, parent=None, on_saved_callback=None):
        show_obabel_path_dialog(self.app, parent=parent, on_saved_callback=on_saved_callback)

    def show_recent_dirs_dialog(self):
        show_recent_dirs_dialog(self.app, self.controller)

    def show_psi4_dialog(self):
        show_psi4_dialog(self.app, self.controller)

    def show_openbabel_dialog(self):
        show_openbabel_dialog(self.app, self.controller)

    def show_mapping_manager_dialog(self):
        show_mapping_manager_dialog(self.app, self.controller)

    def show_mapping_editor_dialog(self):
        show_mapping_editor_dialog(self.app, self.controller)

    def show_reaction_animation_dialog(self):
        show_reaction_animation_dialog(self.app, self.controller)

    def show_history_dialog(self):
        show_history_dialog(self.app, self.controller)

    def show_results_browser_dialog(self):
        show_results_browser_dialog(self.app, self.controller)

    def show_diff_sync_dialog(self):
        show_diff_sync_dialog(self.app, self.controller)

    def show_advanced_tools_dialog(self):
        show_advanced_tools_dialog(self.app, self.controller)

    def show_formula_dialog(self):
        show_formula_dialog(self.app, self.controller)

    def export_geometry_csv(self):
        export_geometry_csv(self.app, self.controller)

    def show_backup_manager_dialog(self):
        """F17：备份管理（快照列表 / 预览 / 回滚）。"""
        show_backup_manager_dialog(self.app, self.controller)

    # ---- F18 在线更新检查（T15/T16）----
    def show_update_dialog(self, info, manual: bool = False):
        """发现新版本时的对话框（版本对比 / 更新说明 / 三个按钮）。"""
        return show_update_dialog(self.app, info, manual=manual)

    def show_no_update_dialog(self, current_version=None):
        """手动检查且已是最新版本时的反馈（静默检查不会调用）。"""
        show_no_update_dialog(self.app, current_version)

    def show_check_failed_dialog(self, reason: str = ""):
        """手动检查失败时的反馈（静默检查不会调用）。"""
        show_check_failed_dialog(self.app, reason)

    def notify_update_result(self, info, manual: bool = False):
        """
        F18 唯一的 UI 分发点：静默 / 手动两条路径的差异全收敛在这里。
        view 的 after(2000) 回调与菜单回调都走它，避免规则各写一份而漂移。
        """
        return notify_update_result(self.app, info, manual=manual)

    # ---- 工具方法 ----
    @staticmethod
    def friendly_error(err):
        return friendly_error(err)

    def _append_text(self, widget, text, tag=None, see_end=True):
        _append_text(self.app, widget, text, tag, see_end)

    def _clear_text(self, widget):
        _clear_text(self.app, widget)
