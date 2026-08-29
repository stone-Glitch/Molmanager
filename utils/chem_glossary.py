#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内嵌化学词典（U-08 的数据层）。

提供一份「计算化学研究生高频术语」小型词典，支持：
    - lookup_term(term, max_dist=1)：精确匹配 + Levenshtein 模糊（拼写纠错，如 苯→笨）
    - glossary_search(keyword)：按术语 / 中文名 / 释义子串检索
    - make_glossary_tooltip(widget, term=None)：薄封装，把悬停 Tooltip 绑到任意 tkinter 控件

纯逻辑（术语数据 + 检索）可单测；Tooltip 绑定依赖 tkinter，仅作可选封装，
具体「哪个 UI 控件挂词典」是设计决策，由上层决定，本模块不主动挂靠任何 UI（避免盲改高风险界面）。
"""
from __future__ import annotations

# 术语 → 释义。cat: 分类（便于以后做分组/过滤）。
# 这份词典刻意保持「小而准」，覆盖 MolManager 用户最高频的困惑点；后续可继续扩充。
_GLOSSARY: dict[str, dict[str, str]] = {
    "benzene": {"zh": "苯", "en": "benzene", "cat": "常见分子", "desc": "最简芳香烃，C6H6，平面六元环，π 电子离域。"},
    "ethanol": {"zh": "乙醇", "en": "ethanol", "cat": "常见分子", "desc": "酒精，C2H6O，常用溶剂与反应底物。"},
    "water": {"zh": "水", "en": "water", "cat": "常见分子", "desc": "H2O，最常见溶剂，显隐性取决于模型。"},
    "methane": {"zh": "甲烷", "en": "methane", "cat": "常见分子", "desc": "最简烷烃，CH4。"},
    "SCF": {"zh": "自洽场", "en": "Self-Consistent Field", "cat": "量化术语", "desc": "迭代求解哈特里-福克/DFT 波函数，使场与密度自洽收敛。"},
    "HF": {"zh": "哈特里-福克", "en": "Hartree-Fock", "cat": "量化方法", "desc": "平均场近似的波函数方法，忽略电子相关；基准但不算相关能。"},
    "DFT": {"zh": "密度泛函理论", "en": "Density Functional Theory", "cat": "量化方法", "desc": "以电子密度为核心的量子化学方法，性价比高，含近似交换关联泛函。"},
    "B3LYP": {"zh": "B3LYP 泛函", "en": "B3LYP", "cat": "泛函", "desc": "最常用杂化泛函之一，含 HF 交换与梯度校正；有机分子几何/能量的稳妥默认。"},
    "basis set": {"zh": "基组", "en": "basis set", "cat": "量化术语", "desc": "用原子轨道线性组合逼近分子轨道的函数集；sto-3g 最小，def2/6-31G* 更精细。"},
    "sto-3g": {"zh": "STO-3G 基组", "en": "STO-3G", "cat": "基组", "desc": "最小基组，每个原子用一个收缩高斯函数；快速但不精确，适合试算/教学。"},
    "geometry optimization": {"zh": "几何优化", "en": "geometry optimization", "cat": "计算类型", "desc": "在给定方法/基组下寻找能量极小结构（键长/键角收敛）。"},
    "frequency": {"zh": "频率计算", "en": "frequency", "cat": "计算类型", "desc": "在优化结构上算振动频率；无虚频=稳定点，1 个虚频=过渡态。"},
    "TS": {"zh": "过渡态", "en": "transition state", "cat": "量化术语", "desc": "反应路径能量最高点，一阶鞍点，恰好一个虚频。"},
    "IRC": {"zh": "内禀反应坐标", "en": "Intrinsic Reaction Coordinate", "cat": "计算类型", "desc": "从过渡态沿最小能量路径向前/后追踪，验证反应前后真实连接的反应物/产物。"},
    "NMR": {"zh": "核磁共振", "en": "NMR", "cat": "波谱", "desc": "通过化学位移/shielding 预测谱图；需含 NMR 的泛函（如 mPW1PW91）与基组（如 6-31G*）。"},
    "pKa": {"zh": "酸解离常数", "en": "pKa", "cat": "物化性质", "desc": "衡量酸性；计算时需先确定质子化状态（见「去质子化」）。"},
    "logP": {"zh": "脂水分配系数", "en": "logP", "cat": "物化性质", "desc": "辛醇/水分配系数的对数，衡量亲脂性；越大越脂溶。"},
    "conformer": {"zh": "构象", "en": "conformer", "cat": "结构概念", "desc": "同 connectivity、不同单键二面角的空间排列；柔性分子有多个低能构象。"},
    "rotatable bond": {"zh": "可旋转键", "en": "rotatable bond", "cat": "结构概念", "desc": "单键中可自由旋转者（非环、非末端），越多构象空间越大。"},
    "solvent model": {"zh": "溶剂模型", "en": "solvent model", "cat": "量化术语", "desc": "隐式溶剂（如 PCM/IEFPCM）把溶剂当作连续介质，修正气相结果。"},
    "PCM": {"zh": "极化连续介质模型", "en": "PCM", "cat": "溶剂模型", "desc": "常用隐式溶剂；若溶剂不可用会回退气相并给出醒目告警（科学红线 S-04）。"},
    "basis convergence": {"zh": "基组收敛", "en": "basis convergence", "cat": "量化术语", "desc": "结果随基组增大趋于极限；triple-zeta 通常已较可靠。"},
    "zero-point energy": {"zh": "零点能", "en": "Zero-Point Energy (ZPE)", "cat": "热化学", "desc": "频率计算给出的 0K 振动能修正；热化学量需含此项。"},
    "enthalpy": {"zh": "焓", "en": "enthalpy", "cat": "热化学", "desc": "H = U + PV；反应焓用 freq 任务的热校正得到。"},
    "Gibbs free energy": {"zh": "吉布斯自由能", "en": "Gibbs free energy", "cat": "热化学", "desc": "G = H - TS；判断反应自发性与平衡常数的关键量。"},
    "BSSE": {"zh": "基组叠加误差", "en": "Basis Set Superposition Error", "cat": "量化术语", "desc": "弱相互作用计算时因基组重叠被高估结合能；用 Counterpoise 校正。"},
    "dispersion": {"zh": "色散校正", "en": "dispersion (D3/D4)", "cat": "泛函", "desc": "范德华/长程吸引修正；无校正泛函（如 B3LYP）对弱作用偏差大，加 -D3。"},
    "optimization": {"zh": "优化", "en": "optimization", "cat": "计算类型", "desc": "见 geometry optimization；收敛失败常因初始结构差或对称性。"},
    "spin multiplicity": {"zh": "自旋多重度", "en": "spin multiplicity", "cat": "量化术语", "desc": "2S+1；闭壳层=1，自由基=2；设错会导致收敛怪异或能量虚高。"},
    "charge": {"zh": "电荷", "en": "charge", "cat": "量化术语", "desc": "分子总电荷；与多重度共同决定电子数，务必与真实体系一致。"},
}


def _levenshtein(a: str, b: str) -> int:
    """标准编辑距离（与 mapping_utils 同源，但本模块自包含，避免跨模块依赖）。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[lb]


