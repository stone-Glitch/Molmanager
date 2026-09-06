"""
深色护眼主题系统（UI 重构核心）。

设计目标（见 UI_DESIGN.md）：
  - 深色背景层级 + 青绿强调色，降低长时间盯计算/日志的视觉疲劳；
  - 通过「ttk.Style 配置」+「tk 全局 option_add 默认」双重覆盖，
    让现有 tk / ttk 控件在不动业务逻辑的前提下自动转深色；
  - 覆盖 apply_aurora_theme 配置的 Aurora.* 样式，确保深色为权威主题。

只动视觉层，对外契约（app.log_text / progress_var / status_var /
ext_display_var / mapping_count / set_cancel_visible / task_manager /
helpers / on_task_*）一律不变。
"""

from __future__ import annotations

import tkinter as tk
import weakref
from tkinter import ttk

# ---------- 双调色板（深色护眼 + 浅色，与 UI_DESIGN.md / 原型一致） ----------
# 两个调色板键名完全一致，确保 COLORS 代理与 ttk 样式在切换时无 KeyError。
DARK = {
    "bg": "#0F1419",  # 应用主背景
    "surface": "#161B22",  # 侧边栏 / 卡片 / 状态栏
    "card_bg": "#161B22",  # 卡片底
    "card_border": "#232B3A",  # 卡片描边（比 border 略亮以定义边界）
    "elevated": "#1C2330",  # 悬停 / 抬升 / 输入框底
    "input": "#0D1117",  # 输入框 / 文本框底
    "border": "#232B3A",  # 分隔线 / 卡片描边
    "border_strong": "#2D3645",  # 强描边（滚动条/分隔）
    "accent": "#2DD4BF",  # 主强调（青绿）
    "accent_hover": "#5EEAD4",
    "accent_soft": "#0B3B36",  # 强调弱底（选中行/批量条）
    "primary": "#2DD4BF",
    "link": "#58A6FF",  # 链接 / 信息
    "text": "#E6EDF3",  # 正文
    "text_secondary": "#A6B0BC",  # 次要说明（已提亮至 AA）
    "text_muted": "#A6B0BC",
    "text_light": "#8B97AC",  # 小标签 / 占位
    "text_hint": "#8B97AC",  # 占位 / 禁用
    "success": "#3FB950",
    "warning": "#D29922",
    "danger": "#F85149",
    "error": "#F85149",
    "error_hover": "#FF7B72",
    "muted": "#8B97AC",  # 中性（计算文件/禁用）· 语义令牌
    "info": "#58A6FF",  # 信息 · 语义令牌（同 link）
    "purple": "#8B5CF6",  # 紫色强调（步骤条/第三强调色）
    "btn_text": "#0F1419",  # 强调色按钮上的深字
    "btn_recommend_bg": "#3FB950",
    "btn_info_bg": "#2DD4BF",
    "btn_warn_bg": "#D29922",
    "btn_danger_bg": "#F85149",
    "menu_bar_bg": "#161B22",
    "menu_hover_bg": "#1C2330",
    "tree_hover": "#20283A",  # Treeview 悬停行
    "tree_sel_fg": "#0F1419",  # 选中行文字（深字配青绿底）
}

LIGHT = {
    "bg": "#EEF2F7",  # 应用主背景（实验室白天/截图清晰）
    "surface": "#FFFFFF",  # 侧边栏 / 卡片 / 状态栏
    "card_bg": "#FFFFFF",
    "card_border": "#E2E8F0",
    "elevated": "#F1F5F9",  # 悬停 / 抬升 / 输入框底
    "input": "#FFFFFF",  # 输入框 / 文本框底
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "accent": "#0D948B",  # teal-600，白底对比度达标
    "accent_hover": "#0F766E",
    "accent_soft": "#CCFBF1",
    "primary": "#0D948B",
    "link": "#2563EB",
    "text": "#0F172A",  # 正文
    "text_secondary": "#51607A",  # 次要说明（AA）
    "text_muted": "#51607A",
    "text_light": "#64748B",
    "text_hint": "#8A97AB",
    "success": "#16A34A",
    "warning": "#B45309",
    "danger": "#DC2626",
    "error": "#DC2626",
    "error_hover": "#EF4444",
    "muted": "#64748B",  # 中性（计算文件/禁用）· 语义令牌
    "info": "#2563EB",  # 信息 · 语义令牌（同 link）
    "purple": "#7C3AED",  # 紫色强调（步骤条/第三强调色）
    "btn_text": "#FFFFFF",  # 强调色按钮上的白字
    "btn_recommend_bg": "#16A34A",
    "btn_info_bg": "#0D948B",
    "btn_warn_bg": "#B45309",
    "btn_danger_bg": "#DC2626",
    "menu_bar_bg": "#FFFFFF",
    "menu_hover_bg": "#E2E8F0",
    "tree_hover": "#E2E8F0",
    "tree_sel_fg": "#FFFFFF",  # 选中行白字配青绿底
}

