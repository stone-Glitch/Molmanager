#!/usr/bin/env python3
"""
SQLite 持久化层（Phase B · 可维护性）。

把分子、映射、计算结果统一存入单个 SQLite 文件，替代原先散落在 JSON / pickle
里的临时存储，便于检索、去重与跨会话复用。

设计约束：
  - 仅用标准库 ``sqlite3``，零额外依赖；不依赖 Tkinter / PSI4 / OpenBabel。
  - 所有公开方法对异常做兜底（返回 ``None`` / ``[]`` 并打印），不向上抛，
    避免存储故障拖垮主流程（与项目既有的防御式容错风格一致）。
  - 线程安全：每个 Storage 实例持有独立连接，``check_same_thread=False`` +
    写入串行化（单连接天然串行）。多进程场景请各自持实例。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

from core.domain import CalcResult, MappingEntry, MoleculeRecord

_logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS molecule (
    name        TEXT PRIMARY KEY,
    formula     TEXT,
    smiles      TEXT,
    inchi       TEXT,
    mol_file    TEXT,
    xyz_file    TEXT,
    source_path TEXT,
    tags        TEXT,            -- JSON 数组
    created_at  TEXT,
    extra       TEXT             -- JSON 对象
);
CREATE TABLE IF NOT EXISTS mapping (
    eng         TEXT PRIMARY KEY,
    chn         TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS calc_result (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    molecule    TEXT,
    calc_type   TEXT,
    method      TEXT,
    basis       TEXT,
    energy      REAL,
    status      TEXT,
    output_path TEXT,
    summary     TEXT,            -- JSON 对象
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_calc_molecule ON calc_result(molecule);
CREATE INDEX IF NOT EXISTS idx_calc_type ON calc_result(calc_type);
"""


