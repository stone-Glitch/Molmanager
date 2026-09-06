#!/usr/bin/env python3
"""utils/metadata_index —— 深度元数据索引测试（合成 .fchk / .log 文本）。"""

from utils.metadata_index import (
    collect_columns,
    extract_metadata,
    index_files,
    parse_fchk,
)

_FCHK = """\
Charge I                    0
Multiplicity I              1
Number of electrons I       42
Total Energy R              -76.44623491
Dipole Moment R             0.00012345
Zero-point energy R         1.04E-01
RMS force R                 0.00004567
Normal termination L        T
Has Nbo analysis L          F
"""


# ------------------------------------------------ parse_fchk
def test_parse_fchk_types() -> None:
    d = parse_fchk(_FCHK)
    assert d["Charge"] == 0 and d["Number of electrons"] == 42  # 整型
    assert d["Total Energy"] == -76.44623491  # 浮点
    assert d["Zero-point energy"] == 0.104  # 科学计数（1.04E-01）
    assert d["Normal termination"] is True  # 布尔 T
    assert d["Has Nbo analysis"] is False  # 布尔 F


def test_parse_fchk_empty_and_nonmatch() -> None:
    assert parse_fchk("") == {}
    assert parse_fchk("这不是 fchk 文本") == {}
    # 非法数值 → 字符串
    assert parse_fchk("Foo R  abc")["Foo"] == "abc"


def test_parse_fchk_dont_crash_on_none() -> None:
    assert parse_fchk(None) == {}  # type: ignore[arg-type]


# ------------------------------------------------ extract_metadata 分派
def test_extract_metadata_fchk() -> None:
    d = extract_metadata("water.fchk", _FCHK)
    assert d["source"] == "fchk"
    assert d["Total Energy"] == -76.44623491


def test_extract_metadata_gaussian_log() -> None:
    log = (
        "#p b3lyp/6-31g(d) opt freq\n"
        " Entering Link 1\n"
        " SCF Done:  E(RHF) =  -76.44623491\n"
        " Normal termination of Gaussian 16\n"
    )
    d = extract_metadata("water.log", log)
    assert d["source"] == "log"
    assert d["engine"] == "gaussian"
    assert d["energy"] == -76.44623491
    assert d["converged"] is True
    assert d["method"] == "b3lyp" and d["basis"] == "6-31g(d)"


def test_extract_metadata_unknown_ext() -> None:
    # 其它扩展名：不伪造，只标注来源
    assert extract_metadata("foo.xyz", "随便什么") == {"source": "xyz"}
    assert extract_metadata("noext", "") == {"source": ""}


# ------------------------------------------------ 列归并
def test_collect_columns_sorted_union() -> None:
    metas = [
        {"source": "log", "energy": 1.0, "method": "b3lyp"},
        {"source": "fchk", "Total Energy": -1.0},
        None,  # 非 dict 必须被忽略而非崩溃
    ]
    cols = collect_columns(metas)  # type: ignore[list-item]
    assert cols == ["Total Energy", "energy", "method", "source"]  # 字母序


def test_index_files() -> None:
    entries = [
        {"name": "a.log", "meta": {"source": "log", "energy": 1.0}},
        {"name": "b.fchk", "meta": {"source": "fchk", "Total Energy": -2.0}},
    ]
    out = index_files(entries)
    assert len(out) == 1
    assert out[0]["entries"] == entries
    assert out[0]["columns"] == ["Total Energy", "energy", "source"]
