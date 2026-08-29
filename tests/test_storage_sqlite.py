#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/storage_sqlite.py —— SQLite 持久化往返。

只依赖标准库，是 CI 里必跑的一层：映射表丢了用户要重填几百条中文名。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.domain import CalcResult, MappingEntry, MoleculeRecord
from core.storage_sqlite import Storage


@pytest.fixture
def storage(tmp_path: Path):
    db = tmp_path / "molmanager_test.db"
    st = Storage(str(db))
    yield st
    st.close()


# ---------------------------------------------------------------- 分子
def test_upsert_and_get_molecule(storage: Storage) -> None:
    rec = MoleculeRecord(
        name="aspirin", formula="C9H8O4",
        smiles="CC(=O)Oc1ccccc1C(=O)O",
        tags=["药物", "芳香"], extra={"note": "阿司匹林"},
    )
    assert storage.upsert_molecule(rec) is True
    got = storage.get_molecule("aspirin")
    assert got is not None
    assert got.formula == "C9H8O4"
    assert got.smiles == rec.smiles
    # tags / extra 以 JSON 存储，往返后必须还原成原类型
    assert got.tags == ["药物", "芳香"]
    assert got.extra == {"note": "阿司匹林"}


def test_upsert_molecule_is_idempotent(storage: Storage) -> None:
    """同名重复写入应更新而不是新增（ON CONFLICT DO UPDATE）。"""
    storage.upsert_molecule(MoleculeRecord(name="x", formula="C1"))
    storage.upsert_molecule(MoleculeRecord(name="x", formula="C2"))
    assert len(storage.list_molecules()) == 1
    assert storage.get_molecule("x").formula == "C2"  # type: ignore[union-attr]


def test_list_molecules_sorted_by_name(storage: Storage) -> None:
    for n in ("c", "a", "b"):
        storage.upsert_molecule(MoleculeRecord(name=n))
    assert [m.name for m in storage.list_molecules()] == ["a", "b", "c"]


def test_list_molecules_filter_by_tag(storage: Storage) -> None:
    storage.upsert_molecule(MoleculeRecord(name="a", tags=["芳香"]))
    storage.upsert_molecule(MoleculeRecord(name="b", tags=["烷烃"]))
    assert [m.name for m in storage.list_molecules(tag="芳香")] == ["a"]
    assert len(storage.list_molecules()) == 2


def test_tag_filter_does_not_cross_match(storage: Storage) -> None:
    """tag 过滤走 JSON 精确匹配，"芳香" 不能命中 "非芳香"。"""
    storage.upsert_molecule(MoleculeRecord(name="a", tags=["非芳香"]))
    assert [m.name for m in storage.list_molecules(tag="芳香")] == []


def test_get_missing_molecule_returns_none(storage: Storage) -> None:
    assert storage.get_molecule("nope") is None


def test_delete_molecule(storage: Storage) -> None:
    storage.upsert_molecule(MoleculeRecord(name="x"))
    assert storage.delete_molecule("x") is True
    assert storage.get_molecule("x") is None


# ---------------------------------------------------------------- 映射
def test_upsert_and_get_mapping(storage: Storage) -> None:
    assert storage.upsert_mapping(MappingEntry(eng="benzene", chn="苯", note="芳香烃")) is True
    got = storage.get_mapping("benzene")
    assert got is not None
    assert got.chn == "苯"
    assert got.note == "芳香烃"


def test_upsert_mapping_overwrites_chinese_name(storage: Storage) -> None:
    storage.upsert_mapping(MappingEntry(eng="benzene", chn="苯"))
    storage.upsert_mapping(MappingEntry(eng="benzene", chn="安息油"))
    assert storage.get_mapping("benzene").chn == "安息油"  # type: ignore[union-attr]
    assert len(storage.list_mappings()) == 1


def test_delete_mapping(storage: Storage) -> None:
    storage.upsert_mapping(MappingEntry(eng="x", chn="某"))
    assert storage.delete_mapping("x") is True
    assert storage.get_mapping("x") is None


# ---------------------------------------------------------------- 计算结果
def test_add_and_list_calc_results(storage: Storage) -> None:
    rid = storage.add_calc_result(CalcResult(
        molecule="aspirin", calc_type="psi4", method="B3LYP",
        basis="6-31G*", energy=-647.1, summary={"homo": -0.25},
    ))
    assert rid is not None and rid > 0
    rows = storage.list_calc_results(molecule="aspirin")
    assert len(rows) == 1
    assert rows[0].energy == pytest.approx(-647.1)
    assert rows[0].summary == {"homo": -0.25}


def test_list_calc_results_filter_by_type(storage: Storage) -> None:
    storage.add_calc_result(CalcResult(molecule="m", calc_type="psi4", energy=-1.0))
    storage.add_calc_result(CalcResult(molecule="m", calc_type="descriptor", energy=None))
    assert len(storage.list_calc_results(molecule="m", calc_type="psi4")) == 1
    assert len(storage.list_calc_results(molecule="m")) == 2


def test_calc_results_ordered_newest_first(storage: Storage) -> None:
    storage.add_calc_result(CalcResult(molecule="m", calc_type="t", energy=-1.0))
    storage.add_calc_result(CalcResult(molecule="m", calc_type="t", energy=-2.0))
    rows = storage.list_calc_results(molecule="m")
    assert [r.energy for r in rows] == [pytest.approx(-2.0), pytest.approx(-1.0)]


# ---------------------------------------------------------------- 生命周期
def test_context_manager_closes_connection(tmp_path: Path) -> None:
    with Storage(str(tmp_path / "ctx.db")) as st:
        st.upsert_molecule(MoleculeRecord(name="x"))
        assert st.get_molecule("x") is not None


def test_data_survives_reopen(tmp_path: Path) -> None:
    """真正的持久化验证：关库再开，数据还在。"""
    db = str(tmp_path / "persist.db")
    with Storage(db) as st:
        st.upsert_molecule(MoleculeRecord(name="persisted", formula="H2O"))
    with Storage(db) as st2:
        got = st2.get_molecule("persisted")
        assert got is not None and got.formula == "H2O"
