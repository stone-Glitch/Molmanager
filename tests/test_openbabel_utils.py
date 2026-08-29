#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chem/openbabel_utils —— 需要真实 OpenBabel 的功能测试。

未装 OpenBabel 时整文件跳过；纯逻辑与命名空间回归见
``test_openbabel_namespace.py``（那个文件任何环境都会跑）。
"""

from __future__ import annotations

import pytest

from chem.openbabel_utils import (
    calculate_descriptors,
    compute_fingerprint,
    similarity_search,
    smiles_to_inchikey,
    substructure_search,
    tanimoto,
)

pytestmark = pytest.mark.usefixtures("requires_pybel")


# ---------------------------------------------------------------- InChIKey
def test_inchikey_of_aspirin() -> None:
    r = smiles_to_inchikey("CC(=O)Oc1ccccc1C(=O)O")
    assert r["success"] is True, r
    assert r["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert r["skeleton_14"] == "BSYNRYMUTXBXSQ"
    assert r["formula"] == "C9H8O4"


def test_inchikey_of_benzene() -> None:
    r = smiles_to_inchikey("c1ccccc1")
    assert r["success"] is True, r
    assert r["inchikey"].startswith("UHOVQNZJYSORNB")


def test_inchikey_rejects_empty_and_garbage() -> None:
    assert smiles_to_inchikey("")["success"] is False
    assert smiles_to_inchikey("   ")["success"] is False
    assert smiles_to_inchikey("not-a-smiles-###")["success"] is False


# ---------------------------------------------------------------- 描述符
def test_descriptors_of_first_molecule(smiles_file: str) -> None:
    """底层实现只读文件的第一条分子（此处为苯 C6H6）。"""
    r = calculate_descriptors(smiles_file)
    assert r["success"] is True, r
    d = r["descriptors"]
    assert d["formula"] == "C6H6"
    assert d["molecular_weight"] == pytest.approx(78.11, abs=0.5)
    assert d["num_atoms"] == 6           # SMILES 读入不显式加氢：6 个重原子
    assert d["heavy_atoms"] == 6
    assert d["rings"] == 1               # 一个苯环
    assert d["bonds"] == 6


def test_descriptors_of_aspirin(tmp_path) -> None:
    """阿司匹林 C9H8O4 的参考值（OpenBabel 官方描述符口径）。

    这几个断言守住的是一个真实事故：pybel.Molecule 在 OB>=3.1 没有
    ``.logP`` / ``.tpsa`` 属性，OBMol 也没有 ``NumHBD()`` / ``NumSSSR()``，
    旧实现靠 except 吞异常 → LogP / TPSA / HBD / HBA / 环数**恒为 0**。
    """
    p = tmp_path / "aspirin.smi"
    p.write_text("CC(=O)Oc1ccccc1C(=O)O aspirin\n", encoding="utf-8")
    r = calculate_descriptors(str(p))
    assert r["success"] is True, r
    d = r["descriptors"]
    assert d["formula"] == "C9H8O4"
    assert d["molecular_weight"] == pytest.approx(180.16, abs=0.1)
    assert d["num_atoms"] == 13          # 9 C + 4 O（H 为隐式氢）
    # 以下四项是修复前恒为 0 的指标
    assert d["logP"] == pytest.approx(1.31, abs=0.1)
    assert d["tpsa"] == pytest.approx(63.6, abs=1.0)
    assert d["hbd"] == 1
    assert d["hba"] == 4
    assert d["rings"] == 1               # 一个苯环
    assert d["rotors"] == 3


def test_descriptors_of_missing_file(tmp_path) -> None:
    r = calculate_descriptors(str(tmp_path / "nope.mol"))
    # 允许 success=False，但绝不能抛异常
    assert isinstance(r, dict) and "success" in r


def test_descriptors_are_cached(smiles_file: str) -> None:
    """同一文件重复计算应命中缓存，不产生新条目。"""
    from chem.openbabel_utils import cache_stats, clear_caches

    clear_caches()
    calculate_descriptors(smiles_file)
    after_first = cache_stats()["descriptors"]
    calculate_descriptors(smiles_file)
    assert cache_stats()["descriptors"] == after_first


# ---------------------------------------------------------------- 子结构检索
def test_substructure_carboxyl() -> None:
    mols = ["CCO", "CC(=O)O", "c1ccccc1C(=O)O", "c1ccccc1"]
    hit = substructure_search("C(=O)O", mols)
    assert "CC(=O)O" in hit
    assert "c1ccccc1C(=O)O" in hit
    assert "CCO" not in hit


def test_substructure_invalid_smarts_returns_empty() -> None:
    assert substructure_search("(((nonsense", ["CCO"]) == []


def test_substructure_skips_unparseable_molecules() -> None:
    """单条坏分子不能让整体检索失败。"""
    hit = substructure_search("C(=O)O", ["###bad###", "CC(=O)O"])
    assert hit == ["CC(=O)O"]


# ---------------------------------------------------------------- 相似性检索
def test_similarity_self_is_one() -> None:
    fp = compute_fingerprint("CC(=O)Oc1ccccc1C(=O)O")
    assert fp is not None
    assert tanimoto(fp, fp) == pytest.approx(1.0, abs=1e-6)


def test_similarity_ranks_same_molecule_first() -> None:
    query = "CC(=O)Oc1ccccc1C(=O)O"
    mols = ["CCO", "c1ccccc1", query]
    hits = similarity_search(query, mols, threshold=0.0)
    assert hits
    assert hits[0][0] == query
    assert hits[0][1] == pytest.approx(1.0, abs=1e-6)


def test_similarity_respects_threshold() -> None:
    hits = similarity_search("c1ccccc1", ["CCO", "CCCCCCCC"], threshold=0.99)
    assert hits == []


def test_similarity_respects_top_n() -> None:
    hits = similarity_search("c1ccccc1", ["c1ccccc1C", "c1ccccc1CC", "c1ccccc1CCC"], top_n=2)
    assert len(hits) == 2


def test_tanimoto_handles_none() -> None:
    assert tanimoto(None, None) == 0.0
