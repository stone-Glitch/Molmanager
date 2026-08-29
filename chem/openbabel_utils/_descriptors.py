import csv
import logging
import math
import os
import re
import tempfile
from typing import Any

from utils.constants import (
    ATOMIC_WEIGHTS,
)
from utils.logger import default_logger as logger
from utils.logger import performance_timer

# ======================== 导入与版本兼容 ========================
from ._cache import _cache_key
from ._cli import _run_obabel, _secure_output_path
from ._common import *  # noqa: F403  # 取 ob / pybel / PYBEL_AVAILABLE / desc_cache
from ._io import _read_molecules


def _predict_descriptor(obmol, *names: str) -> float | None:
    """按给定名字依次尝试 ``OBDescriptor``，返回首个可用的预测值。

    这是 OpenBabel 官方的「描述符」计算入口（``obprop`` 插件体系）：
    ``logP`` / ``TPSA`` / ``HBD`` / ``rotors`` / ``MW`` 都从这里取值。

    ⚠ 历史 bug（重要）
    ----------------
    旧实现用 ``getattr(pybel.Molecule, "logP")`` 取值，但 OB >= 3.1 的
    ``pybel.Molecule`` **根本没有** ``.logP`` / ``.tpsa`` 属性，异常被
    ``except`` 吞掉后，LogP 与 TPSA **恒为 0.0** —— 界面上看着有数，其实是假的。
    同理 ``OBMol.NumHBD()`` / ``NumHBA()`` / ``NumSSSR()`` 在新版 SWIG 绑定里
    也不存在，导致氢键供受体数与环数恒为 0。

    参数:
        obmol: ``OBMol`` 实例。
        *names: 依次尝试的描述符名（不同 OB 版本命名有差异，如 HBA / HBA1）。

    返回:
        预测值（float）；全部不可用或结果为 NaN 时返回 ``None``。
    """
    if ob is None:
        return None
    for name in names:
        try:
            desc = ob.OBDescriptor.FindType(name)
        except Exception:
            continue
        if desc is None:
            continue
        try:
            value = desc.Predict(obmol)
        except Exception:
            continue
        if value is None:
            continue
        try:
            fvalue = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(fvalue) or math.isinf(fvalue):
            continue
        return fvalue
    return None


def _fallback_num(obmol, method_name: str) -> float | None:
    """调用 ``OBMol`` 上的 ``Num*()`` 方法（老版本 OB 的入口），不可用返回 None。"""
    fn = getattr(obmol, method_name, None)
    if not callable(fn):
        return None
    try:
        return float(fn())
    except Exception:
        return None


def _count_rings(obmol) -> int:
    """环数（SSSR 大小）。

    ``OBMol.NumSSSR()`` 只在部分 OB 版本/绑定里存在，新版统一走 ``GetSSSR()``。
    """
    try:
        sssr = obmol.GetSSSR()
        if sssr is not None:
            return len(sssr)
    except Exception:
        pass
    for method in ("NumSSSR",):
        fn = getattr(obmol, method, None)
        if callable(fn):
            try:
                return int(fn())
            except Exception:
                pass
    return 0