PALETTES = {"dark": DARK, "light": LIGHT}

# ---------- 复选框字形（主题无关，仅视觉 Token） ----------
# 用于文件 Treeview 的多选列：未选 / 已选 / 半选（表头）。
# 与 HTML 原型复选框列一致：点击行任意处切换、表头全选、部分选中显示半选。
CHECK_GLYPH = {
    "off": "☐",  # ☐  ballot box（空）
    "on": "☑",  # ☑  ballot box with check
    "partial": "▣",  # ▣  半选（表头：部分行被勾选）
}

# ---------- 当前主题状态 ----------
_CURRENT = "dark"


def get_current_theme() -> str:
    return _CURRENT


def set_current_theme(theme: str) -> None:
    global _CURRENT
    if theme in PALETTES:
        _CURRENT = theme


def get_palette() -> dict:
    return PALETTES.get(_CURRENT, DARK)


# ---------- 主题偏好持久化（用户级，跨会话记忆） ----------
import json
from pathlib import Path

_PREF_PATH = Path.home() / ".molmanager" / "theme_pref.json"


def load_theme_preference(default: str = "dark") -> str:
    try:
        if _PREF_PATH.exists():
            v = json.loads(_PREF_PATH.read_text(encoding="utf-8")).get("theme")
            if v in PALETTES:
                return v
    except Exception:
        pass
    return default


def save_theme_preference(theme: str) -> None:
    try:
        _PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREF_PATH.write_text(json.dumps({"theme": theme}), encoding="utf-8")
    except Exception:
        pass


# ---------- 主题色代理（COLORS["key"] / COLORS.get(key, d) 始终读当前主题） ----------
class ThemeColors:
    """只读代理：让 `COLORS["bg"]` 在切换主题后自动返回新值（无需重建控件）。

    仅影响「之后新建」的控件与运行时查询；已显式赋色的旧控件需刷新或重启。
    """

    def __getitem__(self, key):
        try:
            return get_palette()[key]
        except KeyError:
            return DARK.get(key, "#000000")

    def get(self, key, default=None):
        try:
            return get_palette().get(key, default)
        except Exception:
            return default


COLORS = ThemeColors()


# ---------- 工厂控件注册表（供运行时切换主题后就地刷新） ----------
# 🔴 内存泄漏修复：原实现 `_THEMED` 存 (widget, restyle_fn) 强引用，
# 对话框关闭后控件因被列表持有而无法 GC，长期运行会泄漏到 OOM。
# 改为存 weakref.ref(widget)；控件销毁后 ref() 返回 None，刷新时跳过，
# 列表中的死引用在遍历时即时清除（无害且自收敛）。
# 注意：restyle_fn 闭包只通过参数 wd 引用控件、不捕获控件本身，
# 因此 weakref 不会因闭包而被撑活（已在 card/section_title 等工厂核实）。
_THEMED: list[tuple[weakref.ref, callable]] = []


def _register(widget, restyle_fn):
    _THEMED.append((weakref.ref(widget), restyle_fn))


def refresh_themed_widgets() -> None:
    """切换主题后，重绘所有经工厂创建的控件（卡片/按钮/标题）。"""
    P = get_palette()
    alive: list[tuple[weakref.ref, callable]] = []
    for ref, fn in _THEMED:
        w = ref()
        if w is None:
            continue  # 控件已销毁：丢弃死引用，释放列表占用
        alive.append((ref, fn))
        try:
            if w.winfo_exists():
                fn(w, P)
        except Exception:
            pass
    # 原地替换为仅存活条目，避免反复开关对话框后列表无限增长
    _THEMED[:] = alive


