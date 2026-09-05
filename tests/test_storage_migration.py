"""core.storage_sqlite 迁移机制（PRAGMA user_version）回归测试。

覆盖四条路径：
- 全新库：直接标记为当前版本，三张表齐备
- 史前库（有表、无版本标记）：补标版本且数据保留
- 旧版本库：注册的迁移按序执行、版本推进
- 迁移失败：回滚、版本号不推进、原数据完好（下次启动可重试）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import core.storage_sqlite as storage_mod
from core.storage_sqlite import _SCHEMA_VERSION, Storage


def _user_version(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_fresh_db_marked_current_version(tmp_path: Path):
    db = tmp_path / "fresh.sqlite"
    with Storage(db) as st:
        v = st._conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == _SCHEMA_VERSION
        tables = {r[0] for r in st._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"molecule", "mapping", "calc_result"} <= tables


def test_prehistoric_db_marked_and_data_kept(tmp_path: Path):
    """1.0 时代建的库没有 user_version 标记：打开后补标版本，数据不丢。"""
    db = tmp_path / "prehistoric.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE molecule (
            name TEXT PRIMARY KEY, formula TEXT, smiles TEXT, inchi TEXT,
            mol_file TEXT, xyz_file TEXT, source_path TEXT, tags TEXT,
            created_at TEXT, extra TEXT
        );
        """
    )
    conn.execute("INSERT INTO molecule (name, formula) VALUES ('h2o', 'H2O')")
    conn.commit()
    conn.close()
    assert _user_version(db) == 0  # 无版本标记

    with Storage(db) as st:
        rec = st.get_molecule("h2o")
        assert rec is not None and rec.formula == "H2O"
        v = st._conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == _SCHEMA_VERSION
    assert _user_version(db) == _SCHEMA_VERSION  # 落盘到文件


def test_pending_migration_applies_and_bumps_version(tmp_path: Path, monkeypatch):
    """模拟未来升级：把版本注册表临时改为 1→2（molecule 加列），验证迁移执行。"""
    db = tmp_path / "v1.sqlite"
    with Storage(db):
        pass  # 先造一个当前版本（=1）的库
    assert _user_version(db) == 1

    monkeypatch.setattr(storage_mod, "_SCHEMA_VERSION", 2)

    def _add_cas_column(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE molecule ADD COLUMN cas TEXT")

    monkeypatch.setattr(
        storage_mod,
        "_MIGRATIONS",
        {1: (2, "molecule 增加 cas 列", _add_cas_column)},
    )

    with Storage(db) as st:
        v = st._conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == 2
        cols = {r[1] for r in st._conn.execute("PRAGMA table_info(molecule)")}
        assert "cas" in cols
    assert _user_version(db) == 2


def test_failed_migration_rolls_back_and_keeps_version(tmp_path: Path, monkeypatch):
    """迁移抛错：回滚、版本号不推进、原表数据完好；结构兜底脚本仍补齐缺失表。"""
    db = tmp_path / "v1.sqlite"
    with Storage(db) as st:
        st._conn.execute("INSERT INTO mapping (eng, chn) VALUES ('water', '水')")
        st._conn.commit()
    assert _user_version(db) == 1

    monkeypatch.setattr(storage_mod, "_SCHEMA_VERSION", 2)

    def _boom(_conn: sqlite3.Connection) -> None:
        # 对不存在的表做 ALTER，必然抛 sqlite3.OperationalError
        _conn.execute("ALTER TABLE not_a_table ADD COLUMN x TEXT")

    monkeypatch.setattr(
        storage_mod,
        "_MIGRATIONS",
        {1: (2, "注定失败的迁移", _boom)},
    )

    with Storage(db) as st:  # 打开不应抛异常（防御式容错）
        assert _user_version(db) == 1  # 版本号保持旧值
        # 原数据完好
        rows = st.list_mappings()
        assert any(m.eng == "water" and m.chn == "水" for m in rows)


def test_migration_skipped_when_no_step_registered(tmp_path: Path, monkeypatch):
    """版本落后但注册表缺步骤：给出告警、跳过升级、不炸。"""
    db = tmp_path / "v1.sqlite"
    with Storage(db):
        pass
    monkeypatch.setattr(storage_mod, "_SCHEMA_VERSION", 3)
    monkeypatch.setattr(storage_mod, "_MIGRATIONS", {})  # 1→3 无步骤

    with Storage(db) as st:
        v = st._conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == 1  # 停在旧版本，等待未来版本补齐迁移
        # 但基本功能可用（结构兜底脚本已跑）
        assert st.get_molecule("nope") is None


@pytest.mark.parametrize("n", [1])
def test_schema_version_is_positive(n: int):
    assert _SCHEMA_VERSION >= n
