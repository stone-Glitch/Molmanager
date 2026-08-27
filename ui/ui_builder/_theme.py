import sys
import tkinter as tk
from tkinter import ttk

from ui.ui_theme import (
    COLORS,
    apply_theme,
    get_current_theme,
    refresh_themed_widgets,
    save_theme_preference,
    set_current_theme,
)


# ------------------------- 🎨 主题颜色常量 -------------------------

class AuroraTheme:
    # 已切换为深色（与主窗口 ui_theme.DARK 一致），所有引用 AuroraTheme.* 的对话框自动转深色
    BG_START     = "#0F1419"
    BG_END       = "#161B22"
    CARD_BG      = "#161B22"
    CARD_BORDER  = "#232B3A"
    CARD_HL      = "#2DD4BF"
    CARD_SHADE   = "#1C2330"
    TEXT_MAIN    = "#E6EDF3"
    TEXT_MUTED   = "#9DA7B3"
    TEXT_BADGE   = "#0F1419"
    BRAND_BLUE   = "#2DD4BF"
    BRAND_GREEN  = "#3FB950"
    BRAND_PURPLE = "#8B5CF6"
    BRAND_ORANGE = "#D29922"
    BRAND_RED    = "#F85149"
    STEP_1       = "#2DD4BF"
    STEP_2       = "#8B5CF6"
    STEP_3       = "#3FB950"
    TOOLTIP_BG   = "#1C2330"
    TOOLTIP_FG   = "#E6EDF3"
    TREE_EVEN    = "#161B22"
    TREE_ODD     = "#1C2330"
    TREE_SEL_BG  = "#2DD4BF"
    TREE_SEL_FG  = "#1A2142"
    LOG_BG       = "#F8FAFF"
    LOG_SEL      = "#3B6EFF"

    @staticmethod
    def glow(base: str, pct: float = 0.2) -> str:
        base = base.lstrip("#")
        r, g, b = (int(base[i:i+2], 16) for i in (0, 2, 4))
        r = int(r + (255 - r) * pct)
        g = int(g + (255 - g) * pct)
        b = int(b + (255 - b) * pct)
        return f"#{r:02x}{g:02x}{b:02x}"


# ------------------------- 🔠 字体基线（问题一：字太小 修复） -------------------------
# 所有控件显式指定 font=app._fonts["BASE"] 等，避免依赖系统默认 9pt。
# font_size 来自 config.font_size（默认 14pt），配合 DPI 放大系数再调整一次。

def resolve_font_specs(app, force_pt: int | None = None) -> dict:
    """
    基于 config 计算字体尺寸，结果存到 app._fonts 字典。

    参数：
      - force_pt：如果调用方传入（比如字体大小对话框保存后热更新），则忽略
        config.font_size，直接以 force_pt 作为 raw_pt；常用于「保存后不重启」时
        的尽力刷新。
    """
    try:
        cfg = getattr(app, "config_data", {}) or {}
    except Exception:
        cfg = {}
    if isinstance(force_pt, int) and force_pt > 0:
        raw_pt = int(force_pt)
    else:
        raw_pt = int(cfg.get("font_size", 14) or 14)
    raw_pt = max(8, min(24, raw_pt))                      # 8..24pt（字体对话框放宽）

    # DPI 放大（Windows 125% 缩放常见）：如果 config.font_follow_dpi=True，按 DPI/96 再乘一次
    follow_dpi = bool(cfg.get("font_follow_dpi", True))
    scale = 1.0
    if follow_dpi:
        try:
            dpi = float(app.winfo_fpixels("1i"))           # 1 英寸 = DPI 像素
            if dpi > 0:
                scale = dpi / 96.0
        except Exception:
            scale = 1.0
        # DPI 缩放后保留 0.85~1.75，防止在 4K 屏上过大或在特殊屏上过小
        scale = max(0.85, min(1.75, scale))

    # 四舍五入成整数 pt
    base_pt = max(10, int(round(raw_pt * scale)))
    bold_pt = base_pt
    tree_pt = max(10, base_pt - 1)
    log_pt  = max(10, base_pt - 1)
    tab_pt  = base_pt
    btn_big_pt = max(11, base_pt)
    h1_pt = max(12, base_pt + 2)

    # 字体族：优先用系统 UI 级雅黑/微软雅黑；英文日志用 Consolas
    family_cn = "Microsoft YaHei UI" if sys.platform == "win32" else "Microsoft YaHei"
    family_mono = "Consolas" if sys.platform == "win32" else "Menlo"

    specs = {
        "BASE":      (family_cn, base_pt),
        "BOLD":      (family_cn, bold_pt, "bold"),
        "SMALL":     (family_cn, max(10, base_pt - 1)),
        "H1":        (family_cn, h1_pt, "bold"),
        "TREE":      (family_cn, tree_pt),
        "TREEHEAD":  (family_cn, tree_pt, "bold"),
        "TAB":       (family_cn, tab_pt, "bold"),
        "BIGBTN":    (family_cn, btn_big_pt, "bold"),
        "BTN":       (family_cn, base_pt, "bold"),
        "BTN2":      (family_cn, base_pt),
        "ENTRY":     (family_cn, base_pt),
        "LABEL":     (family_cn, base_pt),
        "LOG":       (family_mono, log_pt),
        "STATUS":    (family_cn, max(10, base_pt - 1)),
        "TOOLTIP":   (family_cn, max(9, base_pt - 2)),
    }
    app._fonts = specs
    # 菜单栏右侧「字号 Npt」快捷显示：有就更新
    try:
        var = getattr(app, "_menu_font_pt_var", None)
        if isinstance(var, tk.StringVar):
            var.set(f"字号 {raw_pt}pt")
    except Exception:
        pass
    # 也存到 app.option_add，对没有显式传 font 的老控件兜底（ttk 走 theme，tk 原生会读 *Font）
    try:
        app.option_add("*Font", specs["BASE"])
        app.option_add("*Label.Font", specs["BASE"])
        app.option_add("*Button.Font", specs["BASE"])
        app.option_add("*Entry.Font", specs["ENTRY"])
        app.option_add("*Text.Font", specs["BASE"])
    except Exception:
        pass
    return specs


