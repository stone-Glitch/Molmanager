#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-05 项目打包器 .molproj（纯逻辑层）

把工作目录打包成单个 ``.molproj``（本质是带清单的 ZIP），便于分享/
归档/跨机器迁移；反向可解包还原。清单(molproj.json)记录文件列表、
相对路径、大小、mtime，便于将来做增量校验。

纯 zipfile + json，无 tkinter 依赖，可在沙箱单测（用临时目录）。
"""
import json
import os
import zipfile
from pathlib import Path

MANIFEST_NAME = "molproj.json"
DEFAULT_EXCLUDE_EXTS = (".pyc", ".tmp", ".bak", ".molproj")


def _iter_files(src_dir: Path, exclude_exts: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for root, _dirs, files in os.walk(src_dir):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in exclude_exts:
                continue
            out.append(p)
    return sorted(out)


def pack_project(
    src_dir: str,
    out_zip: str,
    exclude_exts: tuple[str, ...] = DEFAULT_EXCLUDE_EXTS,
    extra_exclude: list[str] | None = None,
) -> dict:
    """
    打包目录为 .molproj（ZIP+清单）。返回清单 dict。

    extra_exclude: 额外按文件名（不含目录）排除的列表。
    """
    src = Path(src_dir)
    if not src.is_dir():
        raise NotADirectoryError(f"源目录不存在: {src_dir}")
    excludes = set(exclude_exts)
    extra = set(extra_exclude or [])

    files = _iter_files(src, tuple(excludes))
    # 先收集条目（不写盘），再一次性写 files + 清单，避免 zip 里出现重复清单条目
    entries = []
    for p in files:
        rel = p.relative_to(src).as_posix()
        if p.name in extra:
            continue
        st = p.stat()
        entries.append({
            "path": rel,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        })
    manifest = {
        "format": "molproj",
        "version": 1,
        "root": src.name,
        "file_count": len(entries),
        "files": entries,
    }
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            rel = p.relative_to(src).as_posix()
            if p.name in extra:
                continue
            zf.write(p, arcname=rel)
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def read_manifest(zip_path: str) -> dict | None:
    """读取 .molproj 里的清单；损坏/缺失返回 None。"""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if MANIFEST_NAME not in zf.namelist():
                return None
            data = zf.read(MANIFEST_NAME).decode("utf-8")
            return json.loads(data)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError):
        return None


def unpack_project(zip_path: str, dest_dir: str, overwrite: bool = False) -> dict:
    """
    解包 .molproj 到 dest_dir。返回 {extracted, skipped, manifest}。

    overwrite=False 时，已存在的文件跳过（skipped），不覆盖用户数据。
    """
    manifest = read_manifest(zip_path)
    if manifest is None:
        raise zipfile.BadZipFile(f"不是有效的 .molproj 文件或清单缺失: {zip_path}")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    extracted, skipped = 0, 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for item in manifest.get("files", []):
            rel = item["path"]
            target = dest / rel
            if target.exists() and not overwrite:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(rel) as src, open(target, "wb") as out:
                out.write(src.read())
            extracted += 1
    return {"extracted": extracted, "skipped": skipped, "manifest": manifest}


__all__ = ["MANIFEST_NAME", "DEFAULT_EXCLUDE_EXTS",
           "pack_project", "read_manifest", "unpack_project"]
