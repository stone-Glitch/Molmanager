#!/usr/bin/env python3
"""utils/mo_diagram —— MO 能级图纯逻辑测试。

红线守卫：轨道数据缺失时必须返回占位 SVG（明确说明），绝不画假图。
"""

from utils.mo_diagram import (
    build_levels,
    homo_lumo,
    parse_fchk_int,
    parse_fchk_orbitals,
    render_mo_svg,
)


_FCHK = """\
Number of alpha electrons I       6
Number of beta electrons I        6
Alpha Orbital Energies R=      5
 -0.9000000E+00 -0.7500000E+00 -0.6000000E+00  0.1000000E+00
  0.2500000E+00
"""


# ------------------------------------------------ fchk 解析
def test_parse_fchk_orbitals() -> None:
    vals = parse_fchk_orbitals(_FCHK)
    assert len(vals) == 5
    assert vals[0] == -0.9 and vals[-1] == 0.25
    # 值跨多行也能收齐，且恰好截断到 N 个
    assert vals == [-0.9, -0.75, -0.6, 0.1, 0.25]


def test_parse_fchk_orbitals_missing_block() -> None:
    assert parse_fchk_orbitals("没有轨道块") == []
    assert parse_fchk_orbitals("") == []


def test_parse_fchk_int() -> None:
    assert parse_fchk_int(_FCHK, "Number of alpha electrons") == 6
    assert parse_fchk_int(_FCHK, "Number of beta electrons") == 6
    assert parse_fchk_int(_FCHK, "不存在的键") is None


# ------------------------------------------------ HOMO/LUMO
def test_homo_lumo_closed_shell() -> None:
    e = [-0.9, -0.75, -0.6, 0.1, 0.25]
    r = homo_lumo(e, 6)  # 闭壳：6 电子占 3 个轨道
    assert r["ok"] is True
    assert r["homo"] == -0.6 and r["homo_idx"] == 2
    assert r["lumo"] == 0.1 and r["lumo_idx"] == 3
    assert r["gap"] == pytest_approx(0.7)
    assert r["n_occ"] == 3


def pytest_approx(x: float) -> object:
    import pytest

    return pytest.approx(x)


def test_homo_lumo_all_occupied() -> None:
    # 全部轨道被占据 → 无 LUMO，但 homo 仍有效
    r = homo_lumo([-0.9, -0.6], 4)
    assert r["ok"] is True
    assert r["homo"] == -0.6
    assert r["lumo"] is None and r["gap"] is None


def test_homo_lumo_invalid_inputs() -> None:
    for energies, ne in ([], 6), ([-0.5, 0.1], 0), ([-0.5, 0.1], -3):
        r = homo_lumo(energies, ne)
        assert r["ok"] is False
        assert r["homo"] is None and r["lumo"] is None


def test_homo_lumo_sorts_input() -> None:
    # 输入乱序也应得到正确的排序结果
    r = homo_lumo([0.1, -0.9, -0.6, -0.75, 0.25], 6)
    assert r["homo"] == -0.6 and r["lumo"] == 0.1


# ------------------------------------------------ 能级窗口
def test_build_levels_window() -> None:
    e = [-0.9, -0.75, -0.6, 0.1, 0.25]
    levels, frontier = build_levels(e, 6, window=4)
    assert frontier["ok"] is True
    # window=4：占据取到 0..2，空轨道取到 3..4 → 全部 5 个
    assert [lv["index"] for lv in levels] == [0, 1, 2, 3, 4]
    occ = [lv for lv in levels if lv["kind"] == "occ"]
    virt = [lv for lv in levels if lv["kind"] == "virt"]
    assert len(occ) == 3 and all(lv["occ"] == 2 for lv in occ)
    assert len(virt) == 2 and all(lv["occ"] == 0 for lv in virt)
    assert sum(lv["is_homo"] for lv in levels) == 1
    assert sum(lv["is_lumo"] for lv in levels) == 1


def test_build_levels_invalid_returns_empty() -> None:
    levels, frontier = build_levels([], 6)
    assert levels == [] and frontier["ok"] is False


# ------------------------------------------------ SVG 渲染
def test_render_mo_svg_normal() -> None:
    svg = render_mo_svg([-0.9, -0.75, -0.6, 0.1, 0.25], 6, title="水分子 MO")
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "HOMO" in svg and "LUMO" in svg
    assert "ΔE=" in svg  # 带隙标注
    assert "stroke-dasharray" in svg  # 空轨道虚线


def test_render_mo_svg_placeholder_when_no_data() -> None:
    svg = render_mo_svg([], 6, title="空")
    assert "无可用的轨道能级数据" in svg
    assert "HOMO" not in svg  # 不画假图


def test_render_mo_svg_escapes_title() -> None:
    svg = render_mo_svg([-0.9, -0.75, -0.6, 0.1, 0.25], 6, title="a<b&c")
    assert "a&lt;b&amp;c" in svg
