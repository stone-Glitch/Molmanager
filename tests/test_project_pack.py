#!/usr/bin/env python3
"""utils/project_pack —— .molproj 打包/解包测试（tmp_path）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from utils.project_pack import (
    MANIFEST_NAME,
    DEFAULT_EXCLUDE_EXTS,
    pack_project,
    read_manifest,
    unpack_project,
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    src = tmp_path / "myproj"
    (src / "sub").mkdir(parents=True)
    (src / "water.xyz").write_text("O 0 0 0\n", encoding="utf-8")
    (src / "sub" / "notes.txt").write_text("hello", encoding="utf-8")
    (src / "junk.pyc").write_text("binary", encoding="utf-8")
    (src / "scratch.tmp").write_text("temp", encoding="utf-8")
    return src


def test_pack_creates_zip_and_manifest(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "proj.molproj"
    manifest = pack_project(str(project), str(out))
    assert out.is_file()
    assert manifest["format"] == "molproj" and manifest["root"] == "myproj"
    paths = [f["path"] for f in manifest["files"]]
    assert "water.xyz" in paths and "sub/notes.txt" in paths
    assert "junk.pyc" not in paths and "scratch.tmp" not in paths  # 默认排除
    assert manifest["file_count"] == len(paths)


def test_pack_extra_exclude(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "proj.molproj"
    manifest = pack_project(str(project), str(out), extra_exclude=["notes.txt"])
    paths = [f["path"] for f in manifest["files"]]
    assert "sub/notes.txt" not in paths


def test_pack_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        pack_project(str(tmp_path / "nope"), str(tmp_path / "x.molproj"))


def test_read_manifest_roundtrip(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "proj.molproj"
    manifest = pack_project(str(project), str(out))
    assert read_manifest(str(out)) == manifest
    assert read_manifest(str(tmp_path / "nope.molproj")) is None
    # 无清单的普通 zip → None
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("a.txt", "x")
    assert read_manifest(str(plain)) is None


def test_unpack_roundtrip(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "proj.molproj"
    pack_project(str(project), str(out))
    dest = tmp_path / "restored"
    r = unpack_project(str(out), str(dest), overwrite=True)
    assert r["extracted"] == 2 and r["skipped"] == 0
    assert (dest / "water.xyz").read_text(encoding="utf-8") == "O 0 0 0\n"
    assert (dest / "sub" / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_unpack_overwrite_semantics(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "proj.molproj"
    pack_project(str(project), str(out))
    dest = tmp_path / "restored"
    dest.mkdir()
    # 预先放一个同名文件（用户数据）→ overwrite=False 必须跳过
    (dest / "water.xyz").write_text("USER DATA", encoding="utf-8")
    r1 = unpack_project(str(out), str(dest), overwrite=False)
    assert r1["extracted"] == 1 and r1["skipped"] == 1
    assert (dest / "water.xyz").read_text(encoding="utf-8") == "USER DATA"  # 未被覆盖
    r2 = unpack_project(str(out), str(dest), overwrite=True)
    assert r2["extracted"] == 2
    assert (dest / "water.xyz").read_text(encoding="utf-8") == "O 0 0 0\n"


def test_unpack_invalid_zip_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.molproj"
    bad.write_text("not a zip", encoding="utf-8")
    with pytest.raises(zipfile.BadZipFile):
        unpack_project(str(bad), str(tmp_path / "out"))


def test_manifest_is_inside_zip(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "proj.molproj"
    pack_project(str(project), str(out))
    with zipfile.ZipFile(out) as zf:
        assert MANIFEST_NAME in zf.namelist()
        # 默认排除扩展名表包含 .molproj 自身，防套娃
        assert ".molproj" in DEFAULT_EXCLUDE_EXTS
