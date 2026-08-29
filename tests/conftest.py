#!/usr/bin/env python3
"""pytest 公共 fixture。

设计目标：**同一套测试既能跑在完整 conda 环境，也能跑在纯 pip 环境**。
需要 OpenBabel / PSI4 的用例通过 fixture 自动跳过，而不是让整轮测试挂掉。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest


def _module_available(name: str) -> bool:
    """轻量探测模块是否可导入（不真正执行导入，避免 PSI4 那种秒级开销）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


HAS_OPENBABEL = _module_available("openbabel") or _module_available("pybel")
HAS_PYBEL = _module_available("openbabel.pybel") or _module_available("pybel")
HAS_PSI4 = _module_available("psi4")
HAS_FASTAPI = _module_available("fastapi")


@pytest.fixture
def requires_openbabel() -> None:
    """需要 OpenBabel 的用例：缺失时跳过。"""
    if not HAS_OPENBABEL:
        pytest.skip("需要 OpenBabel：conda install -c conda-forge openbabel")


@pytest.fixture
def requires_pybel() -> None:
    """需要 OpenBabel Python 绑定（pybel）的用例：缺失时跳过。"""
    if not HAS_PYBEL:
        pytest.skip("需要 OpenBabel 的 Python 绑定（pybel）")


@pytest.fixture
def requires_psi4() -> None:
    """需要 PSI4 的用例：缺失时跳过。"""
    if not HAS_PSI4:
        pytest.skip("需要 PSI4：conda install -c conda-forge psi4")


@pytest.fixture
def requires_fastapi() -> None:
    """需要 FastAPI 的用例（接口层测试）：缺失时跳过。"""
    if not HAS_FASTAPI:
        pytest.skip('需要接口层依赖：pip install -e ".[api]"')


@pytest.fixture
def work_dir(tmp_path: Path) -> Iterator[str]:
    """一个干净的工作目录（尚未创建任何子结构）。"""
    d = tmp_path / "work"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def smiles_file(tmp_path: Path) -> str:
    """一个含两条常见分子的 .smi 文件：苯 + 阿司匹林。"""
    p = tmp_path / "mols.smi"
    p.write_text("c1ccccc1 benzene\nCC(=O)Oc1ccccc1C(=O)O aspirin\n", encoding="utf-8")
    return str(p)
