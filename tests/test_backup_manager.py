#!/usr/bin/env python3
"""utils/backup_manager —— 自动备份快照测试（tmp_path，无 Tk 依赖）。

铁律守卫：任何失败只 warning、绝不 raise 到主流程。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.backup_manager import (
    META_FILENAME,
    TRIGGER_MAPPING,
    TRIGGER_PRERESTORE,
    BackupManager,
    SnapshotMeta,
    format_size,
    sanitize_trigger,
    trigger_label,
)


@pytest.fixture()
def mgr(tmp_path: Path) -> BackupManager:
    work = tmp_path / "work"
    work.mkdir()
    return BackupManager(work / ".backup", keep_per_type=10)


@pytest.fixture()
def two_files(tmp_path: Path) -> list[Path]:
    files = []
    for name, text in (("映射.json", '{"a": 1}'), ("mapping.tsv", "M-01\tCaffeine")):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        files.append(p)
    return files


# ------------------------------------------------ 工具函数
def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(None) == "-"
    assert format_size("abc") == "-"
    assert format_size(-5) == "-"
    assert format_size(2 * 1024 * 1024) == "2.0 MB"


def test_sanitize_trigger_and_label() -> None:
    assert sanitize_trigger("Mapping") == "mapping"
    assert sanitize_trigger("a b/c!") == "a_b_c"
    assert sanitize_trigger("") == "misc"
    assert sanitize_trigger(None) == "misc"  # type: ignore[arg-type]
    assert trigger_label("mapping") == "映射表"
    assert trigger_label("custom") == "custom"  # 未知回落原文


def test_snapshot_meta_roundtrip() -> None:
    meta = SnapshotMeta(snapshot_id="20260906_120000_mapping", timestamp="2026-09-06T12:00:00",
                        trigger="mapping", description="测试", files=[{"orig_name": "a.json"}], total_size=10)
    d = meta.to_dict()
    back = SnapshotMeta.from_dict(d)
    assert back is not None and back.snapshot_id == meta.snapshot_id and back.file_count == 1
    assert back.trigger_label == "映射表" and back.size_text == "10 B"
    assert back.display_time().startswith("2026-09-06 12:00:00")
    # 损坏输入 → None，不抛
    assert SnapshotMeta.from_dict("not-dict") is None  # type: ignore[arg-type]
    assert SnapshotMeta.from_dict(None) is None  # type: ignore[arg-type]


# ------------------------------------------------ 创建快照
def test_create_snapshot_basic(mgr: BackupManager, two_files: list[Path]) -> None:
    meta = mgr.create_snapshot(TRIGGER_MAPPING, two_files, "保存映射表前")
    assert meta is not None
    assert meta.file_count == 2 and meta.trigger == "mapping"
    snap_dir = mgr.get_snapshot_dir(meta.snapshot_id)
    assert snap_dir is not None
    assert (snap_dir / META_FILENAME).is_file()
    # 副本内容一致
    assert json.loads((snap_dir / "映射.json").read_text(encoding="utf-8")) == {"a": 1}
    # 列表可读
    snaps = mgr.list_snapshots()
    assert [m.snapshot_id for m in snaps] == [meta.snapshot_id]


def test_create_snapshot_skips_missing_files(mgr: BackupManager, tmp_path: Path) -> None:
    # 不存在的路径静默跳过；全部不存在 → None
    assert mgr.create_snapshot("export", [tmp_path / "nope.json"]) is None


def test_create_snapshot_disabled(mgr: BackupManager, two_files: list[Path]) -> None:
    mgr.configure(enabled=False)
    assert mgr.create_snapshot("export", two_files) is None


def test_create_snapshot_respects_max_bytes(tmp_path: Path, two_files: list[Path]) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 4096)
    mgr = BackupManager(tmp_path / ".backup", max_file_bytes=1024)
    meta = mgr.create_snapshot("export", [*two_files, big], "含大文件")
    assert meta is not None
    names = [f["orig_name"] for f in meta.files]
    assert "big.bin" not in names and "映射.json" in names  # 大文件被跳过


def test_create_snapshot_never_raises(mgr: BackupManager) -> None:
    """铁律：传入垃圾输入也不允许抛异常。"""
    assert mgr.create_snapshot(123, 456) is None  # type: ignore[arg-type]
    assert mgr.create_snapshot(None, None, None) is None  # type: ignore[arg-type]


# ------------------------------------------------ 列举 / 过滤 / 预览
def test_list_snapshots_filter_and_order(mgr: BackupManager, two_files: list[Path]) -> None:
    m1 = mgr.create_snapshot("mapping", two_files, "第一")
    m2 = mgr.create_snapshot("mapping", two_files, "第二")
    assert m1 and m2
    assert [m.trigger for m in mgr.list_snapshots("mapping")] == ["mapping", "mapping"]
    # 倒序：同类型两次创建，最新的在前（同秒冲突后缀参与排序键）
    assert mgr.list_snapshots()[0].snapshot_id == m2.snapshot_id
    assert mgr.list_snapshots("export") == []  # 类型过滤


def test_list_snapshots_total_size(mgr: BackupManager, two_files: list[Path]) -> None:
    mgr.create_snapshot("mapping", two_files)
    assert mgr.total_size() > 0


def test_list_skips_corrupt_meta(mgr: BackupManager, two_files: list[Path]) -> None:
    meta = mgr.create_snapshot("mapping", two_files)
    assert meta is not None
    snap_dir = mgr.get_snapshot_dir(meta.snapshot_id)
    assert snap_dir is not None
    (snap_dir / META_FILENAME).write_text("{broken json", encoding="utf-8")
    assert mgr.list_snapshots() == []


def test_preview_snapshot(mgr: BackupManager, two_files: list[Path]) -> None:
    meta = mgr.create_snapshot("mapping", two_files)
    assert meta is not None
    rows = mgr.preview_snapshot(meta.snapshot_id)
    assert len(rows) == 2
    row = rows[0]
    assert row["stored_exists"] is True
    assert row["exists_now"] is True  # 原文件还在
    assert row["size_text"].endswith("B")
    assert mgr.preview_snapshot("20260906_999999_misc") == []  # 不存在


# ------------------------------------------------ 回滚
def test_restore_snapshot_to_target_dir(mgr: BackupManager, two_files: list[Path]) -> None:
    meta = mgr.create_snapshot("mapping", two_files, "回滚源")
    assert meta is not None
    dest = mgr.backup_root.parent / "restored"
    restored, errors = mgr.restore_snapshot(meta.snapshot_id, target_dir=dest, pre_snapshot=False)
    assert errors == [] and restored == 2
    assert json.loads((dest / "映射.json").read_text(encoding="utf-8")) == {"a": 1}


def test_restore_snapshot_prerestore_safety_net(mgr: BackupManager, two_files: list[Path]) -> None:
    """回滚前自动为将被覆盖的现存文件建 prerestore 快照。"""
    meta = mgr.create_snapshot("mapping", two_files)
    assert meta is not None
    # 修改原文件，让回滚必然发生覆盖
    two_files[0].write_text('{"a": 999}', encoding="utf-8")
    restored, errors = mgr.restore_snapshot(meta.snapshot_id, pre_snapshot=True)
    assert errors == [] and restored == 2
    assert json.loads(two_files[0].read_text(encoding="utf-8")) == {"a": 1}  # 已回滚
    pre = mgr.list_snapshots(TRIGGER_PRERESTORE)
    # 两个原位置文件都在（都会被回滚覆盖）→ 保险快照记录全部 2 份
    assert len(pre) == 1 and pre[0].file_count == 2


def test_restore_snapshot_missing(mgr: BackupManager) -> None:
    restored, errors = mgr.restore_snapshot("20260906_999999_misc", pre_snapshot=False)
    assert restored == 0 and errors


# ------------------------------------------------ 清理
def test_prune_keeps_latest(mgr: BackupManager, two_files: list[Path]) -> None:
    for _ in range(3):
        mgr.create_snapshot("mapping", two_files)
    assert len(mgr.list_snapshots()) == 3  # keep_per_type=10 → 不清理
    mgr.configure(keep_per_type=2)
    assert mgr.prune("mapping") == 1  # 手动清理删最老 1 份
    assert len(mgr.list_snapshots()) == 2


def test_create_snapshot_auto_prunes(tmp_path: Path, two_files: list[Path]) -> None:
    """create_snapshot 末尾自动 prune：第 3 份落地后超额的第 1 份立即被清。"""
    mgr = BackupManager(tmp_path / ".backup", keep_per_type=2)
    for _ in range(3):
        mgr.create_snapshot("mapping", two_files)
    assert len(mgr.list_snapshots()) == 2


def test_delete_snapshot_rejects_traversal(mgr: BackupManager) -> None:
    assert mgr.delete_snapshot("../escape") is False
    assert mgr.delete_snapshot("") is False
    assert mgr.get_snapshot_dir("../escape") is None  # 越界路径拒绝


def test_clear_all(mgr: BackupManager, two_files: list[Path]) -> None:
    for _ in range(2):
        mgr.create_snapshot("mapping", two_files)
    assert mgr.clear_all() == 2
    assert mgr.list_snapshots() == []
    assert mgr.total_size() == 0
