"""公共命名空间：原 core/model.py 的模块级导入 / 常量 / 辅助函数（拆分时提取）。

所有 mixin 与 __init__ 均从这里 ``import *``，保证方法体里引用的模块级名字
（ob_utils / psi4_utils / logger / SUPPORTED_EXTS / STRUCTURE_EXTS /
enforce_no_symlink_target / win_longpath / get_backup_dir / looks_like_chem_query
/ threading / shutil / json / hashlib / re / datetime / PROTECTED_DIR_NAMES /
is_protected_relpath 等）都能在各自模块内解析到。

注意：本模块**刻意**通过 ``__all__`` 显式导出这些共享名字。否则 ruff 的 F401
会把「仅在 mixin 里经 star-import 使用、本文件未直接引用」的导入（如 threading、
ob_utils、psi4_utils）误判为未使用而删掉，导致运行期 NameError。__all__ 让 ruff
把它们视为「对外导出」从而不再自动清除，是拆分架构下共享命名空间的稳定锚点。
"""

import csv
import hashlib
import json
import os
import re
import shutil
import stat
import threading
from datetime import datetime
from pathlib import Path

import chem.openbabel_utils as ob_utils
import chem.psi4_compute as psi4_utils
from utils.chem_query import looks_like_chem_query
from utils.constants import STRUCTURE_EXTS, SUPPORTED_EXTS
from utils.logger import default_logger as logger
from utils.path_utils import (
    enforce_no_symlink_target,
    get_backup_dir,
    is_windows_junction,
    resolve_secure_output_path,
    win_longpath,
)

_is_windows_junction = is_windows_junction

PROTECTED_DIR_NAMES: frozenset[str] = frozenset({".trash_backup", ".backup", ".preview"})

def is_protected_relpath(rel_path: str) -> bool:
    """相对路径的任一层是否落在受保护目录内。"""
    if not rel_path:
        return False
    try:
        parts = str(rel_path).replace("\\", "/").split("/")
    except Exception:
        return False
    return any(seg in PROTECTED_DIR_NAMES for seg in parts)

def _is_windows_junction(path: str | os.PathLike, *, _raise: bool = False) -> bool:
    """向后兼容包装：参数名 _raise → raise_on_junction"""
    return is_windows_junction(path, raise_on_junction=_raise)

def resolve_secure_output_path_external(
    requested_path,
    *,
    base_dir,
    is_dir: bool = False,
    default_name=None,
    allow_outside: bool = False,
    create_parent: bool = False,
) -> Path:
    """
    向后兼容包装：直接委托给 path_utils.resolve_secure_output_path
    """
    return resolve_secure_output_path(
        requested_path,
        base_dir=base_dir,
        is_dir=is_dir,
        default_name=default_name,
        allow_outside=allow_outside,
        create_parent=create_parent,
    )


__all__ = [
    # 标准库模块 / 名称（仅经 star-import 被 mixin 使用，本文件未直接引用）
    "os", "re", "csv", "json", "stat", "shutil", "hashlib", "threading",
    "datetime", "Path",
    # 第三方 / 项目级单例与常量（model 对外兼容导出）
    "logger", "SUPPORTED_EXTS", "STRUCTURE_EXTS", "looks_like_chem_query",
    "is_windows_junction", "enforce_no_symlink_target",
    "resolve_secure_output_path", "get_backup_dir", "win_longpath",
    "ob_utils", "psi4_utils",
    # 本模块新增的防御性辅助（供 UI / controller 经 star-import 取用）
    "PROTECTED_DIR_NAMES", "is_protected_relpath", "resolve_secure_output_path_external",
]