@performance_timer(name="ob.calculate_descriptors", level=logging.DEBUG, min_ms=10.0)
def calculate_descriptors(input_path: str) -> dict[str, Any]:
    """
    计算分子描述符（分子量、logP、TPSA、氢键供体/受体、可旋转键、环数等）。
    返回: {'success': bool, 'message': str, 'descriptors': dict}
    带 LRU 缓存：基于 (path_resolved, mtime_ns, size) 命中直接返回；读写加锁。
    """
    ck = _cache_key(input_path)
    if ck is not None:
        cached = desc_cache.get(ck)
        if cached is not None:
            return dict(cached)
    descriptors: dict[str, Any] = {}
    try:
        if PYBEL_AVAILABLE:
            ext = os.path.splitext(input_path)[1][1:].lower()
            mols = _read_molecules(input_path, ext)
            if not mols:
                result = {"success": False, "message": "无法读取分子", "descriptors": {}}
            else:
                mol = mols[0]
                obmol = mol.OBMol

                # 氢键供体 / 受体：OB >= 3.1 没有 NumHBD()/NumHBA()，
                # 走 OBDescriptor（HBA 在不同版本叫 HBA / HBA1 / HBA2，依次尝试）。
                hbd = _predict_descriptor(obmol, "HBD")
                if hbd is None:
                    hbd = _fallback_num(obmol, "NumHBD")
                hba = _predict_descriptor(obmol, "HBA", "HBA1", "HBA2")
                if hba is None:
                    hba = _fallback_num(obmol, "NumHBA")
                rotors = _predict_descriptor(obmol, "rotors")
                if rotors is None:
                    rotors = _fallback_num(obmol, "NumRotors")

                descriptors = {
                    "molecular_weight": 0.0,
                    "logP": 0.0,
                    "tpsa": 0.0,
                    "heavy_atoms": obmol.NumAtoms() if hasattr(obmol, "NumAtoms") else len(mol.atoms),
                    "bonds": obmol.NumBonds() if hasattr(obmol, "NumBonds") else None,
                    "hbd": int(hbd) if hbd is not None else 0,
                    "hba": int(hba) if hba is not None else 0,
                    "rotors": int(rotors) if rotors is not None else 0,
                    "rings": _count_rings(obmol),
                }
                # E-04 化学感知搜索：补充「分子式」与「总原子数」两个常用检索维度。
                # 纯增量，不影响已有键（重命名占位符仅引用其中的部分键）。
                try:
                    descriptors["formula"] = mol.formula
                except Exception as _fe:
                    logger.debug("获取分子式失败: %s", _fe)
                try:
                    descriptors["num_atoms"] = len(mol.atoms)
                except Exception as _ae:
                    logger.debug("获取总原子数失败: %s", _ae)
                # 数值描述符：优先 OBDescriptor（官方计算入口），失败再回退 pybel 属性。
                # 旧实现只取 pybel 属性，而 OB >= 3.1 的 Molecule 没有 .logP / .tpsa，
                # 异常被吞掉后这两个键恒为 0.0 —— 界面上有数、实际是假的。
                for ob_name, attr_name, attr_key in (
                    ("MW", "molwt", "molecular_weight"),
                    ("logP", "logP", "logP"),
                    ("TPSA", "tpsa", "tpsa"),
                ):
                    v = _predict_descriptor(obmol, ob_name)
                    if v is None:
                        try:
                            v2 = getattr(mol, attr_name)
                            if callable(v2):
                                v2 = v2()
                            v = float(v2)
                        except Exception as _de:
                            logger.debug("计算描述符 %s 失败: %s", attr_key, _de)
                    if v is not None:
                        descriptors[attr_key] = float(v)
                result = {"success": True, "message": "描述符计算成功", "descriptors": descriptors}
        else:
            # 命令行模式（有限支持）
            with tempfile.NamedTemporaryFile(suffix=".prop", delete=False) as tmp:
                tmp_name = tmp.name
            try:
                cmd = ["obabel", input_path, "-o", "prop", "-O", tmp_name]
                cmd_result = _run_obabel(cmd, timeout=30)
                if cmd_result.returncode == 0 and os.path.exists(tmp_name):
                    with open(tmp_name, encoding='utf-8', errors='replace') as f:
                        data = f.read()
                    descriptors["info"] = data.strip()
                else:
                    descriptors["error"] = "命令行模式获取描述符失败"
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.unlink(tmp_name)
                    except OSError as _oe:
                        logger.debug("清理 obabel 临时描述符文件失败: %s, err=%s", tmp_name, _oe)
            result = {"success": True, "message": "命令行模式描述符（有限）", "descriptors": descriptors}
    except Exception as e:
        result = {"success": False, "message": str(e), "descriptors": {}}
    if ck is not None:
        desc_cache.put(ck, dict(result))
    return result


# ======================== O3：分子式 / 精确分子量 / 元素百分比 ========================

