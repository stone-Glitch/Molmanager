import tkinter as tk

from ui.ui_theme import (
    COLORS,
)

# ------------------------- 🎨 主题颜色常量 -------------------------
from ._theme import _toggle_theme


def build_menu_bar(app) -> None:
    """
    用自绘 Frame + tk.Menubutton 做顶部菜单栏（Windows 原生 Menu 的 cascade 字体不可控）。
    参考经验 415826：不要用 app.config(menu=menubar) 依赖系统绘制，改成自己在顶部放一个 Frame，
    里面放 Menubutton，字体用 app._fonts。
    """
    F = getattr(app, "_fonts", {})
    BASE      = F.get("BASE",      ("Microsoft YaHei UI", 12))
    BOLD      = F.get("BOLD",      ("Microsoft YaHei UI", 12, "bold"))
    BTN       = F.get("BTN",       ("Microsoft YaHei UI", 12, "bold"))
    SMALL     = F.get("SMALL",     ("Microsoft YaHei UI", 11))
    MENU_ITEM = F.get("MENU_ITEM", F.get("BASE", ("Microsoft YaHei UI", 12)))

    # 菜单栏整体背景：用浅色，比主内容稍深一点做层级感
    bar = tk.Frame(app, bg=COLORS.get("menu_bar_bg", "#E1EBFF"), bd=0,
                   highlightbackground=COLORS.get("card_border", "#C7D5FF"),
                   highlightthickness=1)
    bar.pack(side=tk.TOP, fill=tk.X, padx=0, pady=0)

    # —— 1) 应用标题标签（左侧）——
    try:
        tk.Label(bar, text="  分子管理器  ",
                 bg=COLORS.get("menu_bar_bg", "#E1EBFF"),
                 fg=COLORS.get("primary", "#3B6EFF"),
                 font=BOLD, anchor="w", padx=6, pady=4
                 ).pack(side=tk.LEFT)
    except Exception:
        pass

    # 辅助：创建一个 Menubutton + 下拉 tk.Menu
    def _make_mb(bar_parent, label: str, side=tk.LEFT):
        # Menubutton 本身 tk.Menubutton 比 ttk.Menubutton 好配色
        mb = tk.Menubutton(
            bar_parent, text=label,
            bg=COLORS.get("menu_bar_bg", "#E1EBFF"),
            fg=COLORS.get("text", "#1A2142"),
            activebackground=COLORS.get("menu_hover_bg", "#1C2330"),
            activeforeground=COLORS.get("primary", "#3B6EFF"),
            font=BTN, relief=tk.FLAT, bd=0, padx=14, pady=5,
            cursor="hand2",
        )
        mb.pack(side=side, padx=0, pady=0)
        menu = tk.Menu(mb, tearoff=0,
                       bg=COLORS.get("card_bg", "#161B22"), fg=COLORS.get("text", "#E6EDF3"),
                       activebackground=COLORS.get("primary", "#2DD4BF"),
                       activeforeground=COLORS.get("btn_text", "#0F1419"),
                       font=MENU_ITEM, bd=1, relief=tk.SOLID)
        mb.configure(menu=menu)
        return mb, menu

    # —— 2) ⚙️ 设置菜单 ——
    _mb_set, menu_set = _make_mb(bar, "  ⚙️ 设置  ")
    try:
        menu_set.add_command(
            label="  🔤 字体大小…",
            command=lambda: _safe_call(app, "show_font_size_dialog_from_menu"),
        )
        menu_set.add_separator()
        # 预留给以后扩展（保留“预览前确认”等开关，先接已有变量避免空）
        try:
            _prev_var = getattr(app, "preview_before_operation_var", None)
            if _prev_var is None:
                _prev_var = tk.BooleanVar(value=True)
                app.preview_before_operation_var = _prev_var
            menu_set.add_checkbutton(
                label="  ⏱️ 文件整理前先预览（建议开启）",
                variable=_prev_var,
                onvalue=True, offvalue=False,
                command=lambda: _persist_preview_toggle(app),
            )
        except Exception:
            pass
        # 双主题切换（与 UI 设计系统一致：浅色/深色一键切换并记忆）
        menu_set.add_separator()
        menu_set.add_command(
            label="  🌓 切换浅色 / 深色主题",
            command=lambda: _toggle_theme(app),
        )
        # 手动 OB 路径快捷入口
        menu_set.add_command(
            label="  🧭 OpenBabel 可执行路径…",
            command=lambda: _open_ob_path_dialog(app),
        )
        # F06 导入外部文件（T18）：拖放的**兜底入口**。
        # 没装 tkinterdnd2、或用户不习惯拖拽时，这里走的是同一套 drop_handler 规则。
        menu_set.add_command(
            label="  📥 导入外部文件到工作目录…（也可直接拖入）",
            command=lambda: _safe_call(app, "import_files_from_menu"),
        )
        # F17 备份管理（T12）：查看自动快照 / 回滚误操作
        menu_set.add_separator()
        menu_set.add_command(
            label="  🗂️ 备份管理（快照 / 回滚）…",
            command=lambda: _safe_call(app, "show_backup_dialog_from_menu"),
        )
        # F18 在线更新检查（T16）：**手动**检查，必定给出明确反馈
        # （与启动 2s 后的静默检查区分：静默检查无更新时完全无声）
        menu_set.add_separator()
        menu_set.add_command(
            label="  🔄 检查更新…",
            command=lambda: _safe_call(app, "check_update_from_menu"),
        )
    except Exception:
        pass

    # —— 3) ❓ 帮助菜单 ——
    _mb_help, menu_help = _make_mb(bar, "  ❓ 帮助  ")
    try:
        menu_help.add_command(
            label="  🧪 环境诊断（检查 OB / PSI4 依赖）",
            command=lambda: _safe_call(app, "show_environment_dialog_from_menu"),
        )
        menu_help.add_separator()
        # OpenBabel 路径与字体大小设置已统一收敛到「⚙️ 设置」菜单，帮助菜单不再重复。
        # 关于
        menu_help.add_separator()
        menu_help.add_command(
            label="  ℹ️ 关于",
            command=lambda: _show_about(app),
        )
    except Exception:
        pass

    # —— 3.5) 🧰 工具菜单（纯逻辑模块接入入口）——
    _mb_tools, menu_tools = _make_mb(bar, "  🧰 工具  ")
    try:
        menu_tools.add_command(
            label="  📁 目录树概览",
            command=lambda: _safe_call(app, "show_tree_overview_from_menu"),
        )
        menu_tools.add_command(
            label="  🔗 反向追溯（结构 ↔ 结果）",
            command=lambda: _safe_call(app, "show_file_association_from_menu"),
        )
        menu_tools.add_command(
            label="  🧠 规则引擎",
            command=lambda: _safe_call(app, "show_rule_engine_from_menu"),
        )
        menu_tools.add_command(
            label="  📜 生成 SLURM 作业脚本…",
            command=lambda: _safe_call(app, "show_hpc_script_from_menu"),
        )
        menu_tools.add_command(
            label="  🎒 项目打包（.molproj）…",
            command=lambda: _safe_call(app, "show_project_pack_from_menu"),
        )
        menu_tools.add_separator()
        menu_tools.add_command(
            label="  📊 日志解析 / 动态元数据",
            command=lambda: _safe_call(app, "show_log_parse_from_menu"),
        )
        menu_tools.add_command(
            label="  🖼️ MO 能级图（.fchk → SVG）…",
            command=lambda: _safe_call(app, "show_mo_diagram_from_menu"),
        )
        menu_tools.add_command(
            label="  ⭐ 结构美观度评分",
            command=lambda: _safe_call(app, "show_structure_score_from_menu"),
        )
        menu_tools.add_separator()
        menu_tools.add_command(
            label="  📚 示例分子库",
            command=lambda: _safe_call(app, "show_example_library_from_menu"),
        )
        menu_tools.add_command(
            label="  🧭 新手任务向导",
            command=lambda: _safe_call(app, "show_wizard_steps_from_menu"),
        )
        menu_tools.add_command(
            label="  🖥️ CLI 无头模式预览",
            command=lambda: _safe_call(app, "show_cli_batch_from_menu"),
        )
        menu_tools.add_separator()
        menu_tools.add_command(
            label="  🎚️ 简易 / 专家模式切换",
            command=lambda: _safe_call(app, "toggle_ui_mode_from_menu"),
        )
    except Exception:
        pass

    # —— 4) 右侧状态：字体大小 + 工作目录信息（可选）——
    try:
        right_row = tk.Frame(bar, bg=COLORS.get("menu_bar_bg", "#E1EBFF"))
        right_row.pack(side=tk.RIGHT, padx=6, pady=0)
        # 字体大小显示（点击可快捷改）
        try:
            cfg = getattr(app, "config_data", {}) or {}
            _cur_pt = int(cfg.get("font_size", 14) or 14)
        except Exception:
            _cur_pt = 14
        _font_pt_var = tk.StringVar(value=f"字号 {_cur_pt}pt")
        _font_btn = tk.Button(
            right_row, textvariable=_font_pt_var,
            bg=COLORS.get("menu_bar_bg", "#E1EBFF"),
            fg=COLORS.get("primary", "#3B6EFF"),
            activebackground=COLORS.get("menu_hover_bg", "#1C2330"),
            activeforeground=COLORS.get("primary", "#3B6EFF"),
            font=SMALL, relief=tk.FLAT, bd=0, padx=10, pady=5,
            cursor="hand2",
            command=lambda: _safe_call(app, "show_font_size_dialog_from_menu"),
        )
        _font_btn.pack(side=tk.RIGHT, padx=2, pady=0)
        app._menu_font_pt_var = _font_pt_var
    except Exception:
        pass


