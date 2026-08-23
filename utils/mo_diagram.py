#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-13 MO 能级图 / 能量趋势图（.fchk 轨道能级 → SVG）· 纯逻辑层

从 Gaussian .fchk 的轨道能级块抽取 alpha 轨道能量，计算 HOMO/LUMO/带隙，
并生成一张 SVG 能级图（纯文本，无 tkinter/matplotlib 依赖，可沙箱单测）。

红线：轨道能级缺失或电子数非法时，返回带说明的占位 SVG 或明确标记，
绝不画一张「看起来正常但其实没数据」的假图。
"""
import re
from typing import Dict, List, Optional, Tuple

_FLOAT = re.compile(r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?")


def _parse_floats(s: str) -> List[float]:
    return [float(m) for m in _FLOAT.findall(s)]


def parse_fchk_orbitals(text: str, key: str = "Alpha Orbital Energies") -> List[float]:
    """
    从 .fchk 文本解析某个轨道能级块，返回有序的浮点列表。

    Gaussian fchk 格式：头部行含标签与 ``N=<个数>``，随后若干行、
    每行多个空格分隔的浮点值。本函数按 N 收集恰好 N 个值。
    """
    lines = (text or "").splitlines()
    n: Optional[int] = None
    collecting = False
    vals: List[float] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if not collecting:
            if s.startswith(key):
                collecting = True
                m = re.search(r"N=\s*(\d+)", s)
                if m:
                    n = int(m.group(1))
        else:
            vals.extend(_parse_floats(s))
            if n is not None and len(vals) >= n:
                break
    if n is not None:
        vals = vals[:n]
    return vals


def parse_fchk_int(text: str, key: str) -> Optional[int]:
    """抽取 .fchk 里单行整型字段（如 Number of electrons）。"""
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(key):
            m = _FLOAT.search(s[len(key):])
            if m:
                return int(float(m.group(0)))
    return None


def homo_lumo(energies: List[float], n_electrons: int) -> Dict:
    """
    按闭壳近似（每个轨道占 2 电子）计算 HOMO/LUMO/带隙。

    返回 {homo, lumo, gap, homo_idx, lumo_idx, n_occ, ok}。
    非法输入（空轨道、电子数越界）返回 ok=False 且数值为 None。
    """
    e = sorted(energies)
    n = len(e)
    if n == 0 or n_electrons <= 0:
        return {"homo": None, "lumo": None, "gap": None,
                "homo_idx": None, "lumo_idx": None, "n_occ": 0, "ok": False}
    n_occ = n_electrons // 2
    if n_occ >= n:  # 全部占据，无 LUMO
        return {"homo": e[-1], "lumo": None, "gap": None,
                "homo_idx": n - 1, "lumo_idx": None, "n_occ": n_occ, "ok": True}
    homo_idx = max(0, n_occ - 1)
    lumo_idx = n_occ
    homo, lumo = e[homo_idx], e[lumo_idx]
    return {"homo": homo, "lumo": lumo, "gap": lumo - homo,
            "homo_idx": homo_idx, "lumo_idx": lumo_idx, "n_occ": n_occ, "ok": True}


def build_levels(energies: List[float], n_electrons: int, window: int = 4) -> Tuple[List[Dict], Dict]:
    """取 frontier 附近 ``window`` 个占据 + ``window`` 个空轨道，返回 (levels, frontier)。"""
    frontier = homo_lumo(energies, n_electrons)
    e = sorted(energies)
    n = len(e)
    if not frontier["ok"] or n == 0:
        return [], frontier
    hi, li = frontier["homo_idx"], frontier["lumo_idx"]
    lo = max(0, hi - window + 1)
    hi_idx = min(n - 1, hi + window if li is not None else hi)
    levels = []
    for i in range(lo, hi_idx + 1):
        occ = 2 if i <= hi else 0
        levels.append({
            "index": i,
            "energy": e[i],
            "occ": occ,
            "kind": "occ" if i <= hi else "virt",
            "is_homo": i == hi,
            "is_lumo": (li is not None and i == li),
        })
    return levels, frontier


def render_mo_svg(
    energies: List[float],
    n_electrons: int,
    title: str = "MO 能级图",
    width: int = 420,
    height: int = 520,
) -> str:
    """
    生成一张 SVG 能级图（字符串）。占据轨道实线+填充点、空轨道虚线+空心点，
    HOMO/LUMO 标注、带隙双箭头标注。

    - 空轨道 / 电子数非法 → 返回一张带说明文字的占位 SVG，不造假图。
    """
    margin_l, margin_r, margin_t, margin_b = 60, 60, 40, 50
    levels, frontier = build_levels(energies, n_electrons)
    if not levels:
        return _placeholder_svg(title, width, height, "无可用的轨道能级数据")

    emin = min(lv["energy"] for lv in levels)
    emax = max(lv["energy"] for lv in levels)
    span = (emax - emin) or 1.0
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b

    def y_for(energy: float) -> float:
        return margin_t + (emax - energy) / span * plot_h

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
    )
    parts.append(
        f'<rect width="{width}" height="{height}" fill="#1e1e1e"/>'
    )
    parts.append(
        f'<text x="{width//2}" y="24" fill="#e6e6e6" font-size="15" '
        f'text-anchor="middle" font-family="sans-serif">{_escape(title)}</text>'
    )

    # 能量轴刻度标签
    parts.append(
        f'<text x="{margin_l - 8}" y="{y_for(emax) + 4}" fill="#8b949e" '
        f'font-size="11" text-anchor="end" font-family="monospace">{emax:.3f}</text>'
    )
    parts.append(
        f'<text x="{margin_l - 8}" y="{y_for(emin) + 4}" fill="#8b949e" '
        f'font-size="11" text-anchor="end" font-family="monospace">{emin:.3f}</text>'
    )

    # 能级线
    for lv in levels:
        y = y_for(lv["energy"])
        x1 = margin_l + 20
        x2 = width - margin_r
        is_occ = lv["kind"] == "occ"
        color = "#4c9aff" if is_occ else "#9aa4b0"
        dash = "" if is_occ else ' stroke-dasharray="5,4"'
        parts.append(
            f'<line x1="{x1}" y1="{y:.1f}" x2="{x2}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="2"{dash}/>'
        )
        # 端点：占据实心、空轨道空心
        fill = color if is_occ else "#1e1e1e"
        parts.append(f'<circle cx="{x1}" cy="{y:.1f}" r="4" fill="{fill}" stroke="{color}"/>')
        # 右侧能级标注
        label = f"MO{lv['index'] + 1}"
        parts.append(
            f'<text x="{x2 + 4}" y="{y + 4:.1f}" fill="#b6bcc6" font-size="11" '
            f'font-family="monospace">{label}</text>'
        )
        if lv.get("is_homo"):
            parts.append(
                f'<text x="{x1 - 6}" y="{y - 6:.1f}" fill="#ffd166" font-size="12" '
                f'text-anchor="end" font-family="sans-serif">HOMO</text>'
            )
        if lv.get("is_lumo"):
            parts.append(
                f'<text x="{x1 - 6}" y="{y - 6:.1f}" fill="#ff8fa3" font-size="12" '
                f'text-anchor="end" font-family="sans-serif">LUMO</text>'
            )

    # 带隙双箭头
    if frontier.get("gap") is not None:
        homo_lv = next((lv for lv in levels if lv.get("is_homo")), None)
        lumo_lv = next((lv for lv in levels if lv.get("is_lumo")), None)
        if homo_lv and lumo_lv:
            yh = y_for(homo_lv["energy"])
            yl = y_for(lumo_lv["energy"])
            xgap = x1 + 8
            parts.append(
                f'<line x1="{xgap}" y1="{yh:.1f}" x2="{xgap}" y2="{yl:.1f}" '
                f'stroke="#ffd166" stroke-width="1.5" marker-end="url(#arr)"/>'
            )
            mid = (yh + yl) / 2
            parts.append(
                f'<text x="{xgap + 6}" y="{mid:.1f}" fill="#ffd166" font-size="12" '
                f'font-family="monospace">ΔE={frontier["gap"]:.4f}</text>'
            )
            parts.append(
                '<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="4" refY="4" '
                'orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#ffd166"/></marker></defs>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def _placeholder_svg(title: str, width: int, height: int, msg: str) -> str:
    esc = _escape(msg)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#1e1e1e"/>'
        f'<text x="{width//2}" y="24" fill="#e6e6e6" font-size="15" text-anchor="middle" '
        f'font-family="sans-serif">{_escape(title)}</text>'
        f'<text x="{width//2}" y="{height//2}" fill="#8b949e" font-size="13" '
        f'text-anchor="middle" font-family="sans-serif">{esc}</text>'
        f'</svg>'
    )


def _escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


__all__ = ["parse_fchk_orbitals", "parse_fchk_int", "homo_lumo",
           "build_levels", "render_mo_svg"]
