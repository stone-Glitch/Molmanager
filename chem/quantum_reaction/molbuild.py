"""分子构建：SMILES / 名称 → 3D XYZ 坐标。

策略：
1. 优先用 rdkit 从 SMILES 生成 3D 坐标 + MMFF 优化（提供初值，量子优化之前）
2. 暴露接口供 pubchempy 通过名称查找（不阻塞，找不到就抛）
3. 把多个分子拼成一个反应物/产物 super-molecule XYZ（多个分子分隔在坐标里，原子数总和）

融合说明（MolManager）：
- 原版顶层 ``from rdkit import Chem`` 会让整个包在无 rdkit 环境下 import 失败；
  现改为 try/except 探测 + 函数内惰性导入，rdkit 缺失时给出可操作的中文提示，
  与 MolManager「缺依赖优雅降级」的整体风格一致（psi4 / OpenBabel 同款处理）。
"""

from __future__ import annotations

# rdkit 探测：缺失时不阻塞 import，只在真正构建分子时报错
try:  # pragma: no cover - 环境差异分支
    from rdkit import Chem
    from rdkit.Chem import AllChem

    RDKIT_AVAILABLE = True
except Exception:  # ImportError 或 rdkit 崩溃（如缺 libOMP）
    Chem = AllChem = None
    RDKIT_AVAILABLE = False


def _require_rdkit():
    if not RDKIT_AVAILABLE:
        raise RuntimeError(
            "SMILES 构建 3D 坐标需要 rdkit。\n"
            "安装方法（任选其一）：\n"
            "  ① conda install -c conda-forge rdkit\n"
            "  ② pip install rdkit"
        )


# 简单分子兜底 XYZ（当 rdkit 处理单原子分子不方便时直接用）
PRESET_XYZ = {
    "H2": """2
H2
H 0.0 0.0 0.0
H 0.0 0.0 0.74
""",
    "O2": """2
O2
O 0.0 0.0 -0.6
O 0.0 0.0 0.6
""",
    "N2": """2
N2
N 0.0 0.0 -0.55
N 0.0 0.0 0.55
""",
    "Cl2": """2
Cl2
Cl 0.0 0.0 -0.99
Cl 0.0 0.0 0.99
""",
}


def _smiles_to_molblock(smiles: str, seed: int = 7):
    """SMILES → rdkit mol，3D embedded + MMFF 优化。返回 mol 对象。"""
    _require_rdkit()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"无法解析 SMILES: {smiles!r}")
    mol = Chem.AddHs(mol)
    # 多次尝试 embed
    for attempt in range(5):
        code = AllChem.EmbedMolecule(mol, randomSeed=seed + attempt, useRandomCoords=True)
        if code == 0:
            break
    else:
        raise RuntimeError(f"3D embedding 失败: {smiles!r}")
    try:
        AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    except Exception:
        # MMFF 不适用某些体系（如纯离子），用 UFF 兜底
        try:
            AllChem.UFFOptimizeMolecule(mol, maxIters=500)
        except Exception:
            pass
    return mol


def mol_to_xyz(mol) -> str:
    """rdkit mol → XYZ 字符串（含所有原子）。"""
    _require_rdkit()
    conf = mol.GetConformer(0)
    n = mol.GetNumAtoms()
    lines = [str(n), "rdkit"]
    for i in range(n):
        atom = mol.GetAtomWithIdx(i)
        sym = atom.GetSymbol()
        p = conf.GetAtomPosition(i)
        lines.append(f"{sym} {p.x:.6f} {p.y:.6f} {p.z:.6f}")
    return "\n".join(lines) + "\n"


def smiles_to_xyz(smiles: str) -> str:
    """SMILES → XYZ 字符串（H 全部展开）。"""
    # 单原子/双原子小分子走预设
    if smiles in ("[H][H]",):
        return PRESET_XYZ["H2"]
    if smiles == "O=O":
        return PRESET_XYZ["O2"]
    if smiles == "N#N":
        return PRESET_XYZ["N2"]
    if smiles == "ClCl":
        return PRESET_XYZ["Cl2"]
    _require_rdkit()
    mol = _smiles_to_molblock(smiles)
    return mol_to_xyz(mol)


def parse_xyz(text: str):
    """解析 XYZ 文本 → (elements list, numpy array Nx3)。"""
    import numpy as np

    lines = text.strip().split("\n")
    n = int(lines[0].strip())
    elements, coords = [], []
    for i in range(2, 2 + n):
        parts = lines[i].split()
        elements.append(parts[0])
        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
    return elements, np.array(coords)


def name_to_smiles(name: str) -> str:
    """通过 pubchempy 把分子名翻译为 SMILES（isomeric）。失败抛异常。"""
    import pubchempy as pcp

    name = name.strip()
    # 简单单质兜底
    mapping = {
        "hydrogen": "[H][H]",
        "H2": "[H][H]",
        "H": "[H][H]",
        "oxygen": "O=O",
        "O2": "O=O",
        "nitrogen": "N#N",
        "N2": "N#N",
        "water": "O",
        "H2O": "O",
        "ammonia": "N",
        "NH3": "N",
        "methane": "C",
        "CH4": "C",
        "carbon dioxide": "O=C=O",
        "CO2": "O=C=O",
        "carbondioxide": "O=C=O",
        "carbon monoxide": "[C-]#[O+]",
        "CO": "[C-]#[O+]",
        "ethylene": "C=C",
        "ethene": "C=C",
        "C2H4": "C=C",
        "ethane": "CC",
        "C2H6": "CC",
        "methanol": "CO",
        "CH3OH": "CO",
        "dimethyl ether": "COC",
        "DME": "COC",
        "CH3OCH3": "COC",
        "ozone": "[O-][O+]=O",
        "O3": "[O-][O+]=O",
        "hydrogen chloride": "Cl",
        "HCl": "Cl",
        "chlorine": "ClCl",
        "Cl2": "ClCl",
    }
    key = name.lower()
    if key in mapping:
        return mapping[key]
    try:
        c = pcp.get_compounds(name, "name", record_type="standard")
        if c:
            smi = c[0].isomeric_smiles
            return smi
    except Exception as e:
        raise ValueError(f"无法查询分子名 {name!r}: {e}")
    raise ValueError(f"未找到分子: {name!r}")


def normalize_atom_counts(r_elements, p_elements):
    """检查反应物/产物原子总数与元素是否一致（化学配平）。
    返回 (ok, message)。ok=True 才能做动画插值。
    """
    from collections import Counter

    rc = Counter(r_elements)
    pc = Counter(p_elements)
    if rc == pc:
        return True, "原子配平: OK"
    return False, f"原子未配平: 反应物 {dict(rc)} vs 产物 {dict(pc)}"
