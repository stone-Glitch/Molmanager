#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主窗口 - 整合所有组件
"""

import sys
import tkinter as tk
from tkinter import ttk

from utils.logger import default_logger as logger
from utils.config import load_config, save_config, CONFIG_FILE
from core.task_manager import TaskManager
from core.controller import Controller
from ui.dialogs import Dialogs
from ui.app_helpers import AppHelpers
from ui.ui_builder import build_ui, apply_aurora_theme as _apply_aurora_theme  # noqa: F401
from ui.wizard import maybe_show_first_run_wizard  # 首次使用向导
# _apply_aurora_theme 不再调用（新版清爽扁平 UI 统一用 LabelFrame + ttk 原生样式，
# 不再依赖 Aurora Frost 的 Canvas / 粒子装饰），但保留导入避免旧插件/脚本误用。
# 如需启用旧版主题，可在 build_ui() 之前手工调用 _apply_aurora_theme(self)。

# ============================================================================
# F06 拖放导入：tkinterdnd2 是**可选依赖**（架构 §3.2）
# ----------------------------------------------------------------------------
# 设计要点（三条硬约束）：
#   1. 探测放在模块级、包在 try 里 —— 没装 tkinterdnd2 时程序必须照常启动，
#      仅仅是"拖不进来"，不能有任何报错/弹窗（静默降级）。
#   2. mixin 顺序必须是 (DnDWrapper, tk.Tk)：DnDWrapper 只提供
#      drop_target_register / dnd_bind 等方法且**没有** __init__，
#      放前面才能保证这些方法不被 tk.Tk 的同名属性遮挡，
#      同时 super().__init__() 仍会顺着 MRO 落到 tk.Tk.__init__。
#   3. tkdnd 的 Tcl 运行库要等 Tk 根创建之后才能 _require()，
#      所以这里只做 import 探测，真正加载在 MainView._init_dnd_runtime()。
# ============================================================================
try:
    from tkinterdnd2 import TkinterDnD as _TkinterDnD, DND_FILES as _DND_FILES
    _DND_BASES: tuple = (_TkinterDnD.DnDWrapper, tk.Tk)
    DND_IMPORT_OK: bool = True
    _DND_IMPORT_ERROR: str = ""
except Exception as _dnd_imp_err:  # noqa: BLE001 - 缺依赖是预期情况，不是错误
    _TkinterDnD = None
    _DND_FILES = "DND_Files"          # 常量字面量，缺包时也不会 NameError
    _DND_BASES = (tk.Tk,)
    DND_IMPORT_OK = False
    _DND_IMPORT_ERROR = str(_dnd_imp_err)


def _clamp_geometry_to_screen(root, geom: str, min_w: int = 960, min_h: int = 680) -> str:
    """
    把「上次保存的窗口几何」钳制回当前屏幕内。

    为什么必须做：用户可能上次在外接大屏（如 1920x1080）用过，配置里存下
    "1920x1005+-207+-30"；换回笔记本小屏（如 1536x864）再打开时：
      - 窗口比屏幕大 → 右侧/底部内容永久看不到；
      - y 为负 → **标题栏跑到屏幕上方之外，用户无法用鼠标拖动窗口**，只能改配置文件自救。
    原先只判断「窗口中心点是否落在屏幕内」，上面这种几何中心点仍在屏内，检查会放行，
    所以必须改成对 宽/高/x/y 全部做边界钳制。
    """
    import re as _re
    try:
        m = _re.match(r"^\s*(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?\s*$", str(geom))
        if not m:
            return geom
        w, h = int(m.group(1)), int(m.group(2))
        has_pos = m.group(3) is not None
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        if sw <= 0 or sh <= 0:
            return geom
        # 给任务栏 + 窗口标题栏留出余量，避免"贴满屏幕反而看不到底部按钮"
        RESERVED_H = 80
        w = max(1, min(w, max(min_w, sw - 20)))
        h = max(1, min(h, max(min_h, sh - RESERVED_H)))
        if not has_pos:
            return f"{w}x{h}"
        x, y = int(m.group(3)), int(m.group(4))
        # x/y 钳到 [0, 屏幕尺寸-窗口尺寸]，保证标题栏一定可见、窗口一定在屏内
        x = max(0, min(x, max(0, sw - w)))
        y = max(0, min(y, max(0, sh - h - 40)))
        return f"{w}x{h}+{x}+{y}"
    except Exception:
        return geom


class MainView(*_DND_BASES):
    """
    主窗口。

    基类由上面的 `_DND_BASES` 动态决定：
      - 装了 tkinterdnd2  → ``(TkinterDnD.DnDWrapper, tk.Tk)``，支持拖放导入；
      - 没装             → ``(tk.Tk,)``，功能完全一致，只是拖不进文件。
    两种情况下 `super().__init__()` 都会落到 ``tk.Tk.__init__``（DnDWrapper 无 __init__）。
    """

    def __init__(self):
        try:
            super().__init__()
            self.config_data = load_config()
            # tkdnd 运行库必须在 Tk 根建立之后加载；失败即静默降级
            self._init_dnd_runtime()
            self.title("🫧  分子与计算文件管理器 ｜ Aurora Frost")
            _saved_geom = self.config_data.get("window_geometry", "1100x780")
            _safe_geom = _clamp_geometry_to_screen(self, _saved_geom)
            if _safe_geom != _saved_geom:
                logger.info("窗口几何 %s 超出当前屏幕，已自动修正为 %s", _saved_geom, _safe_geom)
            self.geometry(_safe_geom)
            self.minsize(960, 680)

            # ---- 修复字体模糊：**强制**启用高DPI支持 (Windows) ----
            # 问题一补充：SetProcessDPIAware 可能失败，尝试 Win10+ 的 Per-Monitor V2；
            # 即便失败也继续走 fallback，不中断启动。
            try:
                if sys.platform == 'win32':
                    import ctypes
                    try:
                        # 优先：Per-Monitor V2（Win10 1703+），应对不同屏幕不同 DPI
                        ctypes.windll.shcore.SetProcessDpiAwareness(2)
                    except (OSError, AttributeError, ValueError):
                        try:
                            ctypes.windll.shcore.SetProcessDpiAwareness(1)
                        except (OSError, AttributeError, ValueError):
                            ctypes.windll.user32.SetProcessDPIAware()
            except (OSError, AttributeError) as e:
                logger.debug("启用高DPI支持失败（非致命，字体仍按配置放大）: %s", e)

            # ---- 问题一：全局字体基线（来自 config.font_size + font_follow_dpi）----
            # 计算逻辑交给 ui_builder.resolve_font_specs，保证 MainView 与子控件使用同一套规则。
            # 这里显式调用一次，把 app._fonts 和 option_add 先准备好，后续 build_ui 内会再调用一次
            # （第二次调用命中的是同一 config，结果一致，幂等。）
            try:
                from ui.ui_builder import resolve_font_specs as _resolve_fonts
                _F = _resolve_fonts(self)
                _default_font = _F["BASE"]
            except Exception as _fe:
                logger.debug("从 config 计算字体基线失败，使用默认字体: %s", _fe)
                _default_font = ('Microsoft YaHei UI', 12) if sys.platform == 'win32' else ('Arial', 12)

            # tk.call('tk', 'scaling') 也要调大，保证 ttk 控件的内边距/图标也随之变大
            try:
                # 目标 pt 值：_default_font[1]。win32 默认 96DPI 下，tk scaling 点/英寸≈1.0 对应约 9pt；
                # 按比例换算：我们的默认 14pt → scaling ≈ 14/9 ≈ 1.56
                pt = int(_default_font[1]) if len(_default_font) > 1 else 12
                _s = max(1.1, min(2.2, pt / 9.0))
                self.tk.call('tk', 'scaling', _s)
            except (tk.TclError, ValueError, OSError) as e:
                logger.debug("tk scaling 设置失败，使用默认: %s", e)

            self.option_add('*Font', _default_font)
            self.option_add('*Dialog.msg.font', _default_font)
            self.option_add('*Menu.Font',    _default_font)
            self.option_add('*Button.Font',  _default_font)
            self.option_add('*Label.Font',   _default_font)
            self.option_add('*Entry.Font',   _default_font)
            self.option_add('*Text.Font',    _default_font)

            # 全局 ttk 样式 + 字体（使用当前平台默认 clam 主题 + 默认 background/fieldbackground，
            # 不强制 Aurora Frost 的 Canvas/粒子美学，保持清爽扁平风格）
            try:
                _s = ttk.Style(self)
                try:
                    _s.theme_use("clam")
                except tk.TclError:
                    pass
                _s.configure('.', font=_default_font)
            except Exception:
                pass
            style = ttk.Style(self)
            style.configure('.', font=_default_font)

            # 核心组件（顺序很重要）
            self.task_manager = TaskManager(self)
            self.task_manager.start()

            # 1. 先创建 AppHelpers
            self.helpers = AppHelpers(self)

            # 2. 再创建 Controller（传入 helpers）
            self.controller = Controller(self, self.helpers)

            # 3. 最后创建 Dialogs
            self.dialogs = Dialogs(self, self.controller)

            # 变量
            self.work_dir_var = tk.StringVar(value=str(self.controller.model.work_dir))
            self.mapping_file_var = tk.StringVar(value=self.config_data.get("mapping_file", ""))
            self.ext_filter_var = tk.StringVar(value=self.config_data.get("ext_filter", ".mol,.xyz,.fchk,.out,.inp"))
            self.mapping_count = tk.StringVar(value="未加载")
            self.current_files = []
            self.progress_var = tk.DoubleVar(value=0.0)
            self.last_scan_result = []
            self.filter_keyword_var = tk.StringVar(value="")
            self.filter_status_var = tk.StringVar(value="全部")
            self.filter_ext_var = tk.StringVar(value="全部")
            self.filter_count_var = tk.StringVar(value="共 0 / 0 个")
            # 设置-菜单栏：是否整理前先预览（从 config 载入）
            try:
                _prev_default = bool(self.config_data.get("preview_before_operation", True))
            except Exception:
                _prev_default = True
            self.preview_before_operation_var = tk.BooleanVar(value=_prev_default)

            # PSI4 配置记忆
            psi4_cfg = self.config_data.get("psi4_config", {})
            self.psi4_last_method = psi4_cfg.get("last_method", "b3lyp")
            self.psi4_last_basis = psi4_cfg.get("last_basis", "6-31g*")
            self.psi4_last_task = psi4_cfg.get("last_task", "energy")

            # 构建界面（清爽扁平布局，稳定无 Canvas 嵌套）
            build_ui(self)

            # F06：控件建好之后再注册拖放目标（tree 等此刻才存在）
            self._setup_drag_and_drop()

            # ---- 启动后强制刷新布局三板斧（无 Aurora Canvas，纯 Frame/LabelFrame 布局）----
            try:
                self.update_idletasks()
            except Exception:
                pass
            try:
                self.geometry(self.geometry())   # 强制 <Configure>，让 paned/tree/log 完成权重分配
            except Exception:
                pass
            try:
                self.after(50, lambda: self.update_idletasks())
            except Exception:
                pass
            try:
                self.after(200, lambda: self.update_idletasks())
            except Exception:
                pass

            # 把 GUI 日志面板挂到根 logger（必须在 build_ui 之后，因为 log_text 此时存在）
            from utils.logger import attach_gui_handler
            attach_gui_handler(lambda: self)

            # —— 问题二：日志空白修复。在 GUI handler 挂载后，立刻输出 2 条 welcome banner，
            # 再回放 setup_logging → 此刻之间的日志（attach_gui_handler 内部已回放），
            # 保证用户第一次打开程序永远能在日志面板看到内容。
            try:
                _wd = str(self.work_dir_var.get() or "(未设置工作目录)")
                logger.success("✅ 欢迎使用 分子管理器！工作目录：%s", _wd)
                logger.info(
                    "💡 新手路径：① 左上「浏览…」选择工作目录 → ② 点「🔧 一键修复全部」 → ③ 点「📂 按类型整理」归档"
                )
                logger.info(
                    "💡 查看依赖状态：右下状态栏有 OB 指示灯，点击可一键诊断/手动设置 OpenBabel 路径。"
                )
            except Exception:
                pass

            # —— 问题三：首次环境检查（300ms 后台跑，不卡界面）——
            # 写完欢迎日志后，延迟调用 helpers.check_environment()，
            # 该方法会填状态栏的 OB 指示灯颜色和文字（绿/红），如果 OB 不可用会弹诊断。
            def _env_check_and_apply_status():
                try:
                    fn = getattr(self.helpers, "check_environment", None)
                    if callable(fn):
                        fn(announce_missing=False)
                except Exception as _env_e:
                    try:
                        logger.debug("环境检查调用失败（非致命）：%s", _env_e)
                    except Exception:
                        pass
            self.after(350, _env_check_and_apply_status)

            # 如果 OB 严重不可用（用户连 Python 包都没装），在 800ms 后主动弹「环境设置」对话框，
            # 引导用户安装或手动选择路径（不阻塞，用户可以关了继续用基础功能）。
            def _maybe_pop_env_dialog():
                try:
                    fn = getattr(self.helpers, "maybe_prompt_environment_on_first_run", None)
                    if callable(fn):
                        fn()
                except Exception:
                    pass
            self.after(800, _maybe_pop_env_dialog)

            # -------- 延迟初始化：让主窗口先完整渲染，再在 300ms 后加载映射 + 扫描 --------
            # 这样能显著降低「点击 exe → 看到可用界面」的感知时间，避免文件列表空白带来的
            # 「启动慢」心理感受；而且映射/扫描本身都是后台任务，只是把 submit 的时机后延。
            def _delayed_init():
                try:
                    if self.mapping_file_var.get():
                        self.controller.load_mapping_file()
                except Exception as _init_e:
                    try:
                        logger.debug("延迟加载映射失败（非致命）: %s", _init_e)
                    except Exception:
                        pass
                try:
                    self.controller.scan_files()
                except Exception as _init_e:
                    try:
                        logger.warning("延迟扫描提交失败: %s", _init_e)
                    except Exception:
                        pass

            self.after(300, _delayed_init)

            # -------------------- F18（T16）：启动 2 秒后静默检查更新 --------------------
            # 「永不打扰」三条铁律（架构 §3.4）：
            #   ① 网络请求全在后台线程，绝不阻塞 UI；
            #   ② 断网 / 超时 / 限流 / 未配置更新源 → 一律静默，无弹窗无红色日志；
            #   ③ 只有真的检测到新版本才弹一次对话框，且用户可「跳过此版本」永久闭嘴。
            try:
                self.after(2000, self._schedule_silent_update_check)
            except Exception as _upd_e:
                logger.debug("排期静默更新检查失败（非致命）: %s", _upd_e)

            # -------------------- 首次使用向导（非阻塞，仅当 config_data["first_run"] 不是 False 时弹） --------------------
            maybe_show_first_run_wizard(self)

            # -------- 启动后把窗口带到顶层（修复 splash 关闭后主窗口被其他应用遮挡 / 屏幕外 / 最小化 问题）--------
            # ⚠️ 重要：不能在 __init__ 里直接 lift() —— 此时窗口尚未 map（WM 还没绘制），
            #   lift/focus_force 都会被吞掉；必须用 after(50, …) 在下一轮事件循环里做，
            #   同时用「瞬时 topmost=True → 100ms 后 topmost=False」的技巧，保证不管 splash
            #   还是其他应用窗口在前面，主窗口一定能被用户第一眼看到。
            def _bring_to_front():
                try:
                    self.deiconify()
                except Exception:
                    pass
                try:
                    self.update_idletasks()
                except Exception:
                    pass
                try:
                    # 先强制 topmost（跨平台最可靠的置顶手段），然后 100ms 后再取消，
                    # 避免用户后续操作中窗口永远顶在最前
                    self.attributes("-topmost", True)
                except Exception:
                    pass
                try:
                    self.lift()
                except Exception:
                    pass
                try:
                    self.focus_force()
                except Exception:
                    pass
                try:
                    self.focus_set()
                except Exception:
                    pass

                def _undo_topmost():
                    try:
                        self.attributes("-topmost", False)
                    except Exception:
                        pass
                try:
                    self.after(100, _undo_topmost)
                except Exception:
                    # after 不可用的话立刻直接取消（退化到一次性 lift）
                    _undo_topmost()

                # 多显示器/负坐标兼容：如果窗口几何中心不在任一屏幕范围内，强制居中
                try:
                    import re as _re
                    geom = self.geometry()  # 格式 "WxH+X+Y"
                    m = _re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geom)
                    if m:
                        w, h, x, y = (int(x) for x in m.groups())
                        sw = self.winfo_screenwidth()
                        sh = self.winfo_screenheight()
                        cx, cy = x + w // 2, y + h // 2
                        if not (0 <= cx < sw and 0 <= cy < sh):
                            nx = max(0, (sw - w) // 2)
                            ny = max(0, (sh - h) // 2)
                            self.geometry(f"{w}x{h}+{nx}+{ny}")
                except Exception:
                    pass

            try:
                self.after(50, _bring_to_front)
            except Exception:
                # after 不可用：退化到同步执行
                try:
                    _bring_to_front()
                except Exception:
                    pass

            # -------- 全局快捷键绑定（易用性改进）--------
            # 注意：F1 帮助里列出的每一个键都必须真实绑定在这里，
            # 否则用户按了没反应会直接怀疑「这软件是不是坏了」。
            self.bind("<Control-g>", lambda e: self._on_ctrl_g())
            self.bind("<Control-s>", lambda e: self._on_ctrl_s())
            self.bind("<F1>", lambda e: self._show_help())
            self.bind("<Control-z>", self._on_ctrl_z)
            self.bind("<Control-y>", self._on_ctrl_y)
            self.bind("<Control-Shift-Z>", self._on_ctrl_y)   # 另一种常见的「重做」习惯
            self.bind("<F5>", self._on_f5)
            self.bind("<Control-f>", self._on_ctrl_f)
            # F15：日志过滤关键词框（Ctrl+F 已被文件列表搜索占用，这里必须避让）
            self.bind("<Control-Shift-F>", self._on_ctrl_shift_f)
            # 命令面板（设计落地 Phase 1）：Ctrl/Cmd+K 全局唤起
            from ui.command_palette import open_command_palette as _open_cmd_palette
            self.bind("<Control-k>", lambda e: _open_cmd_palette(self))
            self.bind("<Command-k>", lambda e: _open_cmd_palette(self))

            self.protocol("WM_DELETE_WINDOW", self.on_close)
        except Exception as e:
            import traceback as _tb
            # 把完整堆栈先打进日志，这样即使用户看不到弹窗也能在日志文件里查
            logger.error("MainView 初始化失败: %s", e)
            logger.error("堆栈:\n%s", _tb.format_exc())
            # 关键：如果 super().__init__() 已经执行成功（也就是 Tk 根已经创建），
            # 那么此时半初始化的 MainView 仍然是一个活的 Tk 窗口，如果不 destroy，
            # 它会作为「看不见的主窗口」留在 Tk 解释器里，让后续 messagebox.showerror
            # 选它做父窗口，导致错误对话框也看不见。
            try:
                # 用 Tkinter 的标准方式判断 tk 解释器是否还活着
                if bool(getattr(self, 'tk', None)):
                    try:
                        self.destroy()
                    except Exception:
                        pass
            except Exception:
                pass
            # 再原样抛出去，让 main.py 的 load_main 捕获，弹出 showerror 友好提示
            raise

    # ===================== F06：拖放导入（T18） =====================
    def _init_dnd_runtime(self) -> None:
        """
        加载 tkdnd 的 Tcl 运行库（必须在 Tk 根创建之后）。

        ⚠️ 契约：**任何失败都只降级不报错**。`self.dnd_available` 是唯一的开关，
        后续所有拖放相关代码都先看它，缺依赖时整条链路彻底静默。
        """
        self.dnd_available = False
        self.TkdndVersion = None
        if not DND_IMPORT_OK or _TkinterDnD is None:
            # 审计 UX1 修复：原仅 debug 级别，用户拖入文件无反应却毫无提示。
            # 提升到 info 并明确给出安装命令，便于排查。
            logger.info(
                "拖放导入不可用：未安装 tkinterdnd2（其余功能不受影响）。"
                "如需拖放导入，请在该 Python 环境执行 `pip install tkinterdnd2` 后重启程序。"
            )
            self._refresh_dnd_status_bar()
            return
        try:
            # _require() 会 `package require tkdnd`，把 Tcl 侧扩展挂到本解释器上。
            # 用私有函数是 tkinterdnd2 官方推荐的「mixin 到自有 Tk 子类」写法。
            self.TkdndVersion = _TkinterDnD._require(self)
            self.dnd_available = True
            logger.debug("tkdnd 运行库已加载，版本 %s", self.TkdndVersion)
            self._refresh_dnd_status_bar()
        except Exception as exc:  # noqa: BLE001
            self.dnd_available = False
            logger.debug("tkdnd 运行库加载失败，拖放导入已静默禁用: %s", exc)
            self._refresh_dnd_status_bar()

    def _setup_drag_and_drop(self) -> None:
        """
        把主窗口与文件列表注册成拖放目标。

        注册多个控件是因为 tkdnd 按「鼠标下的控件」派发事件：只注册根窗口时，
        用户拖到 Treeview 上方松手会没反应 —— 而那恰恰是最自然的落点。
        """
        if not getattr(self, "dnd_available", False):
            return
        try:
            from core.drop_handler import DropHandler
            if not DropHandler.is_enabled(getattr(self, "config_data", {})):
                logger.debug("配置 dnd.enabled = false，跳过拖放目标注册")
                return
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取拖放开关失败，按默认启用处理: %s", exc)

        widgets = [self]
        for attr in ("tree", "main_notebook", "log_text"):
            w = getattr(self, attr, None)
            if w is not None:
                widgets.append(w)

        registered = 0
        for w in widgets:
            try:
                w.drop_target_register(_DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_dnd_drop)
                registered += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("注册拖放目标失败（已跳过该控件）: %s", exc)
        if registered:
            logger.info("🖱️ 拖放导入已就绪：把文件或文件夹直接拖进窗口即可导入")
        else:
            self.dnd_available = False
            logger.debug("没有任何控件注册成功，拖放导入已禁用")
        self._refresh_dnd_status_bar()

    def _on_dnd_drop(self, event):
        """
        ``<<Drop>>`` 事件回调：只做「取数据 + 转交 controller」，不做任何业务判断。

        路径解析（含带空格路径的花括号包裹）、白名单、目录展开、受保护目录拒绝
        全部在 `core.drop_handler` 里，这样菜单兜底导入才能复用同一套规则。
        """
        try:
            data = getattr(event, "data", "") or ""
            self.controller.handle_dropped_paths(data, source="drop")
        except Exception as exc:  # noqa: BLE001
            logger.warning("处理拖放事件失败: %s", exc)
            try:
                self.helpers.on_log(f"❌ 处理拖入文件失败: {exc}", "error")
            except Exception:
                pass
        # tkdnd 约定：回调应返回本次采用的动作（通常是 event.action）
        return getattr(event, "action", None)

    def _refresh_dnd_status_bar(self) -> None:
        """UX1：根据 dnd_available 同步状态栏拖放指示灯（状态栏未构建时自动跳过）。"""
        if not hasattr(self, "dnd_status_var") or not hasattr(self, "dnd_dot_canvas"):
            return
        try:
            ok = bool(getattr(self, "dnd_available", False))
            self.dnd_status_var.set("🖱️ 拖放就绪" if ok else "🖱️ 拖放不可用（需 tkinterdnd2）")
            color = "#3fb950" if ok else "#f85149"
            self.dnd_dot_canvas.itemconfig("dot", fill=color, outline=color)
        except Exception:
            pass

    def import_files_from_menu(self) -> None:
        """菜单栏「设置 → 📥 导入外部文件…」：拖放的兜底入口（无依赖也能用）。"""
        try:
            self.controller.import_files_from_dialog()
        except Exception as exc:  # noqa: BLE001
            logger.warning("菜单导入外部文件失败: %s", exc)
            try:
                from tkinter import messagebox
                messagebox.showerror("导入失败", f"无法打开文件选择框：\n{exc}", parent=self)
            except Exception:
                pass

    # ===================== F18：在线更新检查（T16） =====================
    def _schedule_silent_update_check(self) -> None:
        """
        启动 2 秒后的**静默**检查（after(2000) 回调）。

        全程 try 包裹 + 后台线程执行：离线时既不卡界面，也不产生任何弹窗
        或 ERROR 级日志；只有真检测到新版本才会走到 `notify_update_result`。
        """
        try:
            from utils import updater
            from ui.dialogs.update_dialog import notify_update_result
        except Exception as exc:  # noqa: BLE001
            logger.debug("更新模块不可用，跳过启动检查: %s", exc)
            return

        cfg = getattr(self, "config_data", None)

        def _work(**_kw):
            return updater.check_update(cfg, force=False)

        def _done(info):
            try:
                notify_update_result(self, info, manual=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("处理静默更新结果失败（已忽略）: %s", exc)

        def _err(msg):
            # check_update 本身承诺永不抛，这里纯属兜底，绝不升级成用户可见错误
            logger.debug("静默更新检查异常（已忽略）: %s", msg)

        try:
            self.task_manager.run_async(_work, on_done=_done, on_error=_err)
        except Exception as exc:  # noqa: BLE001
            logger.debug("提交静默更新检查任务失败（已忽略）: %s", exc)

    def check_update_from_menu(self) -> None:
        """
        菜单栏「设置 → 🔄 检查更新」：**手动**检查。

        与静默检查的本质区别：手动检查**必须**给出明确反馈 ——
        有新版本 → 弹更新对话框；已是最新 → 弹「已是最新版本」；
        没配更新源 / 缺 requests / 网络不可达 → 弹说明为什么没查成。
        """
        try:
            from utils import updater
            from ui.dialogs.update_dialog import notify_update_result, show_check_failed_dialog
        except Exception as exc:  # noqa: BLE001
            try:
                from tkinter import messagebox
                messagebox.showerror("检查更新", f"更新模块不可用：\n{exc}", parent=self)
            except Exception:
                pass
            return

        # 防重入：连点菜单不应该并发发起多次请求
        if getattr(self, "_update_check_running", False):
            try:
                from tkinter import messagebox
                messagebox.showinfo("检查更新", "正在检查更新，请稍候…", parent=self)
            except Exception:
                pass
            return
        self._update_check_running = True

        cfg = getattr(self, "config_data", None)
        try:
            self.helpers.on_log("🔄 正在检查更新…", "info")
        except Exception:
            pass

        def _work(**_kw):
            return updater.check_update(cfg, force=True)

        def _done(info):
            self._update_check_running = False
            try:
                notify_update_result(self, info, manual=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("显示更新结果失败: %s", exc)

        def _err(msg):
            self._update_check_running = False
            try:
                show_check_failed_dialog(self, "")
            except Exception:
                pass
            logger.debug("手动更新检查异常: %s", msg)

        try:
            self.task_manager.run_async(_work, on_done=_done, on_error=_err)
        except Exception as exc:  # noqa: BLE001
            self._update_check_running = False
            logger.warning("提交更新检查任务失败: %s", exc)
            try:
                show_check_failed_dialog(self, "")
            except Exception:
                pass

    # ----- 快捷键处理 -----
    def _on_ctrl_g(self):
        """Ctrl+G: 打开反应动画对话框"""
        if hasattr(self.controller, "show_reaction_animation_dialog"):
            self.controller.show_reaction_animation_dialog()
        else:
            self.helpers.on_log("⚠️ 反应动画功能未加载", "warning")

    def _on_ctrl_s(self):
        """Ctrl+S: 保存当前配置"""
        try:
            self._save_config()
            self.helpers.on_log("✅ 配置已保存", "success")
        except Exception as e:
            self.helpers.on_log(f"❌ 保存配置失败: {e}", "error")

    def _is_text_input_focused(self) -> bool:
        """当前焦点是否落在文本输入类控件上。

        用于让 Ctrl+Z / Ctrl+F 这类键在输入框里保持「编辑文本」的原生语义，
        不要被全局的「撤销文件操作」抢走——否则用户在改路径时按 Ctrl+Z
        会突然回滚一批文件，非常吓人。
        """
        try:
            w = self.focus_get()
        except Exception:
            return False
        if w is None:
            return False
        try:
            return w.winfo_class() in (
                "Entry", "TEntry", "Text", "ScrolledText",
                "TCombobox", "Spinbox", "TSpinbox", "Listbox",
            )
        except Exception:
            return False

    def _on_ctrl_z(self, event=None):
        """Ctrl+Z: 撤销上一步文件操作（输入框内则交回给输入框）"""
        if self._is_text_input_focused():
            return None
        try:
            self.controller.undo_last()
        except Exception as e:
            self.helpers.on_log(f"❌ 撤销失败: {e}", "error")
        return "break"

    def _on_ctrl_y(self, event=None):
        """Ctrl+Y / Ctrl+Shift+Z: 重做被撤销的文件操作"""
        if self._is_text_input_focused():
            return None
        try:
            self.controller.redo_last()
        except Exception as e:
            self.helpers.on_log(f"❌ 重做失败: {e}", "error")
        return "break"

    def _on_f5(self, event=None):
        """F5: 重新扫描工作目录"""
        try:
            self.controller.scan_files()
        except Exception as e:
            self.helpers.on_log(f"❌ 刷新失败: {e}", "error")
        return "break"

    def _on_ctrl_f(self, event=None):
        """Ctrl+F: 把光标送到文件列表上方的搜索框"""
        try:
            entry = getattr(self, "filter_keyword_entry", None)
            if entry is not None:
                # 搜索框在「📁 文件管理」页，先切过去再聚焦，否则用户看不到光标在哪
                nb = getattr(self, "main_notebook", None)
                if nb is not None:
                    try:
                        nb.select(1)  # 文件管理（工作台已是第 0 页）
                    except Exception:
                        pass
                entry.focus_set()
                entry.select_range(0, "end")
        except Exception as e:
            logger.debug("Ctrl+F 聚焦搜索框失败: %s", e)
        return "break"

    def _on_ctrl_shift_f(self, event=None):
        """
        Ctrl+Shift+F: 把光标送到**日志过滤条**的关键词输入框。

        ⚠️ 命名/快捷键避让（架构 §6.1）：Ctrl+F 已归「文件列表搜索框」，
        日志过滤只能用 Ctrl+Shift+F，两者绝不能互抢。
        """
        try:
            bar = getattr(self, "log_filter_bar", None)
            if bar is not None and hasattr(bar, "focus_keyword"):
                bar.focus_keyword()
        except Exception as e:
            logger.debug("Ctrl+Shift+F 聚焦日志过滤框失败: %s", e)
        return "break"

    def _show_help(self):
        """F1: 显示快捷键帮助（此处列出的键必须与实际 bind 一一对应）"""
        help_text = """⌨️ 快捷键帮助