# ---------- 已存在控件的就地换色（免重启换肤的关键） ----------
# 大量界面控件是直接写 `bg=COLORS["surface"]` / `fg=COLORS["text"]` 创建的
# （不经工厂、未登记进 _THEMED），切换主题后它们仍保留旧调色板的值，
# 所以旧实现只能弹窗让用户重启程序。
#
# 这里用「反查法」就地刷色：读出控件当前的颜色值 → 在旧调色板里反查它属于哪个 key
# → 替换成新调色板同 key 的值。因为 COLORS 代理保证了所有显式赋色都取自调色板，
# 反查是可靠且零侵入的，不需要改动任何页面构建代码。
#
# 已知边界（不改也无害）：
#   - ttk 控件由 style 驱动，apply_theme 已统一处理，这里 cget 会失败并自然跳过；
#   - Canvas 内用 create_oval / create_rectangle 画的图元（如状态栏圆点指示灯）
#     不是 widget 属性，不在此函数范围内。

# 需要就地换色的控件属性（按出现频率排序，全部包 try/except 兼容不支持的选项）
_COLOR_OPTIONS = (
    "bg",
    "fg",
    "activebackground",
    "activeforeground",
    "disabledforeground",
    "insertbackground",
    "highlightbackground",
    "highlightcolor",
    "selectbackground",
    "selectforeground",
    "troughcolor",
    "readonlybackground",
)


def refresh_all_widget_colors(root, old_theme: str | None = None) -> int:
    """遍历 root 下所有控件，把仍是旧主题色的属性就地替换为新主题色。

    参数：
      - root：根窗口（MainView）或任意容器。
      - old_theme：切换前的主题名；省略时按「所有非当前主题」来反查。

    返回实际发生修改的「控件属性」条数（用于判断是否真的刷到了东西）。
    """
    try:
        new_pal = get_palette()
    except Exception:
        return 0

    # 旧主题色 -> 调色板 key 的反查表（忽略大小写：Tk 返回小写，调色板多为大写）
    if old_theme and old_theme in PALETTES and old_theme != _CURRENT:
        sources = (PALETTES[old_theme],)
    else:
        sources = tuple(p for name, p in PALETTES.items() if name != _CURRENT)

    rev: dict[str, str] = {}
    for pal in sources:
        for k, v in pal.items():
            if isinstance(v, str):
                rev.setdefault(v.strip().lower(), k)
    if not rev:
        return 0

    changed = 0
    stack = [root]
    seen: set[str] = set()
    while stack:
        w = stack.pop()
        # 防重复/防环：同一控件可能被多个父容器引用
        try:
            key_id = str(w)
            if key_id in seen:
                continue
            seen.add(key_id)
        except Exception:
            pass
        # 先收集子控件（ttk 与 tk 混合树都能正常递归）
        try:
            stack.extend(w.winfo_children())
        except Exception:
            pass

        for opt in _COLOR_OPTIONS:
            try:
                cur = w.cget(opt)
            except Exception:
                continue  # 该控件没有这个属性（ttk 控件大多在此跳过）
            if not isinstance(cur, str) or not cur:
                continue
            pal_key = rev.get(cur.strip().lower())
            if pal_key is None:
                continue  # 不是调色板颜色（如系统色名），保持原样
            newv = new_pal.get(pal_key)
            if not isinstance(newv, str) or newv.strip().lower() == cur.strip().lower():
                continue  # 新旧同色，无需改动
            try:
                w.configure(**{opt: newv})
                changed += 1
            except Exception:
                pass
    return changed