class Storage:
    """围绕单个 SQLite 文件的轻量仓储。"""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ---------------- schema ----------------
    def _init_schema(self) -> None:
        try:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error as _e:  # pragma: no cover - 极端环境
            _logger.error("schema init failed: %s", _e)

    # ---------------- molecule ----------------
    def upsert_molecule(self, rec: MoleculeRecord) -> bool:
        try:
            self._conn.execute(
                """INSERT INTO molecule
                   (name, formula, smiles, inchi, mol_file, xyz_file, source_path, tags, created_at, extra)
                   VALUES (:name, :formula, :smiles, :inchi, :mol_file, :xyz_file, :source_path, :tags, :created_at, :extra)
                   ON CONFLICT(name) DO UPDATE SET
                     formula=excluded.formula, smiles=excluded.smiles, inchi=excluded.inchi,
                     mol_file=excluded.mol_file, xyz_file=excluded.xyz_file, source_path=excluded.source_path,
                     tags=excluded.tags, created_at=excluded.created_at, extra=excluded.extra""",
                {
                    "name": rec.name,
                    "formula": rec.formula,
                    "smiles": rec.smiles,
                    "inchi": rec.inchi,
                    "mol_file": rec.mol_file,
                    "xyz_file": rec.xyz_file,
                    "source_path": rec.source_path,
                    "tags": json.dumps(rec.tags, ensure_ascii=False),
                    "created_at": rec.created_at,
                    "extra": json.dumps(rec.extra, ensure_ascii=False),
                },
            )
            self._conn.commit()
            return True
        except sqlite3.Error as _e:
            _logger.error("upsert_molecule failed: %s", _e)
            return False

    def get_molecule(self, name: str) -> MoleculeRecord | None:
        try:
            row = self._conn.execute("SELECT * FROM molecule WHERE name=?", (name,)).fetchone()
            return self._row_to_molecule(row) if row else None
        except sqlite3.Error as _e:
            _logger.error("get_molecule failed: %s", _e)
            return None

    def list_molecules(self, tag: str | None = None) -> list[MoleculeRecord]:
        try:
            if tag:
                rows = self._conn.execute(
                    "SELECT * FROM molecule WHERE tags LIKE ? ORDER BY name",
                    (f'%"{tag}"%',),
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM molecule ORDER BY name").fetchall()
            return [self._row_to_molecule(r) for r in rows]
        except sqlite3.Error as _e:
            _logger.error("list_molecules failed: %s", _e)
            return []

    def delete_molecule(self, name: str) -> bool:
        try:
            self._conn.execute("DELETE FROM molecule WHERE name=?", (name,))
            self._conn.commit()
            return True
        except sqlite3.Error as _e:
            _logger.error("delete_molecule failed: %s", _e)
            return False

    @staticmethod
    def _row_to_molecule(row: sqlite3.Row) -> MoleculeRecord:
        return MoleculeRecord(
            name=row["name"],
            formula=row["formula"],
            smiles=row["smiles"],
            inchi=row["inchi"],
            mol_file=row["mol_file"],
            xyz_file=row["xyz_file"],
            source_path=row["source_path"],
            tags=json.loads(row["tags"] or "[]"),
            created_at=row["created_at"],
            extra=json.loads(row["extra"] or "{}"),
        )

    # ---------------- mapping ----------------
    def upsert_mapping(self, entry: MappingEntry) -> bool:
        try:
            self._conn.execute(
                """INSERT INTO mapping (eng, chn, note, created_at)
                   VALUES (:eng, :chn, :note, :created_at)
                   ON CONFLICT(eng) DO UPDATE SET chn=excluded.chn,
                     note=excluded.note, created_at=excluded.created_at""",
                {
                    "eng": entry.eng,
                    "chn": entry.chn,
                    "note": entry.note,
                    "created_at": entry.created_at,
                },
            )
            self._conn.commit()
            return True
        except sqlite3.Error as _e:
            _logger.error("upsert_mapping failed: %s", _e)
            return False

    def get_mapping(self, eng: str) -> MappingEntry | None:
        try:
            row = self._conn.execute("SELECT * FROM mapping WHERE eng=?", (eng,)).fetchone()
            return MappingEntry(eng=row["eng"], chn=row["chn"], note=row["note"]) if row else None
        except sqlite3.Error as _e:
            _logger.error("get_mapping failed: %s", _e)
            return None

    def list_mappings(self) -> list[MappingEntry]:
        try:
            rows = self._conn.execute("SELECT eng, chn, note FROM mapping ORDER BY eng").fetchall()
            return [MappingEntry(eng=r["eng"], chn=r["chn"], note=r["note"]) for r in rows]
        except sqlite3.Error as _e:
            _logger.error("list_mappings failed: %s", _e)
            return []

    def delete_mapping(self, eng: str) -> bool:
        try:
            self._conn.execute("DELETE FROM mapping WHERE eng=?", (eng,))
            self._conn.commit()
            return True
        except sqlite3.Error as _e:
            _logger.error("delete_mapping failed: %s", _e)
            return False

    # ---------------- calc result ----------------
    def add_calc_result(self, res: CalcResult) -> int | None:
        try:
            cur = self._conn.execute(
                """INSERT INTO calc_result
                   (molecule, calc_type, method, basis, energy, status, output_path, summary, created_at)
                   VALUES (:molecule, :calc_type, :method, :basis, :energy, :status, :output_path, :summary, :created_at)""",
                {
                    "molecule": res.molecule,
                    "calc_type": res.calc_type,
                    "method": res.method,
                    "basis": res.basis,
                    "energy": res.energy,
                    "status": res.status,
                    "output_path": res.output_path,
                    "summary": json.dumps(res.summary, ensure_ascii=False),
                    "created_at": res.created_at,
                },
            )
            self._conn.commit()
            rid = cur.lastrowid
            return int(rid) if rid is not None else None
        except sqlite3.Error as _e:
            _logger.error("add_calc_result failed: %s", _e)
            return None

    def list_calc_results(self, molecule: str | None = None, calc_type: str | None = None) -> list[CalcResult]:
        try:
            sql = "SELECT * FROM calc_result WHERE 1=1"
            params: list[Any] = []
            if molecule is not None:
                sql += " AND molecule=?"
                params.append(molecule)
            if calc_type is not None:
                sql += " AND calc_type=?"
                params.append(calc_type)
            sql += " ORDER BY id DESC"
            rows = self._conn.execute(sql, params).fetchall()
            return [self._row_to_calc(r) for r in rows]
        except sqlite3.Error as _e:
            _logger.error("list_calc_results failed: %s", _e)
            return []

    @staticmethod
    def _row_to_calc(row: sqlite3.Row) -> CalcResult:
        return CalcResult(
            id=row["id"],
            molecule=row["molecule"],
            calc_type=row["calc_type"],
            method=row["method"],
            basis=row["basis"],
            energy=row["energy"],
            status=row["status"],
            output_path=row["output_path"],
            summary=json.loads(row["summary"] or "{}"),
            created_at=row["created_at"],
        )

    # ---------------- lifecycle ----------------
    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *_a: object) -> None:
        self.close()


__all__ = ["Storage"]
