#!/usr/bin/env python3
"""core/model Mixin 冒烟测试（Backup / Mapping / History）。

实例化真实 MolManagerModel（work_dir 指向 tmp_path），验证三个 Mixin 的
公开方法在无 GUI 环境下的核心行为。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.model import MolManagerModel


@pytest.fixture()
def model(tmp_path: Path) -> MolManagerModel:
    work = tmp_path / "work"
    return MolManagerModel(work_dir=str(work))


# ---------------------------------------------------------------- MappingMixin
def test_save_mapping_roundtrip(model: MolManagerModel) -> None:
    mapping = {"caffeine": "咖啡因", "water": "水"}
    out = model.save_mapping(mapping, backup=False)
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == mapping
    assert model.mapping == mapping
    assert model._reverse_mapping == {"咖啡因": "caffeine", "水": "water"}


def test_save_mapping_backup_hook(model: MolManagerModel) -> None:
    """旧产物存在时，保存前应生成快照（.backup 目录）。"""
    p1 = model.save_mapping({"a": "甲"}, backup=True)
    p1.write_text('{"old": "旧"}', encoding="utf-8")  # 造一份"旧"产物
    model.save_mapping({"b": "乙"}, backup=True)
    backup_root = model.work_dir / ".backup"
    assert backup_root.is_dir()
    assert any(backup_root.iterdir())  # 至少有一份快照


def test_parse_mapping_file_tsv(model: MolManagerModel, tmp_path: Path) -> None:
    tsv = tmp_path / "map.tsv"
    tsv.write_text("English\tChinese\nwater\t水\nmethanol\t甲醇\n", encoding="utf-8")
    mapping, info = model.parse_mapping_file(tsv)
    assert mapping == {"water": "水", "methanol": "甲醇"}
    assert info["count"] == 2 and info["dup_eng"] == 0 and info["dup_chn"] == 0


def test_parse_mapping_file_conflicts(model: MolManagerModel, tmp_path: Path) -> None:
    """S-06 科学红线：中文冲突必须被检测出来；英文重复后者静默丢弃。"""
    tsv = tmp_path / "conflict.tsv"
    tsv.write_text(
        "English\tChinese\n"
        "water\t水\n"
        "h2o\t水\n"       # 中文「水」与 water 冲突（都映射水）
        "water\t agua\n"  # 英文重复：此条被丢弃
        "water2\t水\n",   # 中文再次冲突
        encoding="utf-8",
    )
    mapping, info = model.parse_mapping_file(tsv)
    assert mapping == {"water": "水", "h2o": "水", "water2": "水"}  # 3 个不同英文全进
    assert info["dup_eng"] == 1  # water 重复出现
    assert info["dup_chn"] == 2  # 「水」给了 3 个英文名 → 2 次冲突
    assert info["chn_conflicts"] == [("水", "water", "h2o"), ("水", "water", "water2")]


def test_parse_mapping_file_errors(model: MolManagerModel, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        model.parse_mapping_file(tmp_path / "nope.tsv")
    empty = tmp_path / "empty.tsv"
    empty.write_text("只有一行\n", encoding="utf-8")
    with pytest.raises(ValueError):
        model.parse_mapping_file(empty)


def test_load_mapping_file_sets_state(model: MolManagerModel, tmp_path: Path) -> None:
    tsv = tmp_path / "map.tsv"
    tsv.write_text("English\tChinese\nwater\t水\n", encoding="utf-8")
    info = model.load_mapping_file(tsv)
    assert info["count"] == 1
    assert model.mapping == {"water": "水"}
    assert model.mapping_source_path == tsv  # C9：记录来源供快照覆盖


# ---------------------------------------------------------------- HistoryMixin
def test_add_history_and_undo_rename(model: MolManagerModel, tmp_path: Path) -> None:
    src = model.work_dir / "old.xyz"
    dst = model.work_dir / "new.xyz"
    src.write_text("data", encoding="utf-8")
    src.rename(dst)
    model._add_history("rename", [(str(src), str(dst))], "重命名演示")
    assert model.can_undo() is True
    assert model.can_redo() is False
    assert model.undo_last() is True
    assert src.exists() and not dst.exists()
    assert model.can_undo() is False and model.can_redo() is True
    r = model.redo_last()
    assert r["success_count"] == 1  # redo_last 返回统计 dict
    assert dst.exists() and not src.exists()


def test_undo_missing_file_reports_not_crash(model: MolManagerModel) -> None:
    model._add_history("rename", [("/nonexistent/a.xyz", "/nonexistent/b.xyz")], "坏历史")
    # 目标不存在 → 撤销失败但不抛异常
    assert model.undo_last() is False


def test_history_persistence(tmp_path: Path) -> None:
    """D-06：历史落盘 .history/，新实例（重启）可恢复撤销链。"""
    work = tmp_path / "work"
    m1 = MolManagerModel(work_dir=str(work))
    src, dst = work / "a.xyz", work / "b.xyz"
    src.write_text("x", encoding="utf-8")
    src.rename(dst)
    m1._add_history("rename", [(str(src), str(dst))], "重启恢复演示")
    assert m1._history_file_path().exists()
    m2 = MolManagerModel(work_dir=str(work))
    assert m2.can_undo() is True
    assert m2.undo_last() is True
    assert src.exists()


def test_history_snapshot(model: MolManagerModel, tmp_path: Path) -> None:
    src = model.work_dir / "x.xyz"
    dst = model.work_dir / "y.xyz"
    src.write_text("d", encoding="utf-8")
    src.rename(dst)
    model._add_history("rename", [(str(src), str(dst))])
    snap = model.get_history_snapshot()
    assert len(snap) == 1 and snap[0]["type"] == "rename"


def test_undo_until(model: MolManagerModel, tmp_path: Path) -> None:
    files = []
    for i in range(3):
        s = model.work_dir / f"f{i}.xyz"
        d = model.work_dir / f"g{i}.xyz"
        s.write_text(str(i), encoding="utf-8")
        s.rename(d)
        model._add_history("rename", [(str(s), str(d))])
        files.append((s, d))
    r = model.undo_until(0)  # 撤销到 history 剩 0 条 → 全部 3 条撤销
    assert r["total_success"] == 3 and r["steps"] == 3
    for s, d in files:
        assert s.exists() and not d.exists()


# ---------------------------------------------------------------- BackupMixin
def test_backup_manager_property(model: MolManagerModel) -> None:
    mgr = model.backup_manager
    assert mgr is not None
    assert mgr.backup_root == model.work_dir / ".backup"


def test_configure_backup(model: MolManagerModel) -> None:
    model.configure_backup({"enabled": False, "keep_per_type": 3, "max_file_mb": 8})
    assert model._backup_enabled is False
    assert model._backup_keep == 3
    assert model._backup_max_mb == 8
    mgr = model.backup_manager
    assert mgr.enabled is False and mgr.keep_per_type == 3
    # 垃圾配置 → 回落默认
    model.configure_backup({"keep_per_type": "abc"})
    assert model._backup_keep == 10


def test_create_backup_snapshot(model: MolManagerModel, tmp_path: Path) -> None:
    f = tmp_path / "映射.json"
    f.write_text("{}", encoding="utf-8")
    meta = model.create_backup_snapshot("mapping", [f], "测试快照")
    assert meta is not None and meta.file_count == 1


def test_default_mapping_path(model: MolManagerModel) -> None:
    assert model.default_mapping_path() == model.work_dir / "分子命名映射.json"