def bind_treeview_hover(tree, hover_bg=None):
    """给 Treeview 行加悬停高亮：合并 tag（不丢失已有 tag，如状态色）、

    不覆盖已选中行、鼠标离开时清除。"""
    if hover_bg is None:
        hover_bg = get_palette().get("tree_hover", "#20283A")
    try:
        tree.tag_configure("tv_hover", background=hover_bg)
    except Exception:
        pass
    last = {"iid": None}

    def _tags(iid):
        try:
            return list(tree.item(iid, "tags"))
        except Exception:
            return []

    def _add(iid):
        if iid in tree.selection():
            return
        tg = _tags(iid)
        if "tv_hover" not in tg:
            tree.item(iid, tags=tg + ["tv_hover"])

    def _remove(iid):
        tg = _tags(iid)
        if "tv_hover" in tg:
            tg.remove("tv_hover")
            tree.item(iid, tags=tg)

    def _motion(evt):
        try:
            row = tree.identify_row(evt.y)
        except Exception:
            return
        if row == last["iid"]:
            return
        if last["iid"]:
            _remove(last["iid"])
        last["iid"] = row
        if row:
            _add(row)

    def _leave(_evt):
        if last["iid"]:
            _remove(last["iid"])
            last["iid"] = None

    tree.bind("<Motion>", _motion)
    tree.bind("<Leave>", _leave)


