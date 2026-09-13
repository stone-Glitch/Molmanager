#!/usr/bin/env python3
"""阶段3：上传 + 路径安全校验的纯逻辑验收测试（框架无关）。

覆盖 ``api.uploads`` 的三重防线：
  1. 文件名净化（``sanitize_name``）：剥目录、扩展名白名单；
  2. ``file_id`` 解析（``resolve_file_id``）：拒绝 ``..`` / 分隔符 / 绝对路径 / 不存在；
  3. 上传根白名单：越界、符号链接一律拒。

本文件不 import fastapi，缺接口层依赖也能跑。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from api import uploads
from utils.path_utils import resolve_secure_input_file, resolve_secure_output_path


# ---------------------------------------------------------------- sanitize_name
@pytest.mark.parametrize(
    "good,expect",
    [
        ("benzene.xyz", "benzene.xyz"),
        ("mol.mol", "mol.mol"),
        ("data.sdf", "data.sdf"),
        ("protein.pdb", "protein.pdb"),
        ("MOLS.SMI", "MOLS.SMI"),
        ("a.mol2", "a.mol2"),
    ],
)
def test_sanitize_name_accepts_whitelist(good: str, expect: str) -> None:
    assert uploads.sanitize_name(good) == expect


@pytest.mark.parametrize(
    "raw",
    [
        "/tmp/evil.xyz",  # 绝对路径
        "../../etc/passwd.xyz",  # 目录穿越
        "..\\..\\win.xyz",  # Windows 反斜杠穿越
        "sub/dir/mol.xyz",  # 子目录
    ],
)
def test_sanitize_name_strips_directory(raw: str) -> None:
    out = uploads.sanitize_name(raw)
    assert "/" not in out and "\\" not in out
    assert out in {"evil.xyz", "passwd.xyz", "win.xyz", "mol.xyz"}


@pytest.mark.parametrize("bad", ["a.exe", "b.sh", "noext", "", None, ".", "..", "a.", "x.zip"])
def test_sanitize_name_rejects_bad(bad: object) -> None:
    with pytest.raises(uploads.UploadError):
        uploads.sanitize_name(bad)  # type: ignore[arg-type]


def test_sanitize_name_empty_stem_gets_prefix() -> None:
    # ".xyz" 这种「只有扩展名」的输入补兜底主体，避免落盘名只剩 uuid 后缀。
    assert uploads.sanitize_name(".xyz") == "upload.xyz"


# ---------------------------------------------------------------- save_upload
def test_save_upload_returns_uuid_prefixed_id_in_root() -> None:
    fid = uploads.save_upload("benzene.xyz", b"payload")
    assert "_benzene.xyz" in fid
    assert "/" not in fid and "\\" not in fid
    p = uploads.resolve_file_id(fid)
    assert p.read_bytes() == b"payload"
    # 落盘点必须在上传根内
    assert uploads.UPLOAD_ROOT.resolve() in p.parents


def test_save_upload_rejects_oversize() -> None:
    with pytest.raises(uploads.UploadError, match="过大"):
        uploads.save_upload("big.xyz", b"x" * (uploads.MAX_BYTES + 1))


def test_save_upload_rejects_bad_ext() -> None:
    with pytest.raises(uploads.UploadError):
        uploads.save_upload("evil.exe", b"MZ")


def test_save_upload_sanitizes_traversal_name() -> None:
    """穿越名不会写进目录拼接：落盘文件仍在上传根内。"""
    fid = uploads.save_upload("../../etc/passwd.xyz", b"x")
    p = uploads.resolve_file_id(fid)
    assert uploads.UPLOAD_ROOT.resolve() in p.parents
    assert p.name.endswith("passwd.xyz")


# ---------------------------------------------------------------- resolve_file_id
def test_resolve_file_id_happy_path() -> None:
    fid = uploads.save_upload("mol.mol", b"content")
    p = uploads.resolve_file_id(fid)
    assert p.is_file()
    assert p.read_bytes() == b"content"


@pytest.mark.parametrize(
    "bad",
    [
        "../x.xyz",
        "..",
        ".",
        "a/b.xyz",
        "a\\b.xyz",
        "/etc/passwd",
        "",
        "   ",
        None,
    ],
)
def test_resolve_file_id_rejects_traversal_and_absolute(bad: object) -> None:
    with pytest.raises(uploads.UploadError):
        uploads.resolve_file_id(bad)  # type: ignore[arg-type]


def test_resolve_file_id_missing_file() -> None:
    with pytest.raises(uploads.UploadError, match="不存在"):
        uploads.resolve_file_id("deadbeef_nope.xyz")


def test_resolve_file_id_rejects_directory() -> None:
    (uploads.UPLOAD_ROOT / "adir.xyz").mkdir(exist_ok=True)
    with pytest.raises(uploads.UploadError):
        uploads.resolve_file_id("adir.xyz")


def test_resolve_file_id_rejects_symlink_escape(tmp_path: Path) -> None:
    """上传根内的符号链接指向外部 → 必须拒绝（防穿透）。"""
    outside = tmp_path / "outside.xyz"
    outside.write_text("secret")
    link = uploads.UPLOAD_ROOT / "link.xyz"
    if link.exists() or link.is_symlink():
        link.unlink()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台/权限不支持创建符号链接")
    try:
        with pytest.raises(uploads.UploadError):
            uploads.resolve_file_id("link.xyz")
    finally:
        link.unlink(missing_ok=True)


# ---------------------------------------------------------------- 上传根白名单（utils 层面）
def test_resolve_secure_input_file_enforces_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "in.xyz"
    inside.write_text("x")
    outside = tmp_path / "out.xyz"
    outside.write_text("y")

    # 根内：通过
    assert resolve_secure_input_file(str(inside), base_dir=root, allow_outside=False) == inside.resolve()
    # 根外：拒绝
    with pytest.raises(ValueError, match="越出"):
        resolve_secure_input_file(str(outside), base_dir=root, allow_outside=False)


def test_resolve_secure_input_file_relative_resolves_against_cwd(tmp_path: Path, monkeypatch) -> None:
    """守住那个实测坑：相对路径按 CWD 解析，故调用方必须先拼绝对路径。

    这里显式固化行为——如果哪天实现改成按 base_dir 解析，本用例会提醒重新审视
    ``api.uploads.resolve_file_id`` 的绝对路径拼接逻辑。
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "rel.xyz").write_text("rel")
    fake_cwd = tmp_path / "cwd"
    fake_cwd.mkdir()
    monkeypatch.chdir(fake_cwd)

    # 直接传相对路径：会被解析到 CWD 下（不存在）→ 报错而非命中 root。
    with pytest.raises(ValueError, match="不存在"):
        resolve_secure_input_file("rel.xyz", base_dir=root, allow_outside=False)
    # 拼成绝对路径后才命中 root 内的文件。
    got = resolve_secure_input_file(str(root / "rel.xyz"), base_dir=root, allow_outside=False)
    assert got.read_bytes() == b"rel"


