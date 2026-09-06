"""ui.ui_builder：新版主界面装配包。

本包与 ``ui.pages`` 存在双向依赖（pages 需要 ``._theme`` 的控件工具，
子模块链又经 ``._main``/``._statusbar`` 回到 pages），因此 re-export 一律
**按需加载（PEP 562 模块级 ``__getattr__``）**，避免包初始化互锁：
先加载本包 → 子模块链 → ui.pages → ``from ui.ui_builder._theme import ...``
时，本 ``__init__`` 已瞬时执行完毕，仅剩子模块加载，链条闭合。

符号 → 源子模块 映射保持与原 eager import 完全一致。
"""

_EXPORTS = {
    # ._main
    "build_ui": "._main",
    # ._menu
    "_open_ob_path_dialog": "._menu",
    "_persist_preview_toggle": "._menu",
    "_safe_call": "._menu",
    "_show_about": "._menu",
    "build_menu_bar": "._menu",
    # ._sidebar
    "build_sidebar": "._sidebar",
    # ._statusbar
    "build_status_bar_new": "._statusbar",
    # ._tabs（兼容 shim，实际实现已迁往 ui.pages）
    "_adv_grid_of_buttons": "._tabs",
    "_build_paned_file_and_log": "._tabs",
    "_inject_action_tips": "._tabs",
    "_open_queue_log_drawer": "._tabs",
    "_status_cn": "._tabs",
    "build_tab_advanced_tools": "._tabs",
    "build_tab_compute_and_animation": "._tabs",
    "build_tab_compute_queue": "._tabs",
    "build_tab_dashboard": "._tabs",
    "build_tab_file_management": "._tabs",
    "build_tab_mapping": "._tabs",
    # ._theme
    "AuroraGradientCanvas": "._theme",
    "AuroraTheme": "._theme",
    "CollapsibleFrame": "._theme",
    "ToolTip": "._theme",
    "_make_scrolled_frame": "._theme",
    "_toggle_theme": "._theme",
    "add_tooltip": "._theme",
    "apply_aurora_theme": "._theme",
    "apply_aurora_theme_if_available": "._theme",
    "make_aurora_card": "._theme",
    "resolve_font_specs": "._theme",
    # ._toolbar
    "build_toolbar": "._toolbar",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    source = _EXPORTS.get(name)
    if source is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(source, __name__), name)
    globals()[name] = value  # 首次访问后缓存为模块属性
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
