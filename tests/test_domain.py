#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/domain.py —— 领域数据结构的序列化契约。

这些 dataclass 是持久化层（SQLite）与 UI 之间唯一的「形状约定」，
往返（to_dict / from_dict）一旦被破坏，映射与历史记录就会静默丢失字段。
"""

from __future__ import annotations

from core.domain import CalcResult, MappingEntry, MoleculeRecord


# ---------------------------------------------------------------- MoleculeRecord
def test_molecule_record_roundtrip() -> None:
    rec = MoleculeRecord(
        name="aspirin",
        formula="C9H8O4",
        smiles="CC(=O)Oc1ccccc1C(=O)O",
        inchi="InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
        mol_file="mols/aspirin.mol",
        xyz_file="mols/aspirin.xyz",
        source_path="/tmp/aspirin.mol",
        tags=["药物", "芳香"],
        extra={"note": "阿司匹林"},
    )
    back = MoleculeRecord.from_dict(rec.to_dict())
    assert back == rec


def test_molecule_record_ignores_unknown_keys() -> None:
    """旧库多出来的字段不能让反序列化崩掉。"""
    rec = MoleculeRecord.from_dict({"name": "x", "no_such_field": 1, "extra": {"a": 1}})
    assert rec.name == "x"
    assert rec.extra == {"a": 1}
    assert not hasattr(rec, "no_such_field")


def test_molecule_record_tags_accept_comma_string() -> None:
    rec = MoleculeRecord.from_dict({"name": "x", "tags": "药物,芳香,"})
    assert rec.tags == ["药物", "芳香"]


def test_molecule_record_defaults() -> None:
    rec = MoleculeRecord(name="x")
    assert rec.tags == []
    assert rec.extra == {}
    assert rec.created_at  # 自动生成时间戳


# ---------------------------------------------------------------- MappingEntry
def test_mapping_entry_roundtrip() -> None:
    entry = MappingEntry(eng="benzene", chn="苯", note="芳香烃")
    back = MappingEntry.from_dict(entry.to_dict())
    assert back.eng == "benzene"
    assert back.chn == "苯"
    assert back.note == "芳香烃"


def test_mapping_entry_coerces_to_str() -> None:
    entry = MappingEntry.from_dict({"eng": 123, "chn": None})
    assert entry.eng == "123"
    assert entry.chn == "None"


# ---------------------------------------------------------------- CalcResult
def test_calc_result_roundtrip() -> None:
    res = CalcResult(
        id=1,
        molecule="aspirin",
        calc_type="psi4",
        method="B3LYP",
        basis="6-31G*",
        energy=-647.123,
        status="done",
        output_path="out/aspirin.log",
        summary={"homo": -0.25, "lumo": -0.03},
    )
    back = CalcResult.from_dict(res.to_dict())
    assert back == res


def test_calc_result_default_status_is_done() -> None:
    assert CalcResult().status == "done"


def test_calc_result_ignores_unknown_keys() -> None:
    res = CalcResult.from_dict({"molecule": "x", "bogus": 42})
    assert res.molecule == "x"