def analyze_formula(input_path: str) -> dict[str, Any]:
    """
    选中一个文件返回：
      formula (字符串，例：CH4)
      exact_mass (精确分子量，浮点)
      molecular_weight (平均分子量)
      atoms_count (原子总数)
      elements_pct: {"C": 75.0, "H": 25.0, ...} （元素→质量百分比 %）
    """
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]
        obmol = mol.OBMol
        formula = ""
        mw_exact = 0.0
        mw_avg = 0.0
        elements: dict[str, int] = {}
        try:
            formula = obmol.GetFormula() if hasattr(obmol, "GetFormula") else ""
        except Exception as _e:
            logger.debug("obmol.GetFormula() 失败: %s", _e)
        try:
            mw_exact = float(obmol.GetExactMass()) if hasattr(obmol, "GetExactMass") else 0.0
        except Exception as _e:
            logger.debug("obmol.GetExactMass() 失败: %s", _e)
        try:
            mw_avg = float(obmol.GetMolWt()) if hasattr(obmol, "GetMolWt") else 0.0
        except Exception as _e:
            logger.debug("obmol.GetMolWt() 失败: %s", _e)
        try:
            atoms_iter = obmol.GetAtoms() if hasattr(obmol, "GetAtoms") else list(mol.atoms)
        except Exception as _e:
            logger.debug("obmol.GetAtoms() 失败，回退 mol.atoms: %s", _e)
            atoms_iter = list(mol.atoms)
        # 集中化：从 constants 复用原子量表，方便统一维护
        atomic_weights: dict[str, float] = dict(ATOMIC_WEIGHTS)
        tot_mass = 0.0
        atoms_count = 0
        try:
            for a in atoms_iter:
                sym = a.GetSymbol() if hasattr(a, "GetSymbol") else a.symbol
                num = a.GetAtomicNum() if hasattr(a, "GetAtomicNum") else a.atomicnum
                w = atomic_weights.get(sym) or atomic_weights.get(sym.capitalize(), num or 12.0)
                elements[sym] = elements.get(sym, 0) + 1
                tot_mass += w
                atoms_count += 1
        except Exception as _ae:
            logger.debug("遍历原子失败，回退 formula 粗解析: %s", _ae)
            # 回退：按 formula 粗解析
            for m in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
                if not m[0]:
                    continue
                cnt = int(m[1]) if m[1] else 1
                elements[m[0]] = elements.get(m[0], 0) + cnt
                tot_mass += atomic_weights.get(m[0], 12.0) * cnt
                atoms_count += cnt
        # 按 Hill 系统重排
        hill_parts: list[str] = []
        for k in ("C", "H"):
            if k in elements:
                hill_parts.append(f"{k}{elements[k] if elements[k] != 1 else ''}")
        for k in sorted(elements.keys()):
            if k in ("C", "H"):
                continue
            hill_parts.append(f"{k}{elements[k] if elements[k] != 1 else ''}")
        if not formula:
            formula = "".join(hill_parts)
        # 元素质量百分比
        pct: dict[str, float] = {}
        if tot_mass > 0:
            for sym, count in elements.items():
                w = atomic_weights.get(sym, 12.0)
                pct[sym] = round(count * w / tot_mass * 100.0, 2)
        if mw_avg <= 0 and tot_mass > 0:
            mw_avg = tot_mass
        return {
            "success": True,
            "formula": formula,
            "hill_formula": "".join(hill_parts),
            "exact_mass": mw_exact,
            "molecular_weight": mw_avg,
            "atoms_count": atoms_count,
            "elements": elements,
            "elements_pct": pct,
        }
    except Exception as e:
        return {"success": False, "message": f"元素分析失败：{e}"}


# ======================== O6：导出键长 / 键角 CSV ========================