def test_resolve_secure_output_path_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        resolve_secure_output_path(str(root / ".." / "evil.xyz"), base_dir=root, allow_outside=False)


def test_upload_root_is_stable_singleton() -> None:
    """上传根是进程内单例（同一目录），且调用 upload_root() 会保证它存在。"""
    assert uploads.upload_root() == uploads.upload_root()
    assert uploads.upload_root().is_dir()
    assert os.path.isdir(os.fspath(uploads.UPLOAD_ROOT))


# ---------------------------------------------------------------- 回归：输出根不得退回 CWD
def test_render_png_2d_output_outside_cwd(tmp_path: Path, monkeypatch) -> None:
    """回归：``render_png_2d`` 的输出目录与 CWD 不同时必须仍然成功。

    历史问题：``_secure_output_path`` 在未显式传 base_dir 时会退回**当前工作目录**
    作为允许根，于是「CWD=仓库根、输出=/tmp/xxx」（Web/测试环境的常态）被判为「越界」，
    表现为渲染静默失败、上层只报「未能生成任何有效帧」，极难定位。
    """
    ob = pytest.importorskip("chem.openbabel_utils")
    xyz = tmp_path / "mol.xyz"
    xyz.write_text("3\nwater\nO 0 0 0\nH 0 0 0.96\nH 0.93 0 0.24\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # 把 CWD 切到别处，制造「输出目录 != CWD」的场景
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    r = ob.render_png_2d(str(xyz), str(out_dir / "frame.png"), width=320, height=240)
    if not r.get("success") and "OpenBabel" in str(r.get("message")):
        pytest.skip("环境缺 OpenBabel 渲染能力")
    assert r["success"] is True, r
    assert (out_dir / "frame.png").is_file()