F5              重新扫描工作目录
Ctrl+F          跳到文件搜索框
Ctrl+Shift+F    跳到日志过滤关键词框
Ctrl+Z          撤销上一步文件操作
Ctrl+Y          重做（也可用 Ctrl+Shift+Z）
Ctrl+G          打开反应动画对话框
Ctrl+S          保存当前配置
F1              显示此帮助

提示：在输入框里编辑文字时，Ctrl+Z 仍是普通的文本撤销，
不会误触发文件操作回滚。"""
        try:
            from tkinter import messagebox
            messagebox.showinfo("快捷键帮助", help_text, parent=self)
        except Exception:
            pass

    def _snapshot_config_before_save(self, reason: str = "") -> None:
        """
        F17：覆盖配置文件之前先给旧版本拍一张快照（trigger='config'）。

        ⚠️ 契约：备份失败**只警告不抛**（架构 §6.4），绝不能让「保存配置」失败。
        只在「用户显式保存 / 退出保存」这两个低频点调用，不挂在 save_config 内部，
        否则 push_recent_work_dir 等高频调用会把快照目录刷爆。
        """
        try:
            model = getattr(getattr(self, "controller", None), "model", None)
            if model is None or not hasattr(model, "create_backup_snapshot"):
                return
            if not CONFIG_FILE.is_file():
                return  # 首次运行还没有配置文件，没什么可备份的
            model.create_backup_snapshot("config", [CONFIG_FILE], reason or "保存配置前的自动快照")
        except Exception as e:  # noqa: BLE001
            logger.debug("配置快照失败（不影响保存）: %s", e)

    def _save_config(self):
        """保存当前配置到文件（内部使用）"""
        self._snapshot_config_before_save("手动保存配置前的自动快照")
        config = dict(self.config_data)
        config.update({
            "work_dir": self.work_dir_var.get(),
            "mapping_file": self.mapping_file_var.get(),
            "ext_filter": self.ext_filter_var.get(),
            "window_geometry": self.geometry(),
            "psi4_config": {
                "last_method": getattr(self, 'psi4_last_method', 'b3lyp'),
                "last_basis": getattr(self, 'psi4_last_basis', '6-31g*'),
                "last_task": getattr(self, 'psi4_last_task', 'energy')
            },
        })
        if hasattr(self, "preview_before_operation_var"):
            config["preview_before_operation"] = bool(self.preview_before_operation_var.get())
        save_config(config)

    # ----- 任务回调（转发给 helpers） -----
    def on_task_done(self, result, job=None):
        self.helpers.on_task_done(result, job=job)

    def on_task_error(self, error, job=None):
        self.helpers.on_task_error(error, job=job)

    def on_task_cancelled(self, job=None):
        """任务被用户取消（由 TaskManager 路由过来）。"""
        self.helpers.on_task_cancelled(job=job)

    def set_cancel_visible(self, visible: bool):
        """显示 / 隐藏状态栏的「取消」按钮。"""
        try:
            btn = getattr(self, "cancel_button", None)
            if btn is None:
                return
            if visible:
                btn.pack(side=tk.RIGHT, padx=4, pady=4)
            else:
                btn.pack_forget()
        except Exception:
            pass

    # ===== 问题三 + 用户需求：菜单栏入口 =====
    def show_environment_dialog_from_menu(self) -> None:
        """菜单栏「帮助 → 🧪 环境诊断」调用：直接打开诊断对话框。"""
        try:
            self.helpers.check_environment(show_dialog=True)
        except Exception as e:
            try:
                from tkinter import messagebox
                messagebox.showerror("打开失败", f"无法打开环境诊断对话框：\n{e}")
            except Exception:
                pass

    def show_backup_dialog_from_menu(self) -> None:
        """菜单栏「设置 → 🗂️ 备份管理…」调用：打开快照列表 / 回滚对话框（F17）。"""
        try:
            dlg = getattr(self, "dialogs", None)
            if dlg is None:
                from ui.dialogs import Dialogs
                dlg = Dialogs(self, self.controller)
            dlg.show_backup_manager_dialog()
        except Exception as e:
            logger.warning("打开备份管理对话框失败: %s", e)
            try:
                from tkinter import messagebox
                messagebox.showerror("打开失败", f"无法打开备份管理：\n{e}")
            except Exception:
                pass

    def show_font_size_dialog_from_menu(self) -> None:
        """菜单栏「设置 → 字体大小…」调用：打开滑块对话框。"""
        try:
            from ui.dialogs import Dialogs
            dlg = Dialogs(self, self.controller)
            dlg.show_font_size_dialog(parent=self)
        except Exception as e:
            try:
                from tkinter import messagebox
                messagebox.showerror("打开失败", f"无法打开字体大小设置：\n{e}")
            except Exception:
                pass

    def show_error_diagnosis(self, error_text: str, summary: str = None, hint: str = None) -> None:
        """F07 入口：打开错误诊断弹窗（JSON 规则库驱动）。可在任务失败 / 队列诊断时调用。"""
        try:
            from ui.dialogs.error_diagnosis import show_error_diagnosis as _show
            _show(self, error_text, summary=summary, hint=hint)
        except Exception as e:
            try:
                from tkinter import messagebox
                messagebox.showerror("诊断失败", f"无法打开错误诊断：\n{e}")
            except Exception:
                pass

    # ==================== 纯逻辑模块接入（2026-08-16） ====================
    # 以下 all 均为「纯逻辑模块 → 菜单入口」，惰性导入 + try/except 兜底，
    # 任何失败只写日志/弹提示，绝不把主窗口打崩。行为需在裸金属 GUI 实测。
    def _show_text_dialog(self, title: str, text: str) -> None:
        """只读多行文本查看对话框（供各纯逻辑模块展示结果）。"""
        from tkinter import Toplevel, Text, Scrollbar, BOTH, END, Y, RIGHT, LEFT, WORD
        top = Toplevel(self)
        top.title(title)
        top.transient(self)
        try:
            top.geometry("760x540")
        except Exception:
            pass
        txt = Text(top, wrap=WORD, font=("Consolas", 10))
        sb = Scrollbar(top, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side=RIGHT, fill=Y)
        txt.pack(side=LEFT, fill=BOTH, expand=True, padx=8, pady=8)
        txt.insert(END, text)
        txt.configure(state="disabled")

    def show_file_association_from_menu(self) -> None:
        """E-06 反向追溯：按词干把结构文件与 .log/.fchk/.out 关联。"""
        try:
            from utils.file_association import associate_by_stem
            files = sorted(f['name'] for f in (getattr(self, 'last_scan_result', []) or []))
            links = associate_by_stem(files)
            if not links:
                self.helpers.on_log("ℹ️ 未发现可追溯的结构/结果文件", 'info')
                return
            lines = []
            for lk in links:
                lines.append(f"■ {lk.stem}")
                lines.append(f"   结构: {lk.structure or '（无结构文件）'}")
                if lk.results:
                    lines.append("   结果: " + ", ".join(lk.results))
                if lk.extras:
                    lines.append("   其它: " + ", ".join(lk.extras))
            self._show_text_dialog("反向追溯（.xyz ↔ .log/.fchk/.out）", "\n".join(lines))
        except Exception as e:
            self.helpers.on_log(f"❌ 反向追溯失败: {e}", 'error')

    def show_rule_engine_from_menu(self) -> None:
        """E-03 规则引擎：用声明式规则匹配当前文件，展示命中结果。"""
        try:
            from utils.rule_engine import evaluate_rules, render_actions, load_rules
            cfg = getattr(self, 'config_data', {}) or {}
            rules_text = cfg.get('rules_json', '')
            if rules_text:
                rules, errs = load_rules(rules_text)
                if errs:
                    self.helpers.on_log("⚠️ 规则校验有误: " + "; ".join(errs[:3]), 'warning')
            else:
                rules = [
                    {"id": "big_struct", "name": "大结构文件标记待复核",
                     "when": {"field": "ext", "op": "in", "value": [".xyz", ".mol"]},
                     "then": {"action": "flag", "target": "status", "label": "review"}},
                    {"id": "orphan_result", "name": "孤立结果文件提示",
                     "when": {"field": "ext", "op": "in", "value": [".log", ".out"]},
                     "then": {"action": "notify"}},
                ]
            if not rules:
                self.helpers.on_log("⚠️ 无可用规则（未配置 rules_json 且规则为空）", 'warning')
                return
            entries = getattr(self, 'last_scan_result', []) or []
            lines = []
            for e in entries:
                matched = evaluate_rules(rules, e)
                if matched:
                    desc = ", ".join(a['rule_name'] or str(a['rule_id']) for a in render_actions(matched))
                    lines.append(f"{e['name']} → {desc}")
            if not lines:
                self.helpers.on_log("ℹ️ 规则引擎：无文件命中规则", 'info')
                return
            self._show_text_dialog("规则引擎命中", "\n".join(lines))
        except Exception as e:
            self.helpers.on_log(f"❌ 规则引擎执行失败: {e}", 'error')

    def show_hpc_script_from_menu(self) -> None:
        """E-09 HPC 作业脚本生成（SLURM 示例），保存为 .sh 或弹窗预览。"""
        try:
            from tkinter import filedialog
            from utils.hpc_script import generate_script
            job = {"name": "molmanager_job", "nodes": 1, "ntasks": 4,
                   "cpus_per_task": 4, "walltime": "12:00:00", "memory_gb": 8,
                   "commands": ["# 在此填写要运行的命令，例如：", "python main.py --batch --fix-all"]}
            script = generate_script("slurm", job)
            out = filedialog.asksaveasfilename(
                defaultextension=".sh", filetypes=[("Shell 脚本", "*.sh")],
                initialfile="submit_slurm.sh")
            if out:
                with open(out, 'w', encoding='utf-8') as f:
                    f.write(script)
                self.helpers.on_log(f"📜 SLURM 作业脚本已生成: {out}", 'success')
            else:
                self._show_text_dialog("SLURM 作业脚本", script)
        except Exception as e:
            self.helpers.on_log(f"❌ 生成作业脚本失败: {e}", 'error')

    def show_project_pack_from_menu(self) -> None:
        """E-05 项目打包：导出 / 导入 .molproj（ZIP+清单）。"""
        try:
            from tkinter import filedialog, messagebox
            from utils.project_pack import pack_project, unpack_project
            choice = messagebox.askyesnocancel(
                "项目打包 .molproj", "是 = 导出工作目录为 .molproj\n否 = 从 .molproj 导入\n取消 = 返回",
                parent=self)
            if choice is None:
                return
            work = str(self.controller.model.work_dir)
            if choice:
                out = filedialog.asksaveasfilename(
                    defaultextension=".molproj", filetypes=[("MolManager 项目", "*.molproj")],
                    initialfile="project.molproj")
                if not out:
                    return
                manifest = pack_project(work, out)
                self.helpers.on_log(f"🎒 项目已导出到 {out}（{manifest['file_count']} 个文件）", 'success')
            else:
                src = filedialog.askopenfilename(filetypes=[("MolManager 项目", "*.molproj")])
                if not src:
                    return
                res = unpack_project(src, work)
                self.helpers.on_log(
                    f"📦 项目导入完成：{res['extracted']} 个（跳过已存在 {res['skipped']} 个）", 'success')
                self.controller.scan_files()
        except Exception as e:
            self.helpers.on_log(f"❌ 项目打包失败: {e}", 'error')

    def show_log_parse_from_menu(self) -> None:
        """E-07/E-01 日志解析 + 动态元数据：解析勾选的 .log/.out/.fchk。"""
        try:
            from utils.metadata_index import extract_metadata, collect_columns
            sel = self.helpers.get_selected_files()
            lines = []
            for p in sel:
                if not p.lower().endswith(('.log', '.out', '.fchk')):
                    continue
                try:
                    with open(p, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except OSError as oe:
                    lines.append(f"■ {p}\n   （读取失败: {oe}）")
                    continue
                meta = extract_metadata(p, content)
                lines.append(f"■ {p}")
                for c in collect_columns([meta]):
                    lines.append(f"   {c}: {meta.get(c)}")
            if not lines:
                self.helpers.on_log("⚠️ 请先勾选 .log/.out/.fchk 文件", 'warning')
                return
            self._show_text_dialog("日志解析 / 动态元数据", "\n".join(lines))
        except Exception as e:
            self.helpers.on_log(f"❌ 日志解析失败: {e}", 'error')

    def show_mo_diagram_from_menu(self) -> None:
        """E-13 MO 能级图：解析勾选的 .fchk 轨道能级，导出 SVG。"""
        try:
            from tkinter import filedialog
            from utils.mo_diagram import parse_fchk_orbitals, parse_fchk_int, render_mo_svg
            sel = self.helpers.get_selected_files()
            fchk = next((p for p in sel if p.lower().endswith('.fchk')), None)
            if not fchk:
                fchk = filedialog.askopenfilename(filetypes=[("Gaussian fchk", "*.fchk")])
            if not fchk:
                return
            with open(fchk, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            energies = parse_fchk_orbitals(content)
            n_el = parse_fchk_int(content, "Number of electrons") or 0
            svg = render_mo_svg(energies, n_el, title="MO 能级图")
            out = filedialog.asksaveasfilename(
                defaultextension=".svg", filetypes=[("SVG 图", "*.svg")], initialfile="mo_diagram.svg")
            if out:
                with open(out, 'w', encoding='utf-8') as f:
                    f.write(svg)
                self.helpers.on_log(f"🖼️ MO 能级图已导出: {out}", 'success')
            else:
                self._show_text_dialog("MO 能级图 SVG", svg)
        except Exception as e:
            self.helpers.on_log(f"❌ 生成 MO 能级图失败: {e}", 'error')

    def show_structure_score_from_menu(self) -> None:
        """U-16 结构美观度评分：对勾选的结构文件做启发式打分。"""
        try:
            import chem.openbabel_utils as obu
            from utils.structure_score import score_structure
            sel = self.helpers.get_selected_files()
            structure = next((p for p in sel
                              if p.lower().endswith(('.mol', '.sdf', '.xyz', '.pdb', '.cif', '.mol2'))), None)
            if not structure:
                self.helpers.on_log("⚠️ 请先勾选一个结构文件", 'warning')
                return
            res = obu.calculate_descriptors(structure)
            if not res.get('success'):
                self.helpers.on_log(f"⚠️ 无法计算描述符: {res.get('message')}", 'warning')
                return
            s = score_structure(res.get('descriptors') or {})
            text = f"文件: {structure}\n评分: {s['score']}  {s['grade']}\n\n" + \
                   "\n".join("· " + n for n in s['notes'])
            self._show_text_dialog("结构美观度评分", text)
        except Exception as e:
            self.helpers.on_log(f"❌ 美观度评分失败: {e}", 'error')

    def show_example_library_from_menu(self) -> None:
        """U-10 示例分子库 + 失败案例教育。"""
        try:
            from utils.example_library import get_examples, get_failure_cases
            lines = ["【示例分子】"]
            for m in get_examples():
                lines.append(f"· {m['name']}（{m['english']}） {m['formula']}  "
                             f"SMILES: {m['smiles']}  [{m['category']}]")
            lines.append("")
            lines.append("【常见失败案例】")
            for c in get_failure_cases():
                lines.append(f"· {c['title']}：{c['why']}")
            self._show_text_dialog("示例分子库 & 失败案例", "\n".join(lines))
        except Exception as e:
            self.helpers.on_log(f"❌ 打开示例库失败: {e}", 'error')

    def show_wizard_steps_from_menu(self) -> None:
        """U-07 新手任务向导（6 场景）只读概览。"""
        try:
            from utils.wizard_steps import get_scenarios
            lines = []
            for s in get_scenarios():
                lines.append(f"■ {s['title']} — {s['description']}")
                for i, st in enumerate(s['steps'], 1):
                    lines.append(f"   {i}. {st['title']}：{st['detail']}")
                lines.append("")
            self._show_text_dialog("新手任务向导（6 场景）", "\n".join(lines))
        except Exception as e:
            self.helpers.on_log(f"❌ 打开向导失败: {e}", 'error')

    def show_cli_batch_from_menu(self) -> None:
        """E-08 CLI 无头模式：展示 --batch --fix-all 的有序计划预览。"""
        try:
            from utils.cli_batch import parse_args, build_batch_plan, plan_summary
            opts = parse_args(["--batch", "--fix-all", "--dry-run"])
            plan = build_batch_plan(opts)
            self._show_text_dialog("CLI 无头模式计划预览", plan_summary(plan, dry_run=True))
        except Exception as e:
            self.helpers.on_log(f"❌ CLI 计划预览失败: {e}", 'error')

    def toggle_ui_mode_from_menu(self) -> None:
        """U-06 简易/专家模式切换（写入 config.ui_mode，并提示被隐藏的功能）。"""
        try:
            from utils.feature_flags import ADVANCED_ONLY
            cfg = getattr(self, 'config_data', {}) or {}
            cur = cfg.get('ui_mode', 'simple')
            new = 'advanced' if cur != 'advanced' else 'simple'
            cfg['ui_mode'] = new
            self.config_data = cfg
            try:
                from utils.config import save_config
                save_config(cfg)
            except Exception:
                pass
            if new == 'advanced':
                msg = "已切换到「专家」模式：全部高级功能可见。"
            else:
                hidden = ", ".join(sorted(ADVANCED_ONLY))
                msg = "已切换到「简易」模式。\n\n以下功能仅在专家模式可用（当前已隐藏）：\n" + hidden
            self.helpers.on_log(msg, 'info')
            try:
                from tkinter import messagebox
                messagebox.showinfo("模式切换", msg, parent=self)
            except Exception:
                pass
        except Exception as e:
            self.helpers.on_log(f"❌ 模式切换失败: {e}", 'error')

    def show_tree_overview_from_menu(self) -> None:
        """E-02 分层目录树概览（只读，用 tree_builder 从扁平列表构建）。"""
        try:
            from utils.tree_builder import build_tree, iter_tree, count_nodes
            files = [f['name'] for f in (getattr(self, 'last_scan_result', []) or [])]
            tree = build_tree(files)
            dirs, fs = count_nodes(tree)
            lines = [f"目录树概览：{dirs} 个目录 / {fs} 个文件"]
            for path, is_file in iter_tree(tree):
                lines.append(("📄 " if is_file else "📁 ") + path)
            self._show_text_dialog("分层目录树概览", "\n".join(lines))
        except Exception as e:
            self.helpers.on_log(f"❌ 目录树概览失败: {e}", 'error')

    def on_close(self):
        # ———— 关闭拦截：若有任务正在运行，先二次确认，避免杀掉正在写的文件 ————
        try:
            if getattr(self, "task_manager", None) is not None and self.task_manager.is_busy():
                from tkinter import messagebox
                if not messagebox.askyesno(
                    "有任务正在运行",
                    "当前有文件操作或计算任务正在进行中。\n退出可能会中断操作，"
                    "导致部分文件未保存或产物不完整。\n\n确定要退出吗？",
                    parent=self,
                ):
                    return  # 用户选择不退出 → 取消关闭
        except Exception:
            pass

        # ———— 先让 Tk 事件循环处理完所有 pending 的 after/repaint 回调，
        #    防止后台线程刚塞进来的 after(0, cb) 在 destroy 之后执行触发 TclError ————
        try:
            for _ in range(2):
                self.update_idletasks()
                self.update()
        except Exception:
            pass

        # ———— 【关键】必须在 task_manager.stop() **之前** 摘除 GUI 日志 handler ————
        # 死锁链（实测会让进程永久挂起、窗口关不掉）：
        #   stop() 内主线程 join(worker) → worker 退出前 logger.info("...已停止")
        #   → GuiLogHandler.emit → 跨线程调用 app.after(0, flush)
        #   → tkinter 把该调用投递到主线程 Tcl 事件队列并阻塞等待结果
        #   → 而主线程此刻卡在 join()，根本没在跑事件循环 → 双方互等。
        # 摘掉 handler 后，_resolve_app() 返回 None，关闭期间任何后台线程的日志
        # 都只走 console/file handler，不再触碰 Tk，从源头消除该死锁。
        # 顺带也解决了原来的问题：handler 经 `lambda: self` 强引用本窗口，
        # 不摘除则整棵 UI 对象树无法回收；且 destroy 后再写日志会抛 TclError。
        try:
            from utils.logger import detach_gui_handler
            detach_gui_handler()
        except Exception:
            pass

        try:
            # stop() 内部会等 5 秒（100ms × 50 次），大部分情况下几秒内 worker 就会正常退出
            self.task_manager.stop()
        except Exception as e:
            try:
                logger.warning("关闭 TaskManager 异常: %s", e)
            except Exception:
                pass
        # 再给一次事件循环时间，把 _poll_results 里最后几条 after(0, cb) 跑掉（如果还在）
        try:
            for _ in range(2):
                self.update_idletasks()
                self.update()
        except Exception:
            pass
        # —— 先基于「已 deep_merge 过」的 config_data 来保存，避免只存一半字段丢失
        #    font_size / obabel_path / recent_work_dirs / preview_before_operation / font_follow_dpi 等 ——
        # F17：覆盖前先给旧配置拍快照（失败只警告，绝不阻断关闭流程）
        self._snapshot_config_before_save("退出保存配置前的自动快照")
        try:
            config = dict(self.config_data) if isinstance(self.config_data, dict) else {}
        except Exception:
            config = {}
        # 再覆盖需要实时同步的字段（work_dir、mapping_file 等是运行中会变的）
        config.update({
            "work_dir": self.work_dir_var.get(),
            "mapping_file": self.mapping_file_var.get(),
            "ext_filter": self.ext_filter_var.get(),
            "window_geometry": self.geometry(),
            "psi4_config": {
                "last_method": getattr(self, 'psi4_last_method', 'b3lyp'),
                "last_basis": getattr(self, 'psi4_last_basis', '6-31g*'),
                "last_task": getattr(self, 'psi4_last_task', 'energy')
            },
        })
        # 保存 preview 开关（菜单栏可能改过）
        try:
            if hasattr(self, "preview_before_operation_var"):
                config["preview_before_operation"] = bool(self.preview_before_operation_var.get())
        except Exception:
            pass
        save_config(config)
        # M-3 修复：关闭前同步跑一遍过期临时目录清理（>6 小时的就清掉）
        # 同步跑清理线程是同步执行耗时很短，不会卡死（几百毫秒；保证程序退出之前能清得更干净。
        try:
            from utils.path_utils import cleanup_stale_tempdirs
            cleanup_stale_tempdirs(max_age_seconds=6 * 3600)
        except Exception as e:
            try:
                logger.debug("关闭时清理临时目录失败：%s", e)
            except Exception:
                pass
        # 兜底：正常情况下上面（stop() 之前）已经摘过了，这里再调一次是幂等的，
        # 防止有人从别的分支跳进来直接执行到 destroy。
        try:
            from utils.logger import detach_gui_handler
            detach_gui_handler()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = MainView()
    app.mainloop()