def export_geometry_csv(input_path: str, out_csv_path: str) -> dict[str, Any]:
    """
    提取分子所有键长（Å）及所有可能的 1-2-3 键角（度），写 CSV。
    纯 OpenBabel 实现，不依赖任何量化软件。
    """
    # 【审计 1.1】输出路径安全解析
    try:
        out_csv_path = str(_secure_output_path(out_csv_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出 CSV 路径非法: {e}"}
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]
        obmol = mol.OBMol

        # 原子 0-based → 符号 + 坐标 (Å)
        atoms_list: list[tuple[int, str, list[float]]] = []
        try:
            iter_atoms = list(obmol.GetAtoms())
        except Exception:
            iter_atoms = list(mol.atoms)
        for idx, a in enumerate(iter_atoms):
            if hasattr(a, "GetX"):
                sym = a.GetSymbol(); x, y, z = a.GetX(), a.GetY(), a.GetZ()
            else:
                sym = a.symbol; x, y, z = a.coords
            atoms_list.append((idx + 1, str(sym), [float(x), float(y), float(z)]))  # 1-based 编号

        # 键长
        bonds_list: list[tuple[int, int, str, str, float]] = []
        try:
            iter_bonds = list(obmol.GetBonds())
            for b in iter_bonds:
                i = b.GetBeginAtomIdx(); j = b.GetEndAtomIdx()
                if hasattr(b, "GetLength"):
                    length = float(b.GetLength())
                else:
                    import math
                    a1 = next((a for a in atoms_list if a[0] == i), None)
                    a2 = next((a for a in atoms_list if a[0] == j), None)
                    if not a1 or not a2:
                        continue
                    length = math.sqrt(sum((a1[2][k] - a2[2][k]) ** 2 for k in range(3)))
                sym_i = next((a[1] for a in atoms_list if a[0] == i), "?")
                sym_j = next((a[1] for a in atoms_list if a[0] == j), "?")
                bonds_list.append((i, j, sym_i, sym_j, round(length, 5)))
        except Exception:
            import itertools
            import math
            # 回退：根据原子间距 < 1.85Å 猜测键（通用有机分子，金属键可能不准）
            for (i1, s1, c1), (i2, s2, c2) in itertools.combinations(atoms_list, 2):
                d = math.sqrt(sum((c1[k] - c2[k]) ** 2 for k in range(3)))
                if d <= 1.85:
                    bonds_list.append((i1, i2, s1, s2, round(d, 5)))

        # 键角：对每个有至少 2 个邻居的原子作为中心原子，枚举两边
        angles_list: list[tuple[int, int, int, str, str, str, float]] = []
        try:
            neighbors: dict[int, list[int]] = {}
            for i, j, _, _, _ in bonds_list:
                neighbors.setdefault(i, []).append(j)
                neighbors.setdefault(j, []).append(i)
            import math
            sym_map = {a[0]: a[1] for a in atoms_list}
            coord_map = {a[0]: a[2] for a in atoms_list}
            for center, neigh in neighbors.items():
                if len(neigh) < 2:
                    continue
                import itertools as _it
                for a1, a2 in _it.combinations(neigh, 2):
                    if center not in coord_map or a1 not in coord_map or a2 not in coord_map:
                        continue
                    c, p1, p2 = coord_map[center], coord_map[a1], coord_map[a2]
                    v1 = [p1[k] - c[k] for k in range(3)]
                    v2 = [p2[k] - c[k] for k in range(3)]
                    dot = sum(v1[k] * v2[k] for k in range(3))
                    n1 = math.sqrt(sum(v1[k] ** 2 for k in range(3)))
                    n2 = math.sqrt(sum(v2[k] ** 2 for k in range(3)))
                    if n1 <= 0 or n2 <= 0:
                        continue
                    cosang = max(-1.0, min(1.0, dot / (n1 * n2)))
                    deg = math.degrees(math.acos(cosang))
                    angles_list.append((a1, center, a2,
                                        sym_map.get(a1, "?"), sym_map.get(center, "?"), sym_map.get(a2, "?"),
                                        round(deg, 3)))
        except Exception as e_ang:
            logger.debug("计算键角失败：%s", e_ang)
        # 写 CSV
        with open(out_csv_path, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow([f"分子元素分析：{len(atoms_list)} 个原子，{len(bonds_list)} 根键"])
            wr.writerow([])
            wr.writerow(["键长表 (Bond Lengths)"])
            wr.writerow(["Atom1_Id", "Atom1", "Atom2_Id", "Atom2", "Length_A"])
            for i, j, si, sj, L in bonds_list:
                wr.writerow([i, si, j, sj, L])
            wr.writerow([])
            wr.writerow(["键角表 (Bond Angles，度)"])
            wr.writerow(["Atom1_Id", "Atom1", "Center_Id", "Center", "Atom3_Id", "Atom3", "Angle_deg"])
            for a, c, b, sa, sc, sb, deg in angles_list:
                wr.writerow([a, sa, c, sc, b, sb, deg])
        return {
            "success": True,
            "out_csv": out_csv_path,
            "n_atoms": len(atoms_list),
            "n_bonds": len(bonds_list),
            "n_angles": len(angles_list),
        }
    except Exception as e:
        return {"success": False, "message": f"导出几何参数失败：{e}"}


# ======================== O2：SMILES → InChIKey 搜索本地相似分子 ========================

def smiles_to_inchikey(smiles: str) -> dict[str, Any]:
    """
    把一个 SMILES 字符串变成 InChIKey（第一块 14 字母 = 骨架相同可近似命中）。
    失败返回 success=False + message。
    """
    try:
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要安装 pybel/OpenBabel Python 包才能解析 SMILES"}
        smi = smiles.strip()
        if not smi:
            return {"success": False, "message": "SMILES 为空"}
        mol = pybel.readstring("smi", smi)
        if mol is None:
            return {"success": False, "message": f"无法解析 SMILES: {smiles}"}
        obmol = mol.OBMol
        obmol.AddHydrogens()
        try:
            obmol.PerceiveStereo()
        except Exception:
            pass
        inchikey = ""
        # pybel 方式
        try:
            inchikey = str(mol.write("inchikey")).strip().split("\n")[0].strip()
        except Exception:
            pass
        if not inchikey:
            try:
                conv = ob.OBConversion()
                conv.SetOutFormat("inchikey")
                inchikey = conv.WriteString(obmol).strip().split("\n")[0].strip()
            except Exception:
                pass
        if not inchikey or "InChIKey" not in inchikey and len(inchikey) < 10:
            return {"success": False, "message": f"InChIKey 生成失败: {inchikey!r}"}
        key = inchikey if "=" not in inchikey else inchikey.split("=", 1)[1].strip()
        key = key.strip()
        skeleton = key.split("-")[0] if "-" in key else key[:14]
        return {
            "success": True,
            "smiles": smi,
            "inchikey": key,
            "skeleton_14": skeleton.upper(),
            "canonical_smiles": mol.write("can").strip() if mol else smi,
            "formula": obmol.GetFormula() if hasattr(obmol, "GetFormula") else "",
        }
    except Exception as e:
        return {"success": False, "message": f"SMILES 解析失败：{e}"}



def batch_inchikey(paths: list[str]) -> dict[str, str | None]:
    """
    批量把多个分子文件 → InChIKey dict: {abs_path: inchikey or None}。
    带 LRU（基于文件 cache_key）。
    """
    ret: dict[str, str | None] = {}
    if not PYBEL_AVAILABLE:
        return dict.fromkeys(paths)
    for fp in paths:
        try:
            ext = os.path.splitext(fp)[1][1:].lower()
            mols = _read_molecules(fp, ext)
            if not mols:
                ret[fp] = None
                continue
            mol = mols[0]
            obmol = mol.OBMol
            ik = ""
            try:
                ik = str(mol.write("inchikey")).strip().split("\n")[0]
            except Exception as _e1:
                logger.debug("pybel.write(inchikey) 失败 %s: %s", fp, _e1)
            if not ik:
                try:
                    conv = ob.OBConversion()
                    conv.SetOutFormat("inchikey")
                    ik = conv.WriteString(obmol).strip().split("\n")[0]
                except Exception as _e2:
                    logger.debug("OBConversion(inchikey) 失败 %s: %s", fp, _e2)
            if "=" in ik:
                ik = ik.split("=", 1)[1].strip()
            ret[fp] = ik or None
        except Exception as _be:
            logger.debug("批量 InChIKey 处理失败 %s: %s", fp, _be)
            ret[fp] = None
    return ret


# ======================== O4：手性中心识别 + 对映体翻转 ========================
