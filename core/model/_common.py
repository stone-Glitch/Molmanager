"""公共命名空间：原 core/model.py 的模块级导入 / 常量 / 辅助函数（拆分时提取）。

所有 mixin 与 __init__ 均从这里 ``import *``，保证方法体里引用的模块级名字
（ob_utils / psi4_utils / PROTECTED_DIR_NAMES / is_protected_relpath / logger 等）
都能在各自模块内解析到。
"""

import os

import re

import csv

import json

import stat

import shutil

import hashlib

import threading

from datetime import datetime

from pathlib import Path

from typing import List, Dict, Optional, Tuple

from utils.logger import default_logger as logger

from utils.constants import SUPPORTED_EXTS, STRUCTURE_EXTS

from utils.chem_query import looks_like_chem_query

from utils.path_utils import (
    is_windows_junction,
    enforce_no_symlink_target,
    resolve_secure_output_path,
    get_backup_dir,
    win_longpath,
)

import chem.openbabel_utils as ob_utils

import chem.psi4_compute as psi4_utils

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