# ------------------------- 🧭 自绘菜单栏（设置 / 帮助：字体完全可控）-------------------------

def _toggle_theme(app) -> None:
    """在「设置」菜单中切换浅色/深色主题：持久化 + 应用 + 刷新工厂控件 + 提示重启。

    与字体大小对话框一致，部分已显示的界面需重启后完全生效。
    """
    try:
        import tkinter.messagebox as _mb

        from ui.dialogs.common import _restart_app
        _new = "light" if get_current_theme() == "dark" else "dark"
        save_theme_preference(_new)
        set_current_theme(_new)
        apply_theme(app, _new)
        refresh_themed_widgets()
        _label = "浅色" if _new == "light" else "深色"
        if _mb.askyesno("已切换主题",
                        f"已切换为「{_label}」主题。\n\n"
                        "部分已显示的界面需重启后完全生效，是否立即重启？",
                        parent=app):
            _restart_app(app)
    except Exception as _e:
        print("[ui_builder] toggle_theme failed:", _e)



def apply_aurora_theme(app) -> None:
    T = AuroraTheme
    style = ttk.Style(app)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # —— 取字体基线（问题一：字太小）——
    fonts = getattr(app, "_fonts", None)
    if not fonts:
        # 兜底：build_ui 里一定先 resolve_font_specs；这里只是防调用顺序出错
        try:
            fonts = resolve_font_specs(app)
        except Exception:
            fonts = {
                "BASE":      ("Microsoft YaHei UI", 12),
                "BOLD":      ("Microsoft YaHei UI", 12, "bold"),
                "BIGBTN":    ("Microsoft YaHei UI", 13, "bold"),
                "BTN":       ("Microsoft YaHei UI", 12, "bold"),
                "TREE":      ("Microsoft YaHei UI", 11),
                "TREEHEAD":  ("Microsoft YaHei UI", 11, "bold"),
                "TAB":       ("Microsoft YaHei UI", 12, "bold"),
                "ENTRY":     ("Microsoft YaHei UI", 12),
            }

    style.configure(
        ".",
        background=T.BG_END,
        foreground=T.TEXT_MAIN,
        fieldbackground=T.CARD_BG,
        font=fonts["BASE"],
        borderwidth=0,
    )

    style.configure(
        "Aurora.TButton",
        background=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        padding=(14, 8),
        borderwidth=1,
        relief="solid",
        focusthickness=0,
        font=fonts["BTN2"],
    )
    style.map(
        "Aurora.TButton",
        background=[("active", T.glow(T.BRAND_BLUE, 0.88)), ("pressed", T.glow(T.BRAND_BLUE, 0.72))],
        foreground=[("active", T.BRAND_BLUE), ("pressed", T.TEXT_BADGE)],
        bordercolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
        lightcolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
        darkcolor=[("!active", T.CARD_BORDER), ("active", T.BRAND_BLUE)],
    )

    style.configure(
        "Aurora.BigAccent.TButton",
        background=T.BRAND_GREEN,
        foreground=T.TEXT_BADGE,
        padding=(18, 12),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=fonts["BIGBTN"],
    )
    style.map(
        "Aurora.BigAccent.TButton",
        background=[("active", "#11B99A"), ("pressed", "#0C8873")],
        foreground=[("active", T.TEXT_BADGE), ("pressed", T.TEXT_BADGE)],
    )

    style.configure(
        "Aurora.Primary.TButton",
        background=T.BRAND_BLUE,
        foreground=T.TEXT_BADGE,
        padding=(14, 8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=fonts["BTN"],
    )
    style.map(
        "Aurora.Primary.TButton",
        background=[("active", "#5A85FF"), ("pressed", "#2E58D6")],
    )

    style.configure(
        "Aurora.Purple.TButton",
        background=T.BRAND_PURPLE,
        foreground=T.TEXT_BADGE,
        padding=(14, 8),
        borderwidth=0,
        relief="flat",
        focusthickness=0,
        font=fonts["BTN"],
    )
    style.map(
        "Aurora.Purple.TButton",
        background=[("active", "#9B75F7"), ("pressed", "#7348D6")],
    )

    style.configure(
        "Aurora.TLabelframe",
        background=T.BG_END,
        borderwidth=0,
        relief="flat",
        padding=(0, 0, 0, 0),
    )
    style.configure(
        "Aurora.TLabelframe.Label",
        background=T.BG_END,
        foreground=T.TEXT_MAIN,
        font=fonts["BOLD"],
        padding=(6, 0, 6, 6),
    )

    style.configure(
        "Aurora.TEntry",
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        bordercolor=T.CARD_BORDER,
        lightcolor=T.CARD_BORDER,
        darkcolor=T.CARD_BORDER,
        padding=6,
        focusthickness=0,
        font=fonts["ENTRY"],
    )
    style.map(
        "Aurora.TEntry",
        bordercolor=[("focus", T.BRAND_BLUE)],
        lightcolor=[("focus", T.BRAND_BLUE)],
        darkcolor=[("focus", T.BRAND_BLUE)],
    )
    style.configure(
        "Aurora.TCombobox",
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        background=T.CARD_BG,
        arrowcolor=T.BRAND_BLUE,
        bordercolor=T.CARD_BORDER,
        padding=6,
        font=fonts["ENTRY"],
    )
    style.map(
        "Aurora.TCombobox",
        bordercolor=[("focus", T.BRAND_BLUE)],
    )

    # rowheight= (pt+2)*2：让行高随字体放大，避免 Tree 字挤
    tree_row = max(26, int(fonts["TREE"][1]) * 2 + 6)
    style.configure(
        "Aurora.Treeview",
        background=T.CARD_BG,
        fieldbackground=T.CARD_BG,
        foreground=T.TEXT_MAIN,
        rowheight=tree_row,
        borderwidth=1,
        relief="solid",
        bordercolor=T.CARD_BORDER,
        font=fonts["TREE"],
    )
    style.configure(
        "Aurora.Treeview.Heading",
        background=T.glow(T.BRAND_BLUE, 0.9),
        foreground=T.TEXT_MAIN,
        font=fonts["TREEHEAD"],
        relief="flat",
        padding=6,
        borderwidth=0,
    )
    style.map(
        "Aurora.Treeview",
        background=[("selected", T.TREE_SEL_BG)],
        foreground=[("selected", T.TREE_SEL_FG)],
    )

    style.configure(
        "Aurora.TNotebook",
        background=T.BG_END,
        borderwidth=0,
        tabmargins=(0, 4, 0, 0),
    )
    style.configure(
        "Aurora.TNotebook.Tab",
        background=T.CARD_BG,
        foreground=T.TEXT_MUTED,
        padding=(18, 10),
        borderwidth=1,
        relief="solid",
        bordercolor=T.CARD_BORDER,
        font=fonts["TAB"],
    )
    style.map(
        "Aurora.TNotebook.Tab",
        background=[("selected", T.BRAND_BLUE)],
        foreground=[("selected", T.TEXT_BADGE), ("active", T.BRAND_BLUE)],
        expand=[("selected", (0, 0, 0, 2))],
    )

    style.configure(
        "Aurora.Horizontal.TProgressbar",
        troughcolor=T.CARD_SHADE,
        background=T.BRAND_GREEN,
        bordercolor=T.CARD_BORDER,
        lightcolor=T.BRAND_GREEN,
        darkcolor=T.BRAND_GREEN,
        thickness=14,
    )

    style.configure(
        "Aurora.TPanedwindow",
        background=T.BG_END,
        sashwidth=4,
        sashrelief="flat",
        borderwidth=0,
    )

    style.configure(
        "Aurora.Vertical.TScrollbar",
        background=T.CARD_SHADE,
        troughcolor=T.BG_END,
        bordercolor=T.CARD_SHADE,
        arrowcolor=T.BRAND_BLUE,
        gripcount=0,
    )
    style.configure(
        "Aurora.Horizontal.TScrollbar",
        background=T.CARD_SHADE,
        troughcolor=T.BG_END,
        bordercolor=T.CARD_SHADE,
        arrowcolor=T.BRAND_BLUE,
        gripcount=0,
    )

    app._aurora_theme = T
    app._aurora_style = style


# ------------------------- 🎨 渐变背景画布 -------------------------

class AuroraGradientCanvas(tk.Canvas):
    def __init__(self, master, c1: str, c2: str, particles: int = 14, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        self._c1 = c1
        self._c2 = c2
        self._particles_n = particles
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Map>", self._on_map, add="+")

    def _on_map(self, _evt=None):
        try:
            w = self.winfo_width()
            h = self.winfo_height()
            if w > 1 and h > 1:
                try:
                    self.itemconfigure("content", width=w, height=h)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._redraw()
        except Exception:
            pass

    @staticmethod
    def _hex2rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @staticmethod
    def _rgb2hex(rgb) -> str:
        r, g, b = (max(0, min(255, int(v))) for v in rgb)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _lerp(self, a, b, t):
        return a + (b - a) * t

    def _redraw(self, _evt=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        rgb1 = self._hex2rgb(self._c1)
        rgb2 = self._hex2rgb(self._c2)
        for y in range(0, h, 2):
            t = y / max(1, h - 1)
            t_e = t * t * (3 - 2 * t)
            col = self._rgb2hex(self._lerp(rgb1[i], rgb2[i], t_e) for i in range(3))
            self.create_rectangle(0, y, w, y + 2, fill=col, outline=col)
        import random
        rng = random.Random(42)
        palette = [AuroraTheme.BRAND_BLUE, AuroraTheme.BRAND_GREEN, AuroraTheme.BRAND_PURPLE]
        for i in range(self._particles_n):
            cx = int(rng.uniform(0.05 * w, 0.95 * w))
            cy = int(rng.uniform(0.05 * h, 0.9 * h))
            r = int(rng.uniform(40, 150))
            col = rng.choice(palette)
            for k in range(6, 0, -1):
                alpha = 0.03 * k
                rgb = self._hex2rgb(col)
                bg = self._hex2rgb(self._c2 if cy / h > 0.5 else self._c1)
                mixed = self._rgb2hex(self._lerp(bg[i], rgb[i], alpha) for i in range(3))
                self.create_oval(cx - r * k / 6, cy - r * k / 6,
                                 cx + r * k / 6, cy + r * k / 6,
                                 fill=mixed, outline=mixed)
        try:
            w2 = self.winfo_width()
            h2 = self.winfo_height()
            if w2 > 1 and h2 > 1:
                self.itemconfigure("content", width=w2, height=h2)
        except Exception:
            pass


# ------------------------- 🎨 玻璃卡片容器 -------------------------

def make_aurora_card(parent, title: str | None = None, accent: str | None = None, *,
                     app_ref=None) -> tuple[tk.Frame, tk.Frame]:
    T = AuroraTheme
    accent = accent or T.BRAND_GREEN
    outer = tk.Frame(
        parent,
        bg=T.CARD_BORDER,
        highlightthickness=0,
        bd=0,
    )
    inner = tk.Frame(outer, bg=T.CARD_BG, bd=0, highlightthickness=0)
    inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    if title:
        header = tk.Frame(inner, bg=T.CARD_BG, bd=0)
        header.pack(fill=tk.X, padx=18, pady=(16, 0))
        cap = tk.Frame(header, bg=accent, height=18, width=4, bd=0)
        cap.pack(side=tk.LEFT)
        tk.Frame(header, bg=T.glow(accent, 0.6), height=18, width=2, bd=0).pack(side=tk.LEFT)
        title_font = ("Microsoft YaHei UI", 12, "bold")
        if app_ref is not None:
            try:
                title_font = getattr(app_ref, "_fonts", {}).get("BOLD", title_font)
            except Exception:
                pass
        tk.Label(
            header,
            text=title,
            bg=T.CARD_BG,
            fg=T.TEXT_MAIN,
            font=title_font,
            padx=10, pady=0,
        ).pack(side=tk.LEFT)
        rule = tk.Frame(header, bg=T.CARD_BG, height=22)
        rule.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        tk.Frame(rule, bg=T.CARD_BORDER, height=1).pack(side=tk.BOTTOM, fill=tk.X)

    return outer, inner


# ------------------------- 🫧 Tooltip 升级：玻璃胶囊 -------------------------

class ToolTip:
    def __init__(self, widget, text: str, font=None):
        self.widget = widget
        self.text = text
        self.font = font or ("Microsoft YaHei UI", 9)
        self.tip_window: tk.Toplevel | None = None
        self.id: str | None = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<ButtonPress>", self._on_leave)

    def _on_enter(self, _event=None):
        self.id = self.widget.after(380, self._show_tip)

    def _on_leave(self, _event=None):
        if self.id is not None:
            self.widget.after_cancel(self.id)
            self.id = None
        self._hide_tip()

    def _show_tip(self):
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 24
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            tw.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        T = AuroraTheme
        wrap = tk.Frame(tw, bg=T.BRAND_BLUE, bd=0, highlightthickness=0)
        wrap.pack()
        body = tk.Frame(wrap, bg=T.TOOLTIP_BG, padx=14, pady=10, bd=0)
        body.pack(padx=1, pady=1)
        tk.Label(
            body,
            text=self.text,
            justify=tk.LEFT,
            bg=T.TOOLTIP_BG,
            fg=T.TOOLTIP_FG,
            font=self.font,
            wraplength=320,
        ).pack()

    def _hide_tip(self):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None



def add_tooltip(widget, text: str, font=None):
    ToolTip(widget, text, font=font)


# ------------------------- 🧘 主界面：大字体、扁平卡片风格 -------------------------
# 设计：
#   - 100% tk.Frame + ttk，零 Canvas / create_window 嵌套（彻底避免 content 尺寸为 0）
#   - 大字号：卡片标题 13pt / 正文 12pt / Tree 11pt（行高 30）/ 日志 13pt
#   - 分区：配置 / 操作 / 文件列表+日志 / 状态栏
#   - 兼容旧逻辑：filter_status_var / filter_ext_var 默认 "全部"
#   - 关键词过滤条（输入即搜）

# 深色调色板（UI 重构：现代深色护眼风格，详见 UI_DESIGN.md）


# ------------------------- 🔧 辅助：可折叠面板（Labelframe 可「展开/收起」） -------------------------

class CollapsibleFrame(tk.LabelFrame):
    """
    一个可折叠的 LabelFrame：标题栏右侧有「▼/▶」按钮，点击后收起下方内容；
    用于「高级参数」「扫描参数」等默认折叠、不干扰新手但保持功能完整的面板。
    """

    def __init__(self, master, title: str = "", collapsed: bool = False, **kwargs):
        kwargs.setdefault("bg", COLORS["card_bg"])
        kwargs.setdefault("fg", COLORS["text"])
        # 字太小：LabelFrame 标题默认 12→至少 13pt bold（跟随 config 默认 14pt 的 BOLD 基线）
        kwargs.setdefault("font", ('Microsoft YaHei', 13, 'bold'))
        kwargs.setdefault("relief", tk.GROOVE)
        kwargs.setdefault("bd", 2)
        super().__init__(master, text=f"  {title}  ", **kwargs)
        self._collapsed = bool(collapsed)
        self._title = title
        # 子容器：所有用户内容都应塞到 self.body 里
        self.body = tk.Frame(self, bg=COLORS["card_bg"])

        # 字体：CollapsibleFrame 是通用组件，作用域里没有 app 变量。
        # 通过 winfo_toplevel() 拿到承载它的主窗口（MainView），复用其 _fonts 基线；
        # 拿不到就退回默认字体，绝不能因为取字体失败而让整个界面构建崩掉。
        try:
            _app = master.winfo_toplevel()
        except Exception:
            _app = None
        _btn_font = getattr(_app, '_fonts', {}).get('BTN', ('Microsoft YaHei', 12, 'bold')) \
            if _app is not None else ('Microsoft YaHei', 12, 'bold')

        # 「▼ / ▶」切换按钮：嵌入 labelwidget 机制更复杂，这里在 label_frame 的「空白」放
        # 一个小按钮到右上角即可（grid 里塞一个 LabelFrame 内没有直接右上角位置，改用叠加实现）
        self._toggle_btn = tk.Button(
            self, text="▼", relief=tk.FLAT, bg=COLORS["card_bg"], fg=COLORS["primary"],
            font=_btn_font, cursor="hand2", width=3,
            command=self._toggle,
        )
        # 用 place 放到右上角，不影响 body 的 pack/grid
        self._toggle_btn.place(relx=1.0, y=2, anchor="ne")
        self.bind("<Configure>", lambda _e: self._reposition_toggle())
        # 按构造参数直接落地初始状态（collapsed=True 就是收起，不做任何取反）
        self._apply_state()

    def _reposition_toggle(self):
        try:
            self._toggle_btn.place(relx=1.0, y=2, anchor="ne")
        except Exception:
            pass

    def _apply_state(self):
        """把 self._collapsed 渲染到界面上（收起=隐藏 body，展开=显示 body）。"""
        if self._collapsed:
            try:
                self.body.pack_forget()
            except Exception:
                try:
                    self.body.grid_forget()
                except Exception:
                    pass
            self._toggle_btn.config(text="▶")
        else:
            self.body.pack(fill=tk.X, expand=False, padx=4, pady=(0, 6))
            self._toggle_btn.config(text="▼")

    def _toggle(self):
        """用户点击标题栏箭头：翻转折叠状态并重绘。"""
        self._collapsed = not self._collapsed
        self._apply_state()


# ------------------------- 🎨 颜色调色板（双主题，随 ui_theme 当前主题切换） -------------------------
# COLORS 现为 ui.ui_theme 的 ThemeColors 代理：COLORS["bg"] / COLORS.get("bg", d)
# 始终返回「当前主题」的调色板值，构建新控件与运行时查询自动跟随主题。



def _make_scrolled_frame(master, bg, use_x=True, use_y=True):
    """
    返回一个 (outer, inner) 元组：
      - outer：直接放进父容器（如 Notebook 标签页）的框架，自身用 grid 铺满父容器；
      - inner：真正承载内容的框架，放在一个 Canvas 视口里，超宽/超高时可滚动，
               但内容较小时会自动撑满视口（文件列表随窗口增高）。

    解决 Tab1「文件管理」在最小窗口下右侧控件被裁切的问题：改用滚动而非直接裁掉。
    """
    outer = tk.Frame(master, bg=bg)
    canvas = tk.Canvas(outer, bg=bg, highlightthickness=0)
    inner = tk.Frame(canvas, bg=bg)
    canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner")

    vbar = hbar = None
    if use_y:
        vbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
    if use_x:
        hbar = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set)

    canvas.grid(row=0, column=0, sticky="nsew")
    if vbar is not None:
        vbar.grid(row=0, column=1, sticky="ns")
    if hbar is not None:
        hbar.grid(row=1, column=0, sticky="ew")
    outer.grid_rowconfigure(0, weight=1)
    outer.grid_columnconfigure(0, weight=1)

    def _sync(_evt=None):
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
            # 让 inner 至少和视口一样大：内容少时撑满（布局正常），内容多时可滚动
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            iw = inner.winfo_reqwidth()
            ih = inner.winfo_reqheight()
            canvas.itemconfigure("inner", width=max(cw, iw), height=max(ch, ih))
        except Exception:
            pass

    inner.bind("<Configure>", _sync)
    canvas.bind("<Configure>", _sync)

    # 鼠标滚轮：悬停画布时纵向滚动；Shift+滚轮横向滚动（仅本画布，避免影响其他标签页）
    def _on_wheel(evt):
        if evt.state & 0x0001:  # Shift 按下 → 横向
            canvas.xview_scroll(int(-evt.delta / 120), "units")
        else:
            canvas.yview_scroll(int(-evt.delta / 120), "units")

    canvas.bind("<MouseWheel>", _on_wheel)

    return outer, inner



def apply_aurora_theme_if_available(app):
    """如果有 apply_aurora_theme 就调用（tkk Notebook/Progressbar/Button 样式更统一）。"""
    try:
        from ui.ui_builder import apply_aurora_theme
        apply_aurora_theme(app)
    except Exception:
        pass


# ===========================================================
# 🔝 顶部全局工具栏
# ===========================================================
