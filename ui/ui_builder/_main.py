import tkinter as tk

from ui.ui_theme import (
    COLORS,
)

# ------------------------- 🎨 主题颜色常量 -------------------------
from ._menu import build_menu_bar
from ._sidebar import build_sidebar
from ._statusbar import build_status_bar_new
from ui.pages import (
    build_tab_advanced_tools,
    build_tab_compute_and_animation,
    build_tab_compute_queue,
    build_tab_dashboard,
    build_tab_file_management,
    build_tab_mapping,
)
from ._theme import _make_scrolled_frame, apply_aurora_theme_if_available, resolve_font_specs
from ._toolbar import build_toolbar


def build_ui(app):
    """
    构建新版主界面：
      - 顶部全局工具栏（工作目录/最近目录/扫描/撤销重做/进度条）
      - 中部 ttk.Notebook 三标签页（📁 文件管理 / 🔬 计算与动画 / ⚙️ 高级工具）
      - 底部状态栏（状态文字 + 进度条 + 操作提示）
    **零功能损失**：所有旧变量 app.work_dir_entry / app.tree / app.log_text / app.fix_mode_var
    等名称完全保留，controller.py 与 dialogs.py 保持不改动。

    ===== 问题一（字太小）修复 =====
    - 在任何控件创建前先 resolve_font_specs，把字体基线写到 app._fonts 和 app.option_add。
    - 之后所有显式创建的 Label / Button / Entry / Combobox / Treeview / Notebook 页签 / 日志 / 状态栏 都用统一字体。
    - apply_aurora_theme 再把 ttk 控件样式改成同一套字体。
    """
    # === 字太小：Step 1. 先算字体基线 ===
    try:
        resolve_font_specs(app)
    except Exception as _e:
        # 字体计算失败不影响主流程，走系统默认
        import traceback as _tb

        print("[ui_builder] resolve_font_specs failed:", _tb.format_exc())  # noqa: T201
    apply_aurora_theme_if_available(app)

    # —— 双主题：先据持久化偏好设定当前主题，再应用（覆盖 aurora 的 Aurora.* 样式）——
    try:
        import ui.ui_theme as ui_theme

        ui_theme.set_current_theme(ui_theme.load_theme_preference())
        ui_theme.apply_theme(app, ui_theme.get_current_theme())
    except Exception as _te:
        import traceback as _tb

        print("[ui_builder] apply_theme failed:", _tb.format_exc())  # noqa: T201

    # —— 设计落地 Phase 5：把 run_task→task_manager.submit 的所有后台任务接入统一任务队列 ——
    # 仅包装 submit（实例属性），不改动既有逻辑；on_task_done/error 负责把活动任务标记成功/失败。
    try:
        import time as _tm

        _tm_mgr = app.task_manager
        _orig_submit = _tm_mgr.submit

        def _spec_from_config(a):
            try:
                m = getattr(a, "psi4_last_method", "") or ""
                b = getattr(a, "psi4_last_basis", "") or ""
                if m or b:
                    return ("%s/%s" % (m, b)).strip("/") or "—"
            except Exception:
                pass
            return "—"

        def _wrap_submit(func, *args, progress_callback=None, **kwargs):
            job = {
                "id": len(_tm_mgr.jobs) + 1,
                "name": "任务 #%d" % (len(_tm_mgr.jobs) + 1),
                "kind": "后台任务",
                "spec": _spec_from_config(app),
                "status": "running",
                "progress": 0,
                "started": _tm.time(),
                "finished": None,
                "log": [],
                "error": "",
            }
            with _tm_mgr._jobs_lock:
                _tm_mgr.jobs.append(job)
                _tm_mgr._active_job = job
            _orig_pcb = progress_callback

            def _pc(percent, msg=""):
                try:
                    if percent is not None:
                        job["progress"] = int(percent)
                    if msg:
                        job["log"].append(msg)
                except Exception:
                    pass
                if callable(_orig_pcb):
                    try:
                        _orig_pcb(percent, msg)
                    except Exception:
                        pass

            return _orig_submit(func, *args, progress_callback=_pc, job=job, **kwargs)

        _tm_mgr.submit = _wrap_submit
    except Exception as _se:
        import traceback as _tb

        print("[ui_builder] 任务队列接入失败（已跳过，不影响其余功能）:", _tb.format_exc())  # noqa: T201

    # —— 0. 顶部菜单栏（自绘 Menubutton，平台无关；字体完全可控）——
    try:
        build_menu_bar(app)
    except Exception as _me:
        import traceback as _tb

        print("[ui_builder] build_menu_bar failed:", _tb.format_exc())  # noqa: T201

    main = tk.Frame(app, bg=COLORS["bg"])
    main.pack(fill=tk.BOTH, expand=True)
    main.grid_rowconfigure(0, weight=0)  # toolbar
    main.grid_rowconfigure(1, weight=1)  # notebook （拉伸占满）
    main.grid_rowconfigure(2, weight=0)  # status bar
    main.grid_columnconfigure(0, weight=1)

    app.configure(bg=COLORS["bg"])

    # —— 1. 顶部工具栏 ——
    build_toolbar(app, main)

    # —— 2. 主体：左侧导航 + 右侧内容区（取代原顶部 Notebook）——
    body = tk.Frame(main, bg=COLORS["bg"])
    body.grid(row=1, column=0, sticky="nsew")
    body.grid_rowconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=1)

    # 右侧内容容器：三页（每页包进双向滚动框，避免小窗口裁切）
    content = tk.Frame(body, bg=COLORS["bg"])
    content.grid(row=0, column=1, sticky="nsew")
    content.grid_rowconfigure(0, weight=1)
    content.grid_columnconfigure(0, weight=1)

    app._pages = []
    for _builder in (
        build_tab_dashboard,
        build_tab_file_management,
        build_tab_mapping,
        build_tab_compute_and_animation,
        build_tab_advanced_tools,
        build_tab_compute_queue,
    ):
        _sf, _inner = _make_scrolled_frame(content, COLORS["bg"])
        _sf.grid(row=0, column=0, sticky="nsew")
        _builder(app, _inner)
        app._pages.append(_sf)

    # 页面切换（侧边栏导航调用）
    def _show_page(i):
        app._cur_page = i  # 记录当前页，供任务队列轮询节流
        for _idx, _pf in enumerate(app._pages):
            if _idx == i:
                _pf.grid(row=0, column=0, sticky="nsew")
            else:
                _pf.grid_remove()
        try:
            app._update_nav(i)
        except Exception:
            pass
        # 切到队列页时立即刷新一次
        try:
            if i == 5 and hasattr(app, "refresh_queue"):
                app.refresh_queue()
        except Exception:
            pass
        # 切到工作台时刷新统计
        try:
            if i == 0 and hasattr(app, "refresh_dashboard"):
                app.refresh_dashboard()
        except Exception:
            pass
        # 切到分子映射页时刷新条目列表
        try:
            if i == 2 and hasattr(app, "refresh_mapping"):
                app.refresh_mapping()
        except Exception:
            pass

    app._show_page = _show_page

    # 兼容旧代码（view._on_ctrl_f / 计算页「跳转到文件管理」按钮）对 main_notebook.select 的调用
    class _NavShim:
        def __init__(self, a):
            self._a = a

        def select(self, idx):
            try:
                self._a._show_page(idx)
            except Exception:
                pass

    app.main_notebook = _NavShim(app)

    # 左侧导航栏（在 _show_page / _update_nav 就绪后构建）
    build_sidebar(app, body)
    app._show_page(0)

    # —— 3. 底部状态栏（替换原来的 build_status_bar，增加「操作提示」） ——
    build_status_bar_new(app)

    # —— 兼容旧 apply_filter：UI 上已删除 status/ext 下拉，默认都为 "全部" ——
    for _attr, _default in (("filter_status_var", "全部"), ("filter_ext_var", "全部")):
        v = getattr(app, _attr, None)
        if v is None:
            setattr(app, _attr, tk.StringVar(value=_default))
        else:
            try:
                v.set(_default)
            except Exception:
                pass

    # —— 关键词过滤：<KeyRelease> 实时刷新 ——
    try:
        app.filter_keyword_entry.bind("<KeyRelease>", lambda e: app.helpers.apply_filter())
    except Exception:
        pass