def apply_theme(root: tk.Tk | tk.Toplevel, theme: str = "dark") -> None:
    """把整个 Tk 应用切换为指定主题（dark/light）。须在 resolve_font_specs 之后调用。"""
    global _CURRENT
    if theme not in PALETTES:
        theme = "dark"
    _CURRENT = theme
    DARK = PALETTES[theme]  # 局部遮蔽：下方 DARK[...] 指向所选主题调色板
    F = getattr(root, "_fonts", None) or {}
    BASE = F.get("BASE", ("Microsoft YaHei", 12))
    BOLD = F.get("BOLD", ("Microsoft YaHei", 12, "bold"))
    BTN = F.get("BTN", ("Microsoft YaHei", 12, "bold"))
    ENTRY = F.get("ENTRY", ("Microsoft YaHei", 12))
    TREE = F.get("TREE", ("Microsoft YaHei", 11))
    THEAD = F.get("TREEHEAD", ("Microsoft YaHei", 11, "bold"))

    # ---------- 1) tk 控件全局默认（覆盖显式 bg 之外的默认外观） ----------
    root.configure(bg=DARK["bg"])
    _opt = root.option_add
    _opt("*Background", DARK["bg"])
    _opt("*Foreground", DARK["text"])
    _opt("*Frame.Background", DARK["bg"])
    _opt("*Label.Background", DARK["bg"])
    _opt("*Label.Foreground", DARK["text"])
    _opt("*Button.Background", DARK["elevated"])
    _opt("*Button.Foreground", DARK["text"])
    _opt("*Button.HighlightBackground", DARK["border"])
    _opt("*Button.HighlightColor", DARK["accent"])
    _opt("*Entry.Background", DARK["input"])
    _opt("*Entry.Foreground", DARK["text"])
    _opt("*Entry.InsertBackground", DARK["text"])
    _opt("*Entry.HighlightBackground", DARK["border"])
    _opt("*Text.Background", DARK["input"])
    _opt("*Text.Foreground", DARK["text"])
    _opt("*Text.InsertBackground", DARK["text"])
    _opt("*Text.SelectBackground", DARK["accent"])
    _opt("*Text.SelectForeground", DARK["bg"])
    _opt("*Listbox.Background", DARK["input"])
    _opt("*Listbox.Foreground", DARK["text"])
    _opt("*Listbox.SelectBackground", DARK["accent"])
    _opt("*Listbox.SelectForeground", DARK["bg"])
    _opt("*Canvas.Background", DARK["bg"])
    _opt("*Labelframe.Background", DARK["bg"])
    _opt("*Labelframe.Foreground", DARK["text"])
    _opt("*Menu.Background", DARK["surface"])
    _opt("*Menu.Foreground", DARK["text"])
    _opt("*Menubutton.Background", DARK["surface"])
    _opt("*Menubutton.Foreground", DARK["text"])
    _opt("*Toplevel.Background", DARK["surface"])
    # Combobox 下拉列表（这是 tk 原生 listbox，需单独配）
    _opt("*TCombobox*Listbox.background", DARK["input"])
    _opt("*TCombobox*Listbox.foreground", DARK["text"])
    _opt("*TCombobox*Listbox.selectBackground", DARK["accent"])
    _opt("*TCombobox*Listbox.selectForeground", DARK["bg"])
    # 注意：font 必须传「元组字体规格」而非裸字体名字符串。
    # 裸字符串 "Microsoft YaHei" 会被 Tk 当作字体 spec 列表解析
    # （Microsoft=family, YaHei=size），导致 Post 下拉时抛
    # "expected integer but got YaHei"，下拉 listbox 创建失败 → 下拉无选项。
    # 传元组后 tkinter 会转成 "{Microsoft YaHei} 12" 这种合法 spec。
    _opt("*TCombobox*Listbox.font", ENTRY)

    # ---------- 2) ttk 样式（权威覆盖，含 Aurora.*） ----------
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # clam 支持 background/fieldbackground 等细粒度配置
    except tk.TclError:
        pass

    style.configure(".", background=DARK["bg"], foreground=DARK["text"], font=BASE, borderwidth=0)
    style.configure("TFrame", background=DARK["bg"])
    style.configure("TLabel", background=DARK["bg"], foreground=DARK["text"], font=BASE)

    # 次按钮（默认）
    style.configure(
        "TButton",
        background=DARK["elevated"],
        foreground=DARK["text"],
        bordercolor=DARK["border"],
        lightcolor=DARK["elevated"],
        darkcolor=DARK["elevated"],
        padding=(12, 6),
        font=BTN,
        relief="solid",
        borderwidth=1,
    )
    style.map(
        "TButton",
        background=[("active", DARK["border"]), ("pressed", DARK["input"])],
        foreground=[("active", DARK["accent"]), ("pressed", DARK["text"])],
        bordercolor=[("active", DARK["accent"]), ("!active", DARK["border"])],
    )

    # 主强调按钮
    style.configure(
        "Accent.TButton",
        background=DARK["accent"],
        foreground=DARK["bg"],
        borderwidth=0,
        padding=(14, 7),
        font=BTN,
        relief="flat",
    )
    style.map("Accent.TButton", background=[("active", DARK["accent_hover"]), ("pressed", DARK["accent"])])

    # 危险按钮
    style.configure(
        "Danger.TButton",
        background=DARK["error"],
        foreground=DARK["bg"],
        borderwidth=0,
        padding=(12, 6),
        font=BTN,
        relief="flat",
    )
    style.map("Danger.TButton", background=[("active", DARK["error_hover"]), ("pressed", DARK["error"])])

    # 覆盖 aurora 主题（确保深色为权威）
    for _name in ("Aurora.TButton", "Aurora.Purple.TButton"):
        style.configure(
            _name,
            background=DARK["elevated"],
            foreground=DARK["text"],
            bordercolor=DARK["border"],
            lightcolor=DARK["elevated"],
            darkcolor=DARK["elevated"],
            padding=(12, 6),
            font=BTN,
            relief="solid",
            borderwidth=1,
        )
        style.map(
            _name,
            background=[("active", DARK["border"]), ("pressed", DARK["input"])],
            foreground=[("active", DARK["accent"]), ("pressed", DARK["text"])],
        )
    style.configure(
        "Aurora.Primary.TButton",
        background=DARK["accent"],
        foreground=DARK["bg"],
        borderwidth=0,
        relief="flat",
        padding=(14, 7),
        font=BTN,
    )
    style.map("Aurora.Primary.TButton", background=[("active", DARK["accent_hover"]), ("pressed", DARK["accent"])])
    style.configure(
        "Aurora.BigAccent.TButton",
        background=DARK["accent"],
        foreground=DARK["bg"],
        borderwidth=0,
        relief="flat",
        padding=(16, 9),
        font=BTN,
    )
    style.map("Aurora.BigAccent.TButton", background=[("active", DARK["accent_hover"]), ("pressed", DARK["accent"])])

    # 输入框 / 下拉
    style.configure(
        "TEntry",
        fieldbackground=DARK["input"],
        foreground=DARK["text"],
        bordercolor=DARK["border"],
        lightcolor=DARK["input"],
        darkcolor=DARK["input"],
        padding=4,
        insertcolor=DARK["text"],
        font=ENTRY,
    )
    style.configure(
        "TCombobox",
        fieldbackground=DARK["input"],
        foreground=DARK["text"],
        background=DARK["elevated"],
        bordercolor=DARK["border"],
        arrowcolor=DARK["text"],
        padding=4,
        font=ENTRY,
    )
    style.map("TCombobox", fieldbackground=[("readonly", DARK["input"])], foreground=[("readonly", DARK["text"])])

    # Notebook（兼容残留使用）
    style.configure("TNotebook", background=DARK["bg"], bordercolor=DARK["border"])
    style.configure(
        "TNotebook.Tab", background=DARK["surface"], foreground=DARK["text_secondary"], padding=(12, 6), font=BOLD
    )
    style.map("TNotebook.Tab", background=[("selected", DARK["accent"])], foreground=[("selected", DARK["bg"])])

    # 进度条
    style.configure("TProgressbar", troughcolor=DARK["elevated"], background=DARK["accent"], borderwidth=0, thickness=8)

    # Treeview（文件列表 / 结果表）
    style.configure(
        "Treeview",
        background=DARK["input"],
        foreground=DARK["text"],
        fieldbackground=DARK["input"],
        bordercolor=DARK["border"],
        rowheight=26,
        font=TREE,
    )
    style.map("Treeview", background=[("selected", DARK["accent"])], foreground=[("selected", DARK["bg"])])
    style.configure(
        "Treeview.Heading",
        background=DARK["surface"],
        foreground=DARK["text"],
        bordercolor=DARK["border"],
        font=THEAD,
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", DARK["elevated"])])

    # 滚动条
    style.configure(
        "TScrollbar",
        background=DARK["surface"],
        troughcolor=DARK["bg"],
        bordercolor=DARK["border"],
        arrowcolor=DARK["text_secondary"],
        relief="flat",
    )
    style.map("TScrollbar", background=[("active", DARK["elevated"])])

    # 其他 ttk 控件
    style.configure("TLabelframe", background=DARK["bg"], foreground=DARK["text"], font=BOLD)
    style.configure("TLabelframe.Label", background=DARK["bg"], foreground=DARK["text"], font=BOLD)
    style.configure("TCheckbutton", background=DARK["bg"], foreground=DARK["text"], font=BASE)
    style.map("TCheckbutton", background=[("active", DARK["bg"])])
    style.configure("TRadiobutton", background=DARK["bg"], foreground=DARK["text"], font=BASE)
    style.configure("TScale", background=DARK["bg"], troughcolor=DARK["elevated"], bordercolor=DARK["border"])
    style.configure(
        "Horizontal.TScale", background=DARK["bg"], troughcolor=DARK["elevated"], bordercolor=DARK["border"]
    )
    style.configure("TSeparator", background=DARK["border"])


def apply_dark_theme(root: tk.Tk | tk.Toplevel) -> None:
    """兼容旧调用：等同于 apply_theme(root, "dark")。"""
    apply_theme(root, "dark")


# ---------- 运行时一键切换（供命令面板 / 顶栏按钮复用） ----------
def toggle_theme(root: tk.Tk | tk.Toplevel) -> str:
    """在 dark/light 间切换并即时重绘 + 持久化偏好。返回新主题名。

    换肤链路分三层，合起来覆盖全部控件：
      1) ``apply_theme``             → ttk.Style + tk 全局 option_add（样式与新控件）
      2) ``refresh_themed_widgets``  → 工厂创建的控件（卡片/按钮/标题，登记在 _THEMED）
      3) ``refresh_all_widget_colors`` → 其余直接写 ``bg=COLORS[...]`` 的 tk 控件
         （反查法就地换色，本次新增）

    第 3 层补上了原先「必须重启程序才完全生效」的缺口：主界面绝大多数控件
    现在切换后立即变色，无需重启。
    """
    old = get_current_theme()
    new = "light" if old == "dark" else "dark"
    set_current_theme(new)
    apply_theme(root, new)
    refresh_themed_widgets()
    refresh_all_widget_colors(root, old)
    save_theme_preference(new)
    return new


def toggle_density(root: tk.Tk | tk.Toplevel) -> int:
    """在「舒适(14pt) / 紧凑(12pt)」间切换并即时重排 + 持久化 font_size。

    复用 ui_builder.resolve_font_specs 重算字体基线后，apply_theme 会把新字号
    写进 ttk 样式与全局 option_add；已登记的工厂控件经 refresh_themed_widgets 重绘。
    返回新字号（pt）。失败时回退到紧凑档，不抛异常。
    """
    try:
        from utils.config import load_config, save_config

        cfg = load_config()
        cur = int(cfg.get("font_size", 14) or 14)
        new_pt = 12 if cur >= 14 else 14
        cfg["font_size"] = new_pt
        save_config(cfg)
    except Exception:
        new_pt = 12
    try:
        from ui.ui_builder import resolve_font_specs

        resolve_font_specs(root, force_pt=new_pt)
    except Exception:
        pass
    apply_theme(root, get_current_theme())
    refresh_themed_widgets()
    return new_pt


# ---------- 3) 可复用组件工厂（供页面构建统一风格） ----------
def dark_card(parent, **kw):
    """深色卡片容器：面板底 + 1px 边框 + 圆角观感（tk 无圆角，用细边框代替）。"""
    P = get_palette()
    kw.setdefault("bg", P["surface"])
    kw.setdefault("bd", 1)
    kw.setdefault("relief", tk.SOLID)
    # 比 border 略亮，让卡片在背景上有更清晰的边界定义
    kw.setdefault("highlightbackground", P["card_border"])
    kw.setdefault("highlightthickness", 1)
    w = tk.Frame(parent, **kw)

    def _r(wd, pal):
        try:
            wd.configure(bg=pal["surface"], highlightbackground=pal["card_border"])
        except Exception:
            pass

    _register(w, _r)
    return w


def section_title(parent, text, accent=None, **kw):
    """卡片内小标题：左侧青绿强调竖条 + 文字，统一字体与配色。

    返回外层 Frame（内部排布 [竖条][文字]），可直接 .grid()/.pack()，
    调用方无需改动（原实现返回 Label，外部也只用了 .grid/.pack）。
    """
    P = get_palette()
    accent = accent or P["accent"]
    bg = kw.get("bg", P["surface"])
    fg = kw.get("fg", P["text"])
    font = kw.get("font", ("Microsoft YaHei", 13, "bold"))
    outer = tk.Frame(parent, bg=bg, bd=0, relief=tk.FLAT, highlightthickness=0)
    bar = tk.Frame(outer, width=4, bg=accent, bd=0, relief=tk.FLAT, highlightthickness=0)
    bar.grid(row=0, column=0, sticky="ns", padx=(0, 8))
    lbl = tk.Label(outer, text=text, bg=bg, fg=fg, font=font, anchor="w")
    lbl.grid(row=0, column=1, sticky="w")

    def _r(wd, pal):
        try:
            wd.configure(bg=pal["surface"])
            lbl.configure(bg=pal["surface"], fg=pal["text"])
        except Exception:
            pass

    _register(outer, _r)
    return outer


def primary_button(parent, text, command, **kw):
    """主强调按钮（青绿底深字）。"""
    P = get_palette()
    kw.setdefault("bg", P["accent"])
    kw.setdefault("fg", P["btn_text"])
    kw.setdefault("activebackground", P["accent_hover"])
    kw.setdefault("activeforeground", P["btn_text"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 14)
    kw.setdefault("pady", 6)
    # 修复启动崩溃：调用方常传 `tip="..."` 挂 Tooltip，但 tk.Button 不认识 `-tip`
    # → 从 kw 里 pop 出，按钮建好后用 add_tooltip 挂上。惰性导入防循环。
    tip_text = kw.pop("tip", None)
    w = tk.Button(parent, text=text, command=command, **kw)
    if tip_text:
        try:
            from ui.ui_builder import add_tooltip

            add_tooltip(w, tip_text)
        except Exception:
            pass

    def _r(wd, pal):
        try:
            wd.configure(
                bg=pal["accent"],
                fg=pal["btn_text"],
                activebackground=pal["accent_hover"],
                activeforeground=pal["btn_text"],
            )
        except Exception:
            pass

    _register(w, _r)
    return w


def secondary_button(parent, text, command, **kw):
    """次按钮（底浅字，悬停转强调色边框）。"""
    P = get_palette()
    kw.setdefault("bg", P["elevated"])
    kw.setdefault("fg", P["text"])
    kw.setdefault("activebackground", P["border"])
    kw.setdefault("activeforeground", P["accent"])
    kw.setdefault("relief", tk.SOLID)
    kw.setdefault("bd", 1)
    kw.setdefault("highlightbackground", P["border"])
    kw.setdefault("highlightthickness", 1)
    kw.setdefault("font", ("Microsoft YaHei", 12))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    # 同 primary_button：`tip` 由 add_tooltip 处理，不透传给 tk.Button
    tip_text = kw.pop("tip", None)
    w = tk.Button(parent, text=text, command=command, **kw)
    if tip_text:
        try:
            from ui.ui_builder import add_tooltip

            add_tooltip(w, tip_text)
        except Exception:
            pass

    def _r(wd, pal):
        try:
            wd.configure(
                bg=pal["elevated"],
                fg=pal["text"],
                activebackground=pal["border"],
                activeforeground=pal["accent"],
                highlightbackground=pal["border"],
            )
        except Exception:
            pass

    _register(w, _r)
    return w


def danger_button(parent, text, command, **kw):
    """危险按钮（红底深字）。"""
    P = get_palette()
    kw.setdefault("bg", P["error"])
    kw.setdefault("fg", P["btn_text"])
    kw.setdefault("activebackground", P["error_hover"])
    kw.setdefault("activeforeground", P["btn_text"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    w = tk.Button(parent, text=text, command=command, **kw)

    def _r(wd, pal):
        try:
            wd.configure(
                bg=pal["error"],
                fg=pal["btn_text"],
                activebackground=pal["error_hover"],
                activeforeground=pal["btn_text"],
            )
        except Exception:
            pass

    _register(w, _r)
    return w


def success_button(parent, text, command, **kw):
    """推荐/成功按钮（绿底深字，用于「一键修复」「加载」等高确定操作）。"""
    P = get_palette()
    _act = "#56D364" if get_current_theme() == "dark" else "#15803D"
    kw.setdefault("bg", P["success"])
    kw.setdefault("fg", P["btn_text"])
    kw.setdefault("activebackground", _act)
    kw.setdefault("activeforeground", P["btn_text"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    w = tk.Button(parent, text=text, command=command, **kw)

    def _r(wd, pal):
        try:
            wd.configure(
                bg=pal["success"],
                fg=pal["btn_text"],
                activebackground=("#56D364" if get_current_theme() == "dark" else "#15803D"),
                activeforeground=pal["btn_text"],
            )
        except Exception:
            pass

    _register(w, _r)
    return w


def warning_button(parent, text, command, **kw):
    """警告按钮（橙底深字，用于删除重复等需谨慎但非破坏操作）。"""
    P = get_palette()
    _act = "#E3B341" if get_current_theme() == "dark" else "#D97706"
    kw.setdefault("bg", P["warning"])
    kw.setdefault("fg", P["btn_text"])
    kw.setdefault("activebackground", _act)
    kw.setdefault("activeforeground", P["btn_text"])
    kw.setdefault("relief", tk.FLAT)
    kw.setdefault("bd", 0)
    kw.setdefault("font", ("Microsoft YaHei", 12, "bold"))
    kw.setdefault("cursor", "hand2")
    kw.setdefault("padx", 12)
    kw.setdefault("pady", 6)
    w = tk.Button(parent, text=text, command=command, **kw)

    def _r(wd, pal):
        try:
            wd.configure(
                bg=pal["warning"],
                fg=pal["btn_text"],
                activebackground=("#E3B341" if get_current_theme() == "dark" else "#D97706"),
                activeforeground=pal["btn_text"],
            )
        except Exception:
            pass

    _register(w, _r)
    return w


def _apply_tip(widget, tip):
    """把 tip 文本挂到控件上（工厂层统一消费，避免 tip 透传给 tk.Button 抛 TclError）。

    运行时懒导入 add_tooltip（ui_builder 在 build_ui 时已加载，不会触发循环依赖）。
    """
    if not tip:
        return
    try:
        from ui.ui_builder import add_tooltip

        add_tooltip(widget, tip)
    except Exception:
        pass


def themed_button(parent, text, command, kind="secondary", **kw):
    """按语义类型返回对应工厂按钮：primary/secondary/danger/success/warning。

    支持 tip= 传 tooltip 文本（自动挂到按钮上，不会透传给 tk.Button 导致崩溃）。
    """
    tip = kw.pop("tip", None)
    _map = {
        "primary": primary_button,
        "secondary": secondary_button,
        "danger": danger_button,
        "success": success_button,
        "warning": warning_button,
    }
    w = _map.get(kind, secondary_button)(parent, text, command, **kw)
    _apply_tip(w, tip)
    return w
