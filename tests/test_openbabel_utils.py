#!/usr/bin/env python3
"""chem/openbabel_utils —— 需要真实 OpenBabel 的功能测试。

未装 OpenBabel 时整文件跳过；纯逻辑与命名空间回归见
``test_openbabel_namespace.py``（那个文件任何环境都会跑）。
"""

from __future__ import annotations

import importlib.util

import pytest

# 收集阶段就跳过：模块级 from chem.openbabel_utils import ... 会触发
# openbabel 的真实导入，光用 usefixtures 拦不住。
if not (importlib.util.find_spec("openbabel") or importlib.util.find_spec("pybel")):
    pytest.skip(
        "需要 OpenBabel 的 Python 绑定（pybel）：conda install -c conda-forge openbabel",
        allow_module_level=True,
    )

from chem.openbabel_utils import (  # noqa: E402
    analyze_chirality,
    calculate_descriptors,
    compute_fingerprint,
    invert_enantiomer,
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
    assert d["num_atoms"] == 6  # SMILES 读入不显式加氢：6 个重原子
    assert d["heavy_atoms"] == 6
    assert d["rings"] == 1  # 一个苯环
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
    assert d["num_atoms"] == 13  # 9 C + 4 O（H 为隐式氢）
    # 以下四项是修复前恒为 0 的指标
    assert d["logP"] == pytest.approx(1.31, abs=0.1)
    assert d["tpsa"] == pytest.approx(63.6, abs=1.0)
    assert d["hbd"] == 1
    assert d["hba"] == 4
    assert d["rings"] == 1  # 一个苯环
    assert d["rotors"] == 3


def test_descriptors_of_missing_file(tmp_path) -> None:
    r = calculate_descriptors(str(tmp_path / "nope.mol"))
    # 允许 success=False，但绝不能抛异常
    assert isinstance(r, dict)
    assert "success" in r


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


# ---------------------------------------------------------------- 手性分析 / 对映体反转
@pytest.fixture()
def _writable_base(tmp_path):
    """把 ob_utils 输出护栏指到 tmp_path，测完还原（invert_enantiomer 的
    输出路径默认必须落在可信根内）。"""
    from chem.openbabel_utils._cli import set_default_base_dir

    set_default_base_dir(tmp_path)
    yield tmp_path
    set_default_base_dir(None)


def _chiral_file(tmp_path, smiles: str = "C[C@H](O)C(=O)O") -> str:
    """含 1 个手性中心的 (R)-乳酸。SMILES 的 @/@@ 自带立体标注，无需 3D 坐标。"""
    p = tmp_path / "lactic.smi"
    p.write_text(smiles + "\n", encoding="utf-8")
    return str(p)


def test_analyze_chirality_detects_lactic_center(tmp_path) -> None:
    r = analyze_chirality(_chiral_file(tmp_path))
    assert r["success"] is True, r
    assert r["n_centers"] == 1
    c = r["centers"][0]
    assert c["idx_1based"] == 2  # C[C@H]... 的第 2 个原子是手性中心
    assert c["symbol"] == "C"
    # OpenBabel 3.1 无 CIP 描述符：label 诚实保持 "?"，不谎报 R/S
    assert c["label"] == "?"
    assert r["has_unknown"] is True


def test_analyze_chirality_achiral_molecule(tmp_path) -> None:
    p = tmp_path / "benzene.smi"
    p.write_text("c1ccccc1\n", encoding="utf-8")
    r = analyze_chirality(str(p))
    assert r["success"] is True
    assert r["n_centers"] == 0


def test_analyze_chirality_bad_file_is_dict_not_raise(tmp_path) -> None:
    p = tmp_path / "bad.smi"
    p.write_text("###garbage###\n", encoding="utf-8")
    r = analyze_chirality(str(p))
    assert isinstance(r, dict)
    assert r.get("success") is False


def test_invert_enantiomer_flips_winding(tmp_path, _writable_base) -> None:
    """C[C@H] ↔ C[C@@H]：拓扑 winding 翻转固化（曾实测通过的关键链路）。"""
    src = _chiral_file(tmp_path)
    out = str(tmp_path / "inv.smi")
    r = invert_enantiomer(src, out)
    assert r["success"] is True, r
    assert r["n_flipped"] == 1
    smiles = open(out, encoding="utf-8").read().strip().split()[0]
    assert smiles == "C[C@@H](O)C(=O)O"
    # 对映体仍是手性分子：中心数不变，winding 已反向
    r2 = analyze_chirality(out)
    assert r2["success"] is True and r2["n_centers"] == 1


def test_invert_enantiomer_rejects_outside_base_dir(tmp_path) -> None:
    """护栏回归：默认可信根外的输出路径必须被拒（commonpath 判定）。"""
    src = _chiral_file(tmp_path)
    outside = tmp_path.parent / "outside_inv.smi"
    r = invert_enantiomer(src, str(outside))
    assert r["success"] is False
    assert "输出路径非法" in r["message"]
