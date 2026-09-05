#!/usr/bin/env python3
"""按 IQmol XyzParser.C / CartesianCoordinatesParser.C 的逻辑验证多帧 XYZ。

模拟要点（源自 IQmol 源码）：
1. 循环 readNextGeometry：找一行 ^\\d+$ 作为原子数（seek 整数行）
2. 紧接的下一行是注释行，正则 anyReal = ([-+]?[0-9]*\\.[0-9]+([eE][-+]?[0-9]+)?)
   捕获其中第一个实数作为该帧能量（单位 Hartree），无匹配则能量为 0
3. 随后恰好 N 行坐标，每行 >= 4 个 token：
   [元素符号|原子序数] x y z（额外列容忍）；行数不足 N → "Invalid format" 整体失败
4. nextNonEmptyLineAsTokens：空行会被跳过（但会占用注释行之后的读取位）
"""

import json
import re
import sys

# 标准元素符号表（用于模拟 Data::Atom::atomicNumber）
SYMBOLS = set(
    """
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La
Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po
At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg
""".split()
)

INTEGER_ONLY = re.compile(r"^\d+$")
ANY_REAL = re.compile(r"([-+]?[0-9]*\.[0-9]+(?:[eE][-+]?[0-9]+)?)")


def parse_like_iqmol(path):
    """返回 [(n_atoms, energy_hartree_or_None, [(symbol, x, y, z), ...]), ...]
    若违反 IQmol 解析规则则抛异常。"""
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f]

    frames = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not INTEGER_ONLY.match(line):
            i += 1
            continue
        n_atoms = int(line)
        # 注释行必须是紧接的下一行（IQmol: textStream.nextLine()）
        if i + 1 >= n:
            raise ValueError(f"行 {i + 1}: 原子数行后缺少注释行")
        comment = lines[i + 1]
        m = ANY_REAL.search(comment)
        energy = float(m.group(1)) if m else None  # IQmol 中无匹配则记 0.0

        # 恰好 n_atoms 行坐标（跳过空行——nextNonEmptyLineAsTokens）
        coords = []
        j = i + 2
        while len(coords) < n_atoms:
            if j >= n:
                raise ValueError(
                    f"行 {i + 1}: 声明 {n_atoms} 个原子但只读到 {len(coords)} 行（IQmol 将报 Invalid format）"
                )
            toks = lines[j].split()
            if not toks:  # 空行：IQmol 跳过后继续读
                j += 1
                continue
            if len(toks) < 4:
                raise ValueError(f"行 {j + 1}: token 数 {len(toks)} < 4")
            sym = toks[0]
            if sym.isdigit():
                if not (0 < int(sym) <= 112):
                    raise ValueError(f"行 {j + 1}: 非法原子序数 {sym}")
            elif sym not in SYMBOLS:
                raise ValueError(f"行 {j + 1}: 无法识别的元素符号 {sym!r}")
            x, y, z = (float(toks[k]) for k in (1, 2, 3))
            coords.append((sym, x, y, z))
            j += 1
        frames.append((n_atoms, energy, coords, comment))
        i = j
    if not frames:
        raise ValueError("未找到任何帧")
    return frames


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("用法: python -m chem.quantum_reaction.iqmol_check <trajectory.xyz>")
        sys.exit(2)
    path = argv[0]
    frames = parse_like_iqmol(path)
    print(f"✓ IQmol 兼容性验证通过：{len(frames)} 帧，全部完整解析")
    print(f"{'帧':>4} {'原子数':>6} {'能量(Hartree)':>16}  注释行")
    for k, (na, e, _, comment) in enumerate(frames):
        es = f"{e:.6f}" if e is not None else "(无→IQmol记0)"
        print(f"{k + 1:>4} {na:>6} {es:>16}  {comment.strip()[:52]}")

    # 与 energy_curve.json 交叉核对
    import os

    run_dir = os.path.dirname(path)
    ec_path = os.path.join(run_dir, "energy_curve.json")
    if os.path.exists(ec_path):
        ec = json.load(open(ec_path))
        eh = ec.get("energies_eh", [])
        if len(eh) == len(frames):
            print("\n与 energy_curve.json 逐帧核对（注释行能量 vs 计算能量）:")
            ok = True
            for k, (_, e, _, _) in enumerate(frames):
                ref = eh[k]
                if ref != ref:  # NaN
                    status = "✓" if e is None else "✗(NaN帧不应带能量)"
                    ok = ok and (e is None)
                else:
                    d = abs(e - ref) if e is not None else 9e9
                    status = "✓" if d < 5e-7 else f"✗ 偏差{d:.2e}"
                    ok = ok and d < 5e-7
                print(f"  帧 {k + 1}: {status}")
            print("\n结论:", "全部一致 ✓" if ok else "存在不一致 ✗")
            sys.exit(0 if ok else 1)
    print("\n(无对应 energy_curve.json，跳过交叉核对)")


if __name__ == "__main__":
    main()
