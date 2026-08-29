#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：chem/openbabel_utils 子包的「公共命名空间」必须完整。

背景（重要）
----------
``openbabel_utils`` 曾从单文件拆成 ``_common / _io / _descriptors / _advanced /
_cli / _check / _cache / _search`` 多个子模块。拆分时漏掉了
``from ._common import *``，导致各子模块拿不到 ``ob`` / ``pybel`` /
``PYBEL_AVAILABLE`` / ``desc_cache`` / ``mol_read_cache``，
**所有化学功能在运行时抛 NameError**（被各自的 except 吞成
"success=False"，表现为「功能点了没反应」）。

这类问题语法检查查不出、导入也不会失败，只能靠这类断言守住。
本文件**不需要安装 OpenBabel** 也能跑，因此在任何 CI 环境都是第一道防线。
"""

from __future__ import annotations

import importlib

import pytest

# (子模块, 该模块必须能从 _common 拿到的名字)
EXPECTED_NAMES: dict[str, tuple[str, ...]] = {
    "chem.openbabel_utils._common": ("ob", "pybel", "PYBEL_AVAILABLE",
                                     "desc_cache", "mol_read_cache", "OB_INSTALL_GUIDE"),
    "chem.openbabel_utils._io": ("ob", "pybel", "PYBEL_AVAILABLE", "mol_read_cache"),
    "chem.openbabel_utils._descriptors": ("ob", "pybel", "PYBEL_AVAILABLE", "desc_cache"),
    "chem.openbabel_utils._advanced": ("ob", "pybel", "PYBEL_AVAILABLE"),
    # 下划线开头的名字不会被 `import *` 带进来，必须显式导入 —— 单独盯住
    "chem.openbabel_utils._cli": ("ob", "pybel", "PYBEL_AVAILABLE", "OB_INSTALL_GUIDE",
                                  "_MANUAL_OBABEL_PATH"),
    "chem.openbabel_utils._check": ("ob", "pybel", "PYBEL_AVAILABLE", "OB_INSTALL_GUIDE"),
    "chem.openbabel_utils._cache": ("desc_cache", "mol_read_cache"),
}


@pytest.mark.parametrize(
    "module_name,names",
    [(m, n) for m, n in EXPECTED_NAMES.items()],
    ids=list(EXPECTED_NAMES),
)
def test_submodule_has_common_namespace(module_name: str, names: tuple[str, ...]) -> None:
    mod = importlib.import_module(module_name)
    missing = [n for n in names if not hasattr(mod, n)]
    assert not missing, (
        f"{module_name} 缺少来自 ._common 的公共名字：{missing}。"
        "子模块顶部需要 `from ._common import *`（或显式导入这些名字）。"
    )


def test_caches_are_shared_instances() -> None:
    """desc_cache / mol_read_cache 必须是同一个对象，否则缓存互不相通。"""
    from chem.openbabel_utils import _cache as cache_mod
    from chem.openbabel_utils import _common as common
    from chem.openbabel_utils import _descriptors as desc_mod
    from chem.openbabel_utils import _io as io_mod

    assert desc_mod.desc_cache is common.desc_cache
    assert io_mod.mol_read_cache is common.mol_read_cache
    assert cache_mod.desc_cache is common.desc_cache
    assert cache_mod.mol_read_cache is common.mol_read_cache


def test_public_api_is_importable_without_openbabel() -> None:
    """包级 API 在任何环境下都要能导入（内部对 OpenBabel 缺失做降级）。"""
    import chem.openbabel_utils as ob_utils

    for fn in (
        "convert_file", "generate_from_smiles", "optimize_geometry",
        "calculate_descriptors", "analyze_formula", "smiles_to_inchikey",
        "batch_inchikey", "substructure_search", "similarity_search",
        "tanimoto", "compute_fingerprint", "render_png_2d",
        "analyze_chirality", "invert_enantiomer", "protonate_ph",
        "split_multi_sdf", "merge_to_sdf", "align_molecules",
        "check_openbabel", "check_openbabel_simple", "get_supported_formats",
        "clear_caches", "cache_stats",
    ):
        assert callable(getattr(ob_utils, fn)), f"chem.openbabel_utils.{fn} 不可调用"


def test_clear_caches_returns_counts() -> None:
    """clear_caches 触及 desc_cache / mol_read_cache —— 名字缺失会在这里炸。"""
    from chem.openbabel_utils import cache_stats, clear_caches

    evicted = clear_caches()
    assert isinstance(evicted, tuple) and len(evicted) == 2
    assert all(isinstance(n, int) for n in evicted)
    stats = cache_stats()
    assert stats["descriptors"] == 0 and stats["mol_read"] == 0


def test_manual_obabel_path_roundtrip() -> None:
    """手动 obabel 路径的 set/get —— 名字漏导入时会直接 NameError。"""
    from chem.openbabel_utils import get_manual_obabel_path, set_manual_obabel_path

    original = get_manual_obabel_path()
    try:
        set_manual_obabel_path("/tmp/fake/obabel")
        assert get_manual_obabel_path() == "/tmp/fake/obabel"
        set_manual_obabel_path("")
        assert get_manual_obabel_path() is None
        set_manual_obabel_path(None)
        assert get_manual_obabel_path() is None
    finally:
        set_manual_obabel_path(original)  # 别把测试状态泄漏给其他用例


def test_check_openbabel_never_raises() -> None:
    """环境诊断是状态栏 OB 指示灯的数据源，抛异常会让整块诊断变红。"""
    from chem.openbabel_utils import check_openbabel, check_openbabel_simple

    simple = check_openbabel_simple()
    assert isinstance(simple, tuple) and len(simple) >= 2
    assert check_openbabel() is not None  # 缺 OpenBabel 也要返回结果而不是抛异常


def test_functions_degrade_gracefully_without_pybel() -> None:
    """OpenBabel 缺失时，函数应返回 success=False 而不是抛异常。"""
    from chem.openbabel_utils import _common, smiles_to_inchikey

    if _common.PYBEL_AVAILABLE:
        pytest.skip("已安装 pybel，降级路径不适用")

    r = smiles_to_inchikey("CCO")
    assert isinstance(r, dict)
    assert r.get("success") is False
    assert "message" in r
