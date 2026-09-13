#!/usr/bin/env python3
"""
上传文件管理 - 把用户上传的分子文件收进进程内受控临时根目录，并以 file_id 引用。

设计目标
--------
1. **框架无关**：本模块不 import fastapi / starlette，纯路径与 IO 逻辑，可直接单测。
2. **纵深防御**（防目录穿越 / 越界写入 / symlink 穿透）：
   - ① 原始文件名先经 ``Path(name).name`` 剥掉任何目录部分，再做扩展名白名单；
   - ② 落盘名 = ``f"{uuid4().hex}_{净化名}"``，**目录部分完全由服务端构造**，
        用户输入永远不会进入目录拼接；
   - ③ 写入前用 ``resolve_secure_output_path(..., base_dir=UPLOAD_ROOT, allow_outside=False)``
        复检，任何越界 / 符号链接都会被拒；
   - ④ 读取时 ``resolve_file_id`` 同样先拒绝 ``..`` / 路径分隔符 / 绝对路径，
        拼成**绝对路径**后交给 ``resolve_secure_input_file(..., allow_outside=False)``。
3. **自动清理**：上传根用 ``make_temp_dir`` 创建，随进程退出（atexit）统一删除。

⚠️ 实测坑（务必保留本注释）
    ``resolve_secure_input_file`` 对**相对路径按 CWD 解析**，而不是按 ``base_dir``。
    因此 ``resolve_file_id`` 必须先把 file_id 拼成**绝对路径**再校验，否则白名单校验
    会因为路径落在 CWD 下而形同虚设。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from utils.path_utils import (
    make_temp_dir,
    resolve_secure_input_file,
    resolve_secure_output_path,
)

#: 上传根目录（进程内单例，atExit 自动清理）。
UPLOAD_ROOT: Path = Path(make_temp_dir("mm_upload_"))

#: 单文件大小上限（50 MiB）。
MAX_BYTES: int = 50 * 1024 * 1024

#: 允许上传的扩展名白名单（小写，含点）。
ALLOWED_EXT: frozenset[str] = frozenset({".xyz", ".mol", ".sdf", ".pdb", ".smi", ".mol2"})

#: 扩展名缺失 / 不在白名单时的兜底原名。
_FALLBACK_NAME = "upload"


class UploadError(ValueError):
    """上传相关的可预期错误（映射为 HTTP 400）。"""


def upload_root() -> Path:
    """返回上传根目录，必要时（被外部删除后）重建。"""
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return UPLOAD_ROOT


def sanitize_name(name: str | None) -> str:
    """
    净化原始上传文件名。

    - 用 ``Path(name).name`` 剥掉任何目录成分（``../../etc/passwd`` → ``passwd``）；
    - 空 / 纯目录名 → 兜底为 ``upload``；
    - 扩展名必须命中 ``ALLOWED_EXT``，否则抛 ``UploadError``。

    返回：可直接拼进落盘名的相对文件名（不含任何路径分隔符）。
    """
    if not name:
        raise UploadError(f"缺少文件名，需为以下扩展名之一：{_ext_list()}")
    # Path().name 会剥掉 '/' 与 '\' 分隔的目录前缀（Windows 反斜杠在 POSIX 上不会被剥，
    # 故额外做一次手工替换，保证跨平台一致）。
    candidate = Path(str(name).replace("\\", "/")).name.strip()
    if not candidate or candidate in {".", ".."}:
        raise UploadError(f"文件名非法，需为以下扩展名之一：{_ext_list()}")
    # ⚠️ ``Path('.xyz').suffix`` 是 ``''``（被当成纯后缀名而非带扩展名的文件），
    # 因此这里用字符串后缀判断，不能用 Path.suffix。
    lowered = candidate.lower()
    ext = next((e for e in sorted(ALLOWED_EXT, key=len, reverse=True) if lowered.endswith(e)), "")
    if not ext:
        raise UploadError(f"不支持的文件类型，仅支持：{_ext_list()}")
    ext = candidate[len(candidate) - len(ext) :]
    # 兜底：文件名主体为空（例如 ".xyz"）时补前缀。
    stem = candidate[: len(candidate) - len(ext)]
    if not stem:
        candidate = f"{_FALLBACK_NAME}{ext}"
    return candidate


def _ext_list() -> str:
    return ", ".join(sorted(ALLOWED_EXT))


def save_upload(original_name: str | None, data: bytes) -> str:
    """
    把上传内容落盘到上传根目录，返回 ``file_id``。

    file_id 形如 ``<uuid4hex>_<净化名>``，目录部分完全由服务端生成。
    """
    if data is None:
        raise UploadError("上传内容为空")
    if len(data) > MAX_BYTES:
        raise UploadError(f"文件过大：{len(data)} 字节，上限 {MAX_BYTES} 字节（50 MiB）")

    safe = sanitize_name(original_name)
    file_id = f"{uuid4().hex}_{safe}"

    root = upload_root()
    # ③ 写前复检：越界 / symlink 一律拒绝（此处 raw 已是纯文件名，越界不可能，
    #    但保留防线以覆盖 UPLOAD_ROOT 自身被换成 symlink 的情况）。
    target = resolve_secure_output_path(
        f"{root}{os.sep}{file_id}",
        base_dir=root,
        allow_outside=False,
        create_parent=False,
    )
    with open(target, "wb") as fh:
        fh.write(data)
    return file_id


def resolve_file_id(file_id: str | None) -> Path:
    """
    把 ``file_id`` 解析为上传根目录内的真实文件路径。

    抛出 ``UploadError``：file_id 为空 / 含 ``..`` / 含路径分隔符 / 是绝对路径 /
    文件不存在 / 越出上传根 / 落在符号链接上。
    """
    if not file_id or not str(file_id).strip():
        raise UploadError("缺少 file_id")
    fid = str(file_id).strip()
    # ① 显式拒绝任何「看起来想跑出目录」的输入，给出可读错误而不是让底层报错。
    if fid != Path(fid).name or fid in {".", ".."} or Path(fid).is_absolute():
        raise UploadError(f"非法的 file_id: {file_id!r}")
    if "/" in fid or "\\" in fid:
        raise UploadError(f"非法的 file_id: {file_id!r}")

    root = upload_root()
    # ⚠️ 必须拼成绝对路径：resolve_secure_input_file 对相对路径按 CWD 解析。
    abs_path = root / fid
    try:
        return resolve_secure_input_file(
            str(abs_path),
            base_dir=root,
            allow_outside=False,
        )
    except ValueError as exc:
        raise UploadError(str(exc)) from exc


__all__ = [
    "ALLOWED_EXT",
    "MAX_BYTES",
    "UPLOAD_ROOT",
    "UploadError",
    "resolve_file_id",
    "sanitize_name",
    "save_upload",
    "upload_root",
]