def lookup_term(term: str, max_dist: int = 1,
                _glossary: dict[str, dict[str, str]] | None = None) -> dict[str, str] | None:
    """
    查词：先精确（英文术语 / 中文名，大小写不敏感），再按 max_dist 模糊（拼写纠错）。
    返回释义 dict 或 None。单字中文名距离恒为 1 —— 与 mapping_utils 一致，仅当 len>=2 才模糊，
    避免「苯/水/氧」互相误命中噪声。
    """
    g = _glossary if _glossary is not None else _GLOSSARY
    t = (term or "").strip()
    if not t:
        return None
    tl = t.lower()
    # 1) 精确：英文术语
    if tl in g:
        return g[tl]
    # 精确：中文名
    for v in g.values():
        if v.get("zh", "").strip() == t:
            return v
    # 2) 模糊（仅 len>=2，避免单字噪声）
    if len(t) >= 2:
        best = None
        best_d = max_dist + 1
        for key, v in g.items():
            for cand in (key, v.get("zh", "")):
                if not cand or len(cand) < 2:
                    continue
                d = _levenshtein(t, cand)
                if d <= max_dist and d < best_d:
                    best_d = d
                    best = v
        if best is not None:
            return best
    return None


def glossary_search(keyword: str,
                    _glossary: dict[str, dict[str, str]] | None = None) -> list[dict[str, str]]:
    """按关键词子串检索：匹配英文术语 / 中文名 / 释义（大小写不敏感）。返回命中的释义列表。"""
    g = _glossary if _glossary is not None else _GLOSSARY
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    out = []
    seen = set()
    for key, v in g.items():
        blob = " ".join(str(v.get(k, "")) for k in ("en", "zh", "desc", "cat")).lower()
        if kw in key.lower() or kw in blob:
            ident = v.get("en", key)
            if ident not in seen:
                seen.add(ident)
                out.append(v)
    return out


def all_terms(_glossary: dict[str, dict[str, str]] | None = None) -> list[str]:
    """返回全部英文术语键（供 UI 自动补全 / 调试）。"""
    g = _glossary if _glossary is not None else _GLOSSARY
    return sorted(g.keys())


# ---------- 可选：tkinter Tooltip 薄封装 ----------
def make_glossary_tooltip(widget, term: str | None = None,
                          resolve_term=None) -> None:
    """
    把悬停 Tooltip 绑到 widget：鼠标进入显示术语释义，离开隐藏。
    - term：固定术语；若为 None 且提供 resolve_term 回调，则每次进入时调用
      resolve_term() 取当前术语（适合表格单元格等动态场景）。
    纯可选封装；本模块不主动挂靠任何 UI 控件。
    tkinter 在此函数内延迟导入，保证「纯数据/检索层」在无 tkinter 的沙箱也可导入与单测。
    """
    import tkinter as tk

    tip: tk.Toplevel | None = None

    def _show(event=None):
        nonlocal tip
        t = term
        if t is None and callable(resolve_term):
            try:
                t = resolve_term()
            except Exception:
                t = None
        if not t:
            return
        entry = lookup_term(t)
        if entry is None:
            return
        text = f"{entry.get('zh','')} · {entry.get('en','')}\n{entry.get('desc','')}"
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{widget.winfo_pointerx() + 12}+{widget.winfo_pointery() + 12}")
        lbl = tk.Label(tip, text=text, justify=tk.LEFT,
                       background="#FFFFE0", relief=tk.SOLID, borderwidth=1,
                       font=("Microsoft YaHei", 9), wraplength=280)
        lbl.pack(ipadx=4, ipady=3)
        tip.update_idletasks()

    def _hide(event=None):
        nonlocal tip
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
            tip = None

    widget.bind("<Enter>", _show)
    widget.bind("<Leave>", _hide)
