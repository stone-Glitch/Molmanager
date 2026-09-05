"""Psi4 量子化学计算包装。

功能：
- xyz_to_psi4_geom：XYZ 字符串 → psi4.geometry
- optimize_geometry：psi4.optimize，返回 (final_xyz, final_energy)
- single_point：psi4.energy，返回 Eh
- trajectory_energies：对每帧做 single point，返回能量列表
- 全程核心：每次调用后 psi4.core.clean() 释放资源，避免污染下次
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading

# 全局锁：psi4 不是线程安全，同时只能一个计算
_PSI_LOCK = threading.Lock()

# 全局标记：是否已初始化 psi4 全局配置
_PSI_INITIALIZED = False

# 分子优化结果缓存：相同 (xyz, method, basis, charge, mult) → (opt_xyz, energy)
# 同一反应常出现重复分子（如 2H₂O、3O₂），缓存可避免重复 psi4 优化。
# 优化是纯函数（相同输入→相同输出，确定性），缓存安全。
_OPT_CACHE_DIR = os.path.expanduser("~/.cache/quantum_reaction/opt")
_OPT_CACHE = {}  # 进程内缓存（key -> (xyz_str, energy_float)）


def _opt_cache_key(xyz: str, method: str, basis: str, charge: int, mult: int) -> str:
    raw = f"{xyz.strip()}|{method}|{basis}|{charge}|{mult}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _opt_cache_get(key: str):
    if key in _OPT_CACHE:
        return _OPT_CACHE[key]
    p = os.path.join(_OPT_CACHE_DIR, key + ".json")
    if os.path.exists(p):
        try:
            d = json.loads(open(p, encoding="utf-8").read())
            v = (d["xyz"], float(d["energy"]))
            _OPT_CACHE[key] = v
            return v
        except Exception:
            pass
    return None


def _opt_cache_put(key: str, xyz: str, energy: float):
    _OPT_CACHE[key] = (xyz, energy)
    try:
        os.makedirs(_OPT_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_OPT_CACHE_DIR, key + ".json"), "w", encoding="utf-8") as f:
            json.dump({"xyz": xyz, "energy": energy}, f)
    except Exception:
        pass


def _ensure_init():
    global _PSI_INITIALIZED
    if _PSI_INITIALIZED:
        return
    import psi4

    psi4.set_memory("512MB")
    # 多线程加速单帧计算：全局 _PSI_LOCK 保证跨帧不并行，线程只在单帧内
    # 并行，因此对 Flask 单进程单 worker 安全。核数多时显著加速单点/优化。
    try:
        n = min(os.cpu_count() or 4, 8)
        psi4.set_num_threads(n)
    except Exception:
        pass
    # 输出到 stdout，由调用者捕获
    psi4.core.be_quiet()
    _PSI_INITIALIZED = True


def xyz_to_psi4_geom(xyz_str: str, charge: int = 0, multiplicity: int = 1):
    """XYZ 字符串 → psi4 Molecule。自动识别并附加 charge/multiplicity。

    支持两种格式：
      1. 标准 XYZ（"N\\ncomment\\nE x y z..."）→ 前置加 charge/mult 行
      2. 含电荷/自旋多重度行的格式
    """
    import psi4

    lines = [l for l in xyz_str.strip().split("\n")]
    n = int(lines[0].strip())
    body = "\n".join(lines[2 : 2 + n])
    # 显式标注 c1 对称（无对称）：避免优化过程中对称性变化触发
    # "Point group changed" 错误
    geom_str = f"{charge} {multiplicity}\n{body}\nunits angstrom\nsymmetry c1"
    mol = psi4.geometry(geom_str)
    return mol


def _xyz_of_mol(mol) -> str:
    """psi4 Molecule → 标准 XYZ 字符串。

    注意：psi4 Molecule 的 x()/y()/z() 返回 **Bohr** 单位坐标，
    而 XYZ 格式约定为 Å。必须显式换算，否则：
    1) 写出的 xyz 文件被 3Dmol/渲染端按 Å 解读 → 尺寸放大 1.89 倍；
    2) 轨迹帧再喂回 xyz_to_psi4_geom（units angstrom）→ 单点能
       实际算在膨胀 1.89 倍的几何上，能量曲线系统性偏高。
    """
    import psi4

    b2a = psi4.constants.bohr2angstroms
    n = mol.natom()
    lines = [str(n), "psi4"]
    for i in range(n):
        # psi4 symbol() 返回全大写（如 "CL"），XYZ 标准是首字母大写（"Cl"）。
        # 全大写会被 IQmol/OpenBabel 等严格解析器误判，统一 normalize。
        sym = mol.symbol(i).capitalize()
        x, y, z = mol.x(i) * b2a, mol.y(i) * b2a, mol.z(i) * b2a
        lines.append(f"{sym} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines) + "\n"


def optimize_geometry(
    xyz_str: str,
    method: str,
    basis: str,
    charge: int = 0,
    multiplicity: int = 1,
    logger=None,
    cache: bool = True,
    max_retries: int = 2,
) -> tuple:
    """对分子做 psi4.optimize。
    返回 (optimized_xyz_str, energy_Eh, raw_log)。
    失败抛异常（分级重试后仍失败）。

    增强：
    - 缓存：相同 (xyz, method, basis, charge, mult) 直接返回，避免重复优化
    - 单原子降级：孤立原子退化为单点能（几何已最优）
    - 分级重试：优化/SCF 不收敛时逐步放宽 MAXITER / SCF_CONVERGENCE /
      guess=sad / fail_on_maxiter=False，最多重试 max_retries 次
    """

    def log(msg):
        if logger:
            logger(msg)

    import psi4
    from psi4 import driver

    key = _opt_cache_key(xyz_str, method, basis, charge, multiplicity) if cache else None
    if key is not None:
        cached = _opt_cache_get(key)
        if cached is not None:
            log(f"  [psi4] 缓存命中 (mult={multiplicity}) → E={cached[1]:.8f} Eh")
            return cached[0], cached[1], None

    with _PSI_LOCK:
        _ensure_init()
        psi4.core.clean()
        mol = xyz_to_psi4_geom(xyz_str, charge=charge, multiplicity=multiplicity)
        psi4.core.set_output_file("/tmp/psi4_last.out", False)
        # 单原子分子没有可优化的内坐标，optking 会抛 ValueError。
        # 直接退化为单点能（几何已是最优），返回原 XYZ。
        if mol.natom() == 1:
            psi4.set_options(_scf_options(method, multiplicity))
            log("  [psi4] 单原子，跳过几何优化，直接计算单点能")
            e = driver.energy(f"{method}/{basis}", molecule=mol)
            xyz_out = xyz_str.strip() + "\n"
            log(f"  [psi4] single-atom energy = {float(e):.8f} Eh")
            if key is not None:
                _opt_cache_put(key, xyz_out, float(e))
            return xyz_out, float(e), None

        log(f"  [psi4] optimize {method}/{basis}, atoms={mol.natom()}, mult={multiplicity}")
        full_method = f"{method}/{basis}"
        # 分级重试：逐步放宽收敛条件，应对 SCF / 几何优化不收敛
        attempts = [
            {},
            {"GEOM_MAXITER": 400, "guess": "sad", "fail_on_maxiter": False},
            {"GEOM_MAXITER": 800, "guess": "sad", "fail_on_maxiter": False},
        ]
        attempts = attempts[: max(1, 1 + max_retries)]
        last_err = None
        energy = None
        for i, extra in enumerate(attempts):
            try:
                opts = _scf_options(method, multiplicity)
                opts.update(extra)
                psi4.set_options(opts)
                energy = driver.optimize(full_method, molecule=mol, return_history=False)
                break
            except Exception as e:
                last_err = e
                log(f"  [psi4] 优化未收敛/失败（尝试 {i + 1}/{len(attempts)}）: {e}")
                log("  [psi4] 重试：放宽收敛条件（MAXITER↑ / guess=sad）")
        if energy is None:
            log(f"  [psi4] optimize FAILED: {last_err}")
            raise last_err or RuntimeError("optimize failed after retries")
        opt_xyz = _xyz_of_mol(mol)
        log(f"  [psi4] optimized, energy = {energy:.8f} Eh")
        if key is not None:
            _opt_cache_put(key, opt_xyz, float(energy))
        return opt_xyz, float(energy), None


def single_point(xyz_str: str, method: str, basis: str, charge: int = 0, multiplicity: int = 1, logger=None) -> float:
    """单点能量计算。"""

    def log(msg):
        if logger:
            logger(msg)

    import psi4
    from psi4 import driver

    with _PSI_LOCK:
        _ensure_init()
        psi4.core.clean()
        mol = xyz_to_psi4_geom(xyz_str, charge=charge, multiplicity=multiplicity)
        psi4.core.set_output_file("/tmp/psi4_last.out", False)
        psi4.set_options(_scf_options(method, multiplicity))
        full = f"{method}/{basis}"
        log(f"  [psi4] sp {full}, atoms={mol.natom()}, mult={multiplicity}")
        try:
            e = driver.energy(full, molecule=mol)
            log(f"  [psi4] energy = {e:.8f} Eh")
            return float(e)
        except Exception as ex:
            log(f"  [psi4] sp FAILED: {ex}")
            raise
        finally:
            psi4.core.clean()


def trajectory_energies(
    frames_xyz: list, method: str, basis: str, charge: int = 0, multiplicity: int = 1, logger=None
) -> list:
    """对每帧做单点能量。返回 list[float]。"""
    out = []
    n = len(frames_xyz)
    for i, xyz in enumerate(frames_xyz):
        if logger:
            logger(f"  [psi4] trajectory frame {i + 1}/{n}")
        try:
            e = single_point(xyz, method, basis, charge=charge, multiplicity=multiplicity, logger=None)
        except Exception as ex:
            if logger:
                logger(f"  [psi4] frame {i + 1} failed: {ex}; skip")
            e = float("nan")
        out.append(e)
    return out


# 单位换算
EH_TO_KJ_MOL = 2625.499638  # 1 Hartree = 2625.50 kJ/mol

# 常见 DFT 泛函（用于判断开壳层时用 UKS 还是 UHF）
_DFT_KEYWORDS = {
    "b3lyp",
    "pbe",
    "pbe0",
    "bp86",
    "blyp",
    "b97",
    "wb97x",
    "wb97x-d",
    "m06",
    "m062x",
    "m06l",
    "tpss",
    "scan",
    "revpbe",
    "pw91",
}


def _scf_options(method: str, multiplicity: int) -> dict:
    """按方法/多重度生成 SCF 选项。开壳层（mult>1）需要 UHF/UKS reference。

    注意 psi4 的 set_options 是全局的：从开壳层切回闭壳层时必须显式
    重置 REFERENCE，否则上一个 run 的 UHF 会残留导致报错或用错方法。
    """
    opts = {"MAXITER": 200, "INTS_TOLERANCE": 1e-14}
    is_dft = method.lower() in _DFT_KEYWORDS
    if multiplicity > 1:
        opts["REFERENCE"] = "UKS" if is_dft else "UHF"
        # SOSCF 对开壳层不稳定，显式关闭（避免上个 run 的 True 残留）
        opts["SOSCF"] = False
    else:
        opts["REFERENCE"] = "RKS" if is_dft else "RHF"
        opts["SOSCF"] = True
    return opts


# ============ 频率 / 热化学 ============
# 频率分析（含零点能 ZPE + 热化学校正）结果缓存：相同几何/方法/基组 → 同结果，
# 重复分子（如 2H₂O、3O₂）秒回，避免昂贵的重复频率计算。
_FREQ_CACHE_DIR = os.path.expanduser("~/.cache/quantum_reaction/freq")
_FREQ_CACHE = {}


def _freq_cache_key(xyz: str, method: str, basis: str, charge: int, mult: int) -> str:
    raw = f"{xyz.strip()}|{method}|{basis}|{charge}|{mult}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _freq_cache_get(key: str):
    if key in _FREQ_CACHE:
        return _FREQ_CACHE[key]
    p = os.path.join(_FREQ_CACHE_DIR, key + ".json")
    if os.path.exists(p):
        try:
            d = json.loads(open(p, encoding="utf-8").read())
            v = (float(d["E_elec"]), float(d["U"]), float(d["H"]), float(d["G"]))
            _FREQ_CACHE[key] = v
            return v
        except Exception:
            pass
    return None


def _freq_cache_put(key: str, vals: tuple):
    _FREQ_CACHE[key] = vals
    try:
        os.makedirs(_FREQ_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_FREQ_CACHE_DIR, key + ".json"), "w", encoding="utf-8") as f:
            json.dump({"E_elec": vals[0], "U": vals[1], "H": vals[2], "G": vals[3]}, f)
    except Exception:
        pass


def frequency_analysis(
    xyz_str: str, method: str, basis: str, charge: int = 0, multiplicity: int = 1, logger=None, cache: bool = True
):
    """在给定几何上做频率分析，提取热化学量。

    返回 dict：{E_elec, U, H, G}（单位 Hartree），失败返回 None。
      E_elec : 电子总能（psi4 CURRENT ENERGY）
      U      : THERMAL ENERGY = E_elec + ZPE + 热运动能（0K 内能，含零点）
      H      : ENTHALPY（= U + RT）
      G      : GIBBS FREE ENERGY（= H − TS）
    反应层：ΔE=ΣE_prod−ΣE_react、ΔE0=ΣU差、ΔH=ΣH差、ΔG=ΣG差（kJ/mol）。

    ⚠ 单原子（natom=1）必须走解析分支：psi4 1.9.1 对无振动自由度的
    分子跑 frequency 会 C 层段错误（SIGSEGV，无法 try/except 捕获）。
    """

    def log(m):
        if logger:
            logger(m)

    import psi4
    from psi4 import driver

    key = _freq_cache_key(xyz_str, method, basis, charge, multiplicity) if cache else None
    if key is not None:
        c = _freq_cache_get(key)
        if c is not None:
            log(f"  [psi4] 频率缓存命中 (mult={multiplicity}) → H={c[2]:.8f} Eh")
            return {"E_elec": c[0], "U": c[1], "H": c[2], "G": c[3]}

    with _PSI_LOCK:
        _ensure_init()
        psi4.core.clean()
        mol = xyz_to_psi4_geom(xyz_str, charge=charge, multiplicity=multiplicity)
        psi4.core.set_output_file("/tmp/psi4_last.out", False)
        try:
            psi4.set_options(_scf_options(method, multiplicity))
            full = f"{method}/{basis}"
            if mol.natom() == 1:
                # 单原子解析热化学：只有平动 + 电子，无振动/转动
                log("  [psi4] 单原子 → 解析热化学（理想气体平动），跳过频率")
                E = float(driver.energy(full, molecule=mol))
                res = _single_atom_thermo(mol, E)
            else:
                log(f"  [psi4] frequency {full}, atoms={mol.natom()}, mult={multiplicity}")
                e, wfn = driver.frequency(full, molecule=mol, return_wfn=True)
                res = {
                    "E_elec": float(psi4.variable("CURRENT ENERGY")),
                    "U": float(psi4.variable("THERMAL ENERGY")),
                    "H": float(psi4.variable("ENTHALPY")),
                    "G": float(psi4.variable("GIBBS FREE ENERGY")),
                }
        except Exception as ex:
            log(f"  [psi4] 频率分析失败: {ex}")
            return None
        log(f"  [psi4] thermo: E={res['E_elec']:.8f} U={res['U']:.8f} H={res['H']:.8f} G={res['G']:.8f} Eh")
        if key is not None:
            _freq_cache_put(key, (res["E_elec"], res["U"], res["H"], res["G"]))
        return res


def _single_atom_thermo(mol, E_elec: float, temperature: float = 298.15) -> dict:
    """单原子理想气体热化学（解析公式，标准态 1 bar，电子简并度取 1）。

    无振动/转动自由度，只有平动贡献：
      U = E_elec + (3/2)RT
      H = E_elec + (5/2)RT          （= U + RT，理想气体 PV 项）
      S_trans = R[ln((2πmkT)^{3/2}/h³ · kT/p) + 5/2]   （Sackur–Tetrode）
      G = H − T·S
    """
    R = 8.314462618  # J/(mol·K)
    k = 1.380649e-23  # J/K
    h = 6.62607015e-34  # J·s
    AMU = 1.66053906660e-27  # kg
    EH_PER_KJMOL = 1.0 / 2625.499638
    p = 1.0e5  # Pa（1 bar 标准态）
    T = temperature
    RT_eh = R * T / 1000.0 * EH_PER_KJMOL
    m = float(mol.mass(0)) * AMU
    q_v = (2.0 * math.pi * m * k * T) ** 1.5 / h**3  # 平动配分密度 (m^-3)
    S = R * (math.log(q_v * k * T / p) + 2.5)  # J/(mol·K)
    TS_eh = (T * S / 1000.0) * EH_PER_KJMOL
    U = E_elec + 1.5 * RT_eh
    H = E_elec + 2.5 * RT_eh
    G = H - TS_eh
    return {"E_elec": E_elec, "U": U, "H": H, "G": G}
