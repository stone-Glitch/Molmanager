#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-07 多计算程序日志解析（Gaussian / ORCA / CP2K）· 纯逻辑层

从量子化学程序输出文本中抽取通用字段（能量、方法、基组、是否正常收敛、
引擎类型），统一成同构 dict，供 UI 做多程序结果对照。

只做正则抽取，字段找不到就记 "unknown" / None，绝不伪造。纯 stdlib，
可在沙箱用合成日志单测。
"""
import re
from typing import Any


# ---------- 引擎识别 ----------
def detect_engine(text: str) -> str:
    """按日志特征识别引擎：gaussian / orca / cp2k / unknown。"""
    t = text or ""
    if "Entering Link 1" in t or "Gaussian" in t or "SCF Done" in t:
        return "gaussian"
    if "ORCA" in t and ("FINAL SINGLE POINT ENERGY" in t or "O   R   C   A" in t):
        return "orca"
    if "CP2K" in t or "PROGRAM ENDED AT" in t or "FORCE_EVAL" in t:
        return "cp2k"
    return "unknown"


_GAUSS_ENERGY = re.compile(
    r"SCF Done:\s+E\([^)]*\)\s*=\s*([-+]?\d+\.\d+)", re.IGNORECASE)
_GAUSS_NORM = re.compile(r"Normal termination of Gaussian", re.IGNORECASE)

_ORCA_ENERGY = re.compile(r"FINAL SINGLE POINT ENERGY\s+([-+]?\d+\.\d+)", re.IGNORECASE)
_ORCA_NORM = re.compile(r"ORCA TERMINATED NORMALLY", re.IGNORECASE)

_CP2K_ENERGY = re.compile(
    r"Total FORCE_EVAL \( QS \) energy \[a\.u\.\]\s*=\s*([-+]?\d+\.\d+)", re.IGNORECASE)
_CP2K_NORM = re.compile(r"PROGRAM ENDED AT", re.IGNORECASE)


def _gaussian_method_basis(text: str):
    """
    从 Gaussian 的 ``#`` 路由行提取 (method, basis)。

    Gaussian 路由行形如 ``#p b3lyp/6-31g(d) opt freq`` 或 ``# b3lyp/6-31g* opt``，
    用正则一次性匹配极易被 ``\\S*`` 贪婪吞掉跨 ``/`` 的内容，故改为按行解析：
    取第一个 ``#`` 开头的行，剥掉 ``#`` 与前置旗标（p/n/T 等单字母），
    再找第一个含 ``/`` 的 token，按 ``/`` 切成方法/基组。
    """
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        s = s.lstrip("#").strip()
        toks = s.split()
        # 跳过前置旗标（如 #p / #n / #T 里的 p/n/T）
        while toks and len(toks[0]) == 1:
            toks.pop(0)
        for t in toks:
            if "/" in t:
                m, _, b = t.partition("/")
                if m and b:
                    return m.strip(), b.strip()
    return None


def parse_gaussian(text: str) -> dict[str, Any]:
    res: dict[str, Any] = {"engine": "gaussian", "energy": None,
                           "converged": None, "method": "unknown", "basis": "unknown"}
    m = _GAUSS_ENERGY.search(text)
    if m:
        res["energy"] = float(m.group(1))
    res["converged"] = bool(_GAUSS_NORM.search(text))
    mb = _gaussian_method_basis(text)
    if mb:
        res["method"], res["basis"] = mb
    return res


def parse_orca(text: str) -> dict[str, Any]:
    res: dict[str, Any] = {"engine": "orca", "energy": None,
                           "converged": None, "method": "unknown", "basis": "unknown"}
    m = _ORCA_ENERGY.search(text)
    if m:
        res["energy"] = float(m.group(1))
    res["converged"] = bool(_ORCA_NORM.search(text))
    # ORCA 方法/基组：取含 ! 的关键词行
    for line in text.splitlines():
        if "!" in line:
            parts = line.split("!")
            kw = parts[1] if len(parts) > 1 else line
            toks = kw.split()
            if toks:
                res["method"] = toks[0]
                if len(toks) > 1:
                    res["basis"] = " ".join(toks[1:3])
            break
    return res


def parse_cp2k(text: str) -> dict[str, Any]:
    res: dict[str, Any] = {"engine": "cp2k", "energy": None,
                           "converged": None, "method": "unknown", "basis": "unknown"}
    m = _CP2K_ENERGY.search(text)
    if m:
        res["energy"] = float(m.group(1))
    res["converged"] = bool(_CP2K_NORM.search(text))
    return res


def parse_calc_log(text: str) -> dict[str, Any]:
    """自动识别引擎并解析，返回统一字段 dict。"""
    engine = detect_engine(text)
    if engine == "gaussian":
        return parse_gaussian(text)
    if engine == "orca":
        return parse_orca(text)
    if engine == "cp2k":
        return parse_cp2k(text)
    return {"engine": "unknown", "energy": None, "converged": None,
            "method": "unknown", "basis": "unknown"}


__all__ = ["detect_engine", "parse_gaussian", "parse_orca", "parse_cp2k", "parse_calc_log"]