# ——— 菜单栏内部辅助：安全调用 app 方法（容错）———

def _safe_call(app, method_name: str):
    try:
        fn = getattr(app, method_name, None)
        if callable(fn):
            return fn()
    except Exception as _e:
        try:
            from utils.logger import default_logger as _log
            from utils.logger import log_exception
            log_exception(_log, f"菜单栏调用 {method_name} 失败", _e)
        except Exception:
            print(f"[menu] {method_name} failed:", _e)



def _persist_preview_toggle(app) -> None:
    try:
        v = bool(getattr(app, "preview_before_operation_var", None) and
                 app.preview_before_operation_var.get())
        cfg = getattr(app, "config_data", None)
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["preview_before_operation"] = v
        app.config_data = cfg
        try:
            from utils.config import save_config
            save_config(cfg)
        except Exception:
            pass
    except Exception:
        pass



def _open_ob_path_dialog(app) -> None:
    try:
        from ui.dialogs import Dialogs
        dlg = Dialogs(app, getattr(app, "controller", None))
        cb = getattr(app.helpers, "check_environment", None)
        dlg.show_obabel_path_dialog(
            parent=app,
            on_saved_callback=(lambda: cb(announce_missing=False) if callable(cb) else None),
        )
    except Exception as _e:
        try:
            from tkinter import messagebox
            messagebox.showerror("打开失败", f"无法打开 OpenBabel 路径设置：\n{_e}")
        except Exception:
            pass



def _show_about(app) -> None:
    try:
        from tkinter import messagebox
        try:
            cfg = getattr(app, "config_data", {}) or {}
            pt = int(cfg.get("font_size", 14) or 14)
        except Exception:
            pt = 14
        messagebox.showinfo(
            "关于 分子管理器",
            "分子管理器（MolManager）\n\n"
            "用于化学 / 物理计算文件夹整理、分子格式转换、\n"
            "OpenBabel 工具、PSI4 量化任务 / 刚性扫描 / 动画。\n\n"
            f"当前字号：{pt} pt\n"
            "  • 顶部「⚙️ 设置 → 字体大小…」可调整\n"
            "  • 右下状态栏指示灯：绿=OB 就绪，红=OB 不可用\n"
            "  • 点击指示灯可快速进入「环境诊断」\n",
            parent=app,
        )
    except Exception:
        pass


# ------------------------- 🎨 应用全局 ttk 主题 -------------------------
