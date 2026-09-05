"""量子反应计算编排（框架无关核心）。

从原 Quantum Reaction Visualizer 的 Flask ``app._compute_worker`` 抽出，
改为回调驱动：Tk 对话框 / CLI / 未来任何前端都能直接复用同一套管线。

流程（与原版逐步对齐，保证化学结果可复现）：
  1. 解析反应（预设 id 或自定义 spec 列表；自定义支持 ``SMILES:N`` 多重度语法）
  2. 逐分子构建 XYZ（rdkit SMILES → 3D；单原子/双原子走预设）
  3. 配平预检（进 psi4 之前，避免优化全部跑完才发现配平失败）
  4. 逐分子 psi4 优化（缓存 + 单原子降级 + SCF 分级重试）
  5. 聚合 super-molecule → ΔE（kJ/mol）
  6. 可选热化学：逐分子频率分析 → ΔE₀ / ΔH° / ΔG°（298.15 K、1 bar）
  7. Kabsch 对齐 + 线性插值 → 多帧轨迹
  8. 可选逐帧单点能量（反应物侧组合多重度，自旋守恒近似）
  9. 写 IQmol 兼容 trajectory.xyz（注释行嵌入逐帧能量）+ energy_curve.json + MP4

用法::

    result = run_reaction(
        {"reaction_id": "water", "method": "hf", "basis": "sto-3g"},
        run_dir=Path(".../runs/xxx"),
        on_log=print,
        on_stage=lambda stage, p: ...,
        should_cancel=lambda: False,
    )
"""

from __future__ import annotations

import json
import re
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Callable

from .animate import make_trajectory, render_mp4
from .molbuild import normalize_atom_counts, parse_xyz, smiles_to_xyz
from .quantum import EH_TO_KJ_MOL, frequency_analysis, optimize_geometry, trajectory_energies
from .reactions import get_reaction

# `:N` 多重度后缀：如 ``O=O:3`` / ``[O]:3`` / ``H2:2``（原版在网页前端解析，
# 桌面版在此处统一实现；非法值安全剥离并回退单重态）
_MULT_SUFFIX = re.compile(r":\s*(\d+)\s*$")


def parse_species_token(token: str) -> tuple[str, int]:
    """把 ``SMILES:N`` 拆成 (smiles, multiplicity)。

    非法多重度（``:0``、``:abc`` 之类）安全剥离回退单重态，不影响其余解析。
    """
    token = (token or "").strip()
    m = _MULT_SUFFIX.search(token)
    if not m:
        return token, 1
    mult = int(m.group(1))
    base = token[: m.start()].strip()
    return base, (mult if mult >= 1 else 1)


def _safe_mult(spec: dict | None) -> int:
    """安全读取分子自旋多重度：非法/缺失值回退 1（单重态）。"""
    if not isinstance(spec, dict):
        return 1
    try:
        m = int(spec.get("multiplicity", 1))
        return m if m >= 1 else 1
    except (TypeError, ValueError):
        return 1


def combined_mult(specs: list) -> int:
    """逐帧路径能量用「反应物侧组合多重度」（链式 |M1-M2|+1，自旋守恒近似）。

    例：2H₂(1) + O₂(3) → 3；2O₃(1) → 1。
    """
    m = 1
    for s in specs:
        m = abs(m - _safe_mult(s)) + 1
    return m


def _spec_list_from_tokens(tokens: list) -> list:
    """把用户输入的 token 列表转成 spec dict 列表（含 :N 解析）。

    token 可以是 SMILES、分子名（含 pubchempy 查询由 molbuild 处理）、或带 ``:N`` 后缀。
    """
    specs = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        base, mult = parse_species_token(tok)
        spec: dict = {"smiles": base, "label": base}
        if mult != 1:
            spec["multiplicity"] = mult
        specs.append(spec)
    return specs


class CancelledError(RuntimeError):
    """用户取消（由 should_cancel 检查点抛出，调用方据此安全收尾）。"""


def _check_cancel(should_cancel: Callable[[], bool] | None):
    if should_cancel is not None:
        try:
            if should_cancel():
                raise CancelledError("用户取消任务")
        except CancelledError:
            raise
        except Exception:
            pass


def run_reaction(
    payload: dict,
    *,
    run_dir: Path,
    on_log: Callable[[str], None] | None = None,
    on_stage: Callable[[str, float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """执行一次完整的反应能计算。

    参数：
      - payload：``{reaction_id}`` 或 ``{custom: {reactants: [...], products: [...]}}``；
        可选 ``method/basis/n_frames/do_traj_energy/do_thermo``。
        自定义 reactants/products 既接受原版 spec dict，也接受字符串 token（含 ``:N``）。
      - run_dir：本 run 的输出目录（自动创建）。
      - on_log：日志回调（后台线程调用，UI 层自行调度回主线程）。
      - on_stage：阶段回调 ``(stage_name, progress 0~1)``。
      - should_cancel：协作式取消检查（每次返回 True 即中止）。

    返回 result dict（含 delta_e_kjmol / thermo / trajectory_xyz / energy_curve / files...）。
    失败抛异常（配平失败等友好 ValueError；psi4 异常原样上抛）。
    """
    log = on_log or (lambda m: None)
    stage = on_stage or (lambda s, p: None)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    log(f"=== run start: {run_dir.name} ===")
    try:
        method = payload.get("method", "hf")
        basis = payload.get("basis", "sto-3g")
        n_frames = int(payload.get("n_frames", 15))
        do_traj_energy = bool(payload.get("do_traj_energy", False))
        # 热化学（频率分析 → ΔE0/ΔH°/ΔG°）：默认开启；频率失败自动降级为仅 ΔE
        do_thermo = bool(payload.get("do_thermo", True))
        log(f"method={method}/{basis}, n_frames={n_frames}, do_traj_energy={do_traj_energy}, do_thermo={do_thermo}")

        # 1. 解析反应（预设 id 或自定义）
        _check_cancel(should_cancel)
        stage("build_reactants", 0.05)
        if payload.get("reaction_id"):
            r = get_reaction(payload["reaction_id"])
            if not r:
                raise ValueError(f"未知 reaction_id: {payload['reaction_id']}")
            reactant_specs = list(r["reactants"])
            product_specs = list(r["products"])
            log(f"预设反应: {r['name']} ({r['equation']})")
        else:
            custom = payload.get("custom") or {}
            reactant_specs = custom.get("reactants", [])
            product_specs = custom.get("products", [])
            # 允许直接传字符串 token（含 :N 多重度语法）
            if reactant_specs and isinstance(reactant_specs[0], str):
                reactant_specs = _spec_list_from_tokens(reactant_specs)
            if product_specs and isinstance(product_specs[0], str):
                product_specs = _spec_list_from_tokens(product_specs)
            log(f"自定义反应: reactants={reactant_specs}, products={product_specs}")
        if not reactant_specs or not product_specs:
            raise ValueError("反应物或产物列表为空。")

        # 2. 构建 XYZ —— 每个分子单独构建（优化后再聚合 super-molecule）
        def _build_all(specs, side_name):
            xyzs = []
            for idx, spec in enumerate(specs):
                if "smiles" in spec and spec.get("smiles"):
                    xyz = smiles_to_xyz(spec["smiles"])
                elif "name" in spec and spec.get("name"):
                    from .molbuild import name_to_smiles

                    xyz = smiles_to_xyz(name_to_smiles(spec["name"]))
                else:
                    raise ValueError(f"spec 缺少 smiles/name: {spec}")
                xyzs.append(xyz)
                log(
                    f"  {side_name}分子 {idx + 1}: "
                    f"{spec.get('label', spec.get('smiles', spec.get('name')))} "
                    f"({len(parse_xyz(xyz)[0])} 原子)"
                )
            return xyzs

        log("构建反应物各分子...")
        r_mol_xyzs = _build_all(reactant_specs, "反应物")
        _check_cancel(should_cancel)
        stage("build_products", 0.10)
        log("构建产物各分子...")
        p_mol_xyzs = _build_all(product_specs, "产物")

        # 3. 配平预检：进 psi4 之前确认两侧元素种类与数量一致
        r_elems: list = []
        for xyz in r_mol_xyzs:
            r_elems.extend(parse_xyz(xyz)[0])
        p_elems: list = []
        for xyz in p_mol_xyzs:
            p_elems.extend(parse_xyz(xyz)[0])
        balanced, msg = normalize_atom_counts(r_elems, p_elems)
        if not balanced:
            raise ValueError(msg + "。请调整反应物/产物（提示：H₂ + ½ O₂ → H₂O 不合法，应写 2H₂ + O₂ → 2H₂O）")
        log("  " + msg)

        # 4. 逐分子优化 + 可选频率热化学
        def _optimize_side(specs, mol_xyzs, side_name, progress):
            opt_xyzs = []
            e_sum = 0.0
            thermos = []
            thermo_ok = True
            for i, xyz in enumerate(mol_xyzs):
                _check_cancel(should_cancel)
                mult = _safe_mult(specs[i])
                stage(progress[0], progress[1] + i * 0.001)
                log(f"psi4 优化{side_name}分子 {i + 1}/{len(mol_xyzs)} (mult={mult})")
                ox, oe, _ = optimize_geometry(xyz, method, basis, charge=0, multiplicity=mult, logger=log)
                opt_xyzs.append((ox, oe))
                e_sum += oe
                log(f"  E = {oe:.8f} Eh")
                if do_thermo:
                    th = frequency_analysis(ox, method, basis, charge=0, multiplicity=mult, logger=log)
                    thermos.append(th)
                    if th is None:
                        thermo_ok = False
                        log(f"  ⚠ 分子 {i + 1} 频率分析失败，热化学将降级为仅 ΔE")
            return opt_xyzs, e_sum, thermos, thermo_ok

        stage("optimize_reactants", 0.15)
        r_opt_xyzs, r_opt_e, r_mol_thermos, thermo_ok = _optimize_side(
            reactant_specs, r_mol_xyzs, "反应物", ("optimize_reactants", 0.15)
        )
        _check_cancel(should_cancel)
        stage("optimize_products", 0.35)
        p_opt_xyzs, p_opt_e, p_mol_thermos, thermo_ok2 = _optimize_side(
            product_specs, p_mol_xyzs, "产物", ("optimize_products", 0.35)
        )
        thermo_ok = thermo_ok and thermo_ok2

        # 5. 聚合 super-molecule（每分子沿 x 轴 6 Å 隔开）+ ΔE
        import numpy as np

        def aggregate(mol_xyzs):
            all_e, all_c = [], None
            x_off = 0.0
            gap = 6.0
            for xyz in mol_xyzs:
                e, c = parse_xyz(xyz)
                c = c.copy()
                c[:, 0] += x_off - c[:, 0].min()
                x_off = c[:, 0].max() + gap
                all_e.extend(e)
                all_c = c if all_c is None else np.vstack([all_c, c])
            s = f"{len(all_e)}\naggregated\n"
            for sym, row in zip(all_e, all_c):
                s += f"{sym} {row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n"
            return s, all_e, all_c

        r_xyz, r_elements, _ = aggregate([x for x, _ in r_opt_xyzs])
        p_xyz, p_elements, _ = aggregate([x for x, _ in p_opt_xyzs])
        (run_dir / "reactant_opt.xyz").write_text(r_xyz)
        (run_dir / "product_opt.xyz").write_text(p_xyz)
        log(f"反应物总能量 = {r_opt_e:.8f} Eh")
        log(f"产物总能量 = {p_opt_e:.8f} Eh")

        delta_e = p_opt_e - r_opt_e
        delta_kj = delta_e * EH_TO_KJ_MOL
        log(f"ΔE = {delta_e:.8f} Eh = {delta_kj:.2f} kJ/mol")

        # 6. 热化学（298.15 K，1 bar）：ΔE0（含零点）/ ΔH° / ΔG°
        thermo = None
        if do_thermo:
            _check_cancel(should_cancel)
            stage("thermochemistry", 0.48)
            if thermo_ok and len(r_mol_thermos) == len(r_mol_xyzs) and len(p_mol_thermos) == len(p_mol_xyzs):
                try:
                    rU = sum(t["U"] for t in r_mol_thermos)
                    pU = sum(t["U"] for t in p_mol_thermos)
                    rH = sum(t["H"] for t in r_mol_thermos)
                    pH = sum(t["H"] for t in p_mol_thermos)
                    rG = sum(t["G"] for t in r_mol_thermos)
                    pG = sum(t["G"] for t in p_mol_thermos)
                    thermo = {
                        "temperature_K": 298.15,
                        "delta_e0_kjmol": (pU - rU) * EH_TO_KJ_MOL,
                        "delta_h_kjmol": (pH - rH) * EH_TO_KJ_MOL,
                        "delta_g_kjmol": (pG - rG) * EH_TO_KJ_MOL,
                    }
                    log(f"ΔE0(含零点) = {thermo['delta_e0_kjmol']:.2f} kJ/mol")
                    log(f"ΔH°(298K)   = {thermo['delta_h_kjmol']:.2f} kJ/mol")
                    log(
                        f"ΔG°(298K)   = {thermo['delta_g_kjmol']:.2f} kJ/mol"
                        f"  ({'自发' if thermo['delta_g_kjmol'] < 0 else '非自发（标准态）'})"
                    )
                except Exception as e:
                    log(f"热化学聚合失败: {e}")
                    thermo = None
            else:
                log("⚠ 存在频率分析失败的分子，ΔE0/ΔH°/ΔG° 不可用（仅提供 ΔE）")

        # 7. 生成插值帧（Kabsch 对齐 + 线性插值）
        _check_cancel(should_cancel)
        stage("animate", 0.55)
        log("生成插值轨迹...")
        elements, frames = make_trajectory(r_xyz, p_xyz, n_frames=n_frames)
        n_f = len(frames)

        # 8. 可选逐帧单点能量（反应物侧组合多重度）
        agg_mult = combined_mult(reactant_specs)
        energies = []
        if do_traj_energy and len(elements) <= 8:
            _check_cancel(should_cancel)
            stage("trajectory_energy", 0.65)
            log(f"对每帧做 psi4 单点能量 (mult={agg_mult})...")
            frame_xyzs = []
            for i, coords in enumerate(frames):
                s = f"{len(elements)}\nframe {i + 1}\n"
                for sym, (x, y, z) in zip(elements, coords):
                    s += f"{sym} {x:.6f} {y:.6f} {z:.6f}\n"
                frame_xyzs.append(s)
            energies = trajectory_energies(frame_xyzs, method, basis, charge=0, multiplicity=agg_mult, logger=log)
            log("能量轨迹完成")
        else:
            if do_traj_energy and len(elements) > 8:
                log(f"原子数 {len(elements)} > 8，跳过逐帧能量计算")
            energies = [r_opt_e, p_opt_e]

        # 9. 写 IQmol 兼容多帧 XYZ：注释行嵌入逐帧能量
        #    IQmol XyzParser 取注释行第一个带小数点的实数作为该帧能量（Hartree）；
        #    "frame 3/12" 中的整数不含小数点，不会被误认；NaN 帧不写能量（IQmol 记 0）。
        def _frame_energy(i):
            if len(energies) == n_f:
                e = energies[i]
                return e if (e == e) else None  # NaN → None
            if len(energies) == 2 and n_f >= 2:
                if i == 0:
                    return energies[0]
                if i == n_f - 1:
                    return energies[1]
            return None

        traj_xyz_path = run_dir / "trajectory.xyz"
        with open(traj_xyz_path, "w") as f:
            for i, coords in enumerate(frames):
                e = _frame_energy(i)
                comment = f"frame {i + 1}/{n_f}"
                if e is not None:
                    comment += f" E = {e:.8f} Eh"
                f.write(f"{len(elements)}\n{comment}\n")
                for sym, (x, y, z) in zip(elements, coords):
                    f.write(f"{sym} {x:.6f} {y:.6f} {z:.6f}\n")
        log(f"轨迹已写入: {traj_xyz_path.name}（多帧 XYZ，可在 IQmol 中播放）")

        energy_curve = {
            "frames": list(range(len(energies) if energies else 2)),
            "energies_eh": energies,
            "energies_kjmol": [e * EH_TO_KJ_MOL for e in energies] if energies else [],
            "reactant_e": r_opt_e,
            "product_e": p_opt_e,
            "delta_e_eh": delta_e,
            "delta_e_kjmol": delta_kj,
        }
        (run_dir / "energy_curve.json").write_text(json.dumps(energy_curve, indent=2))

        # 10. 渲染 MP4（失败不影响 XYZ 轨迹）
        stage("render_mp4", 0.80)
        log("渲染 MP4...")
        mp4_path = run_dir / "trajectory.mp4"
        mp4_ok = True
        try:
            render_mp4(
                elements,
                frames,
                str(mp4_path),
                fps=max(4, min(15, n_frames // 2 + 4)),
                logger=log,
            )
        except Exception as e:
            mp4_ok = False
            log(f"MP4 渲染失败: {e}（不影响 XYZ 轨迹）")

        # 11. 完成 —— result.json 持久化（重启后历史可查）
        stage("done", 1.0)
        result = {
            "run_id": run_dir.name,
            "reactant_xyz": str(run_dir / "reactant_opt.xyz"),
            "product_xyz": str(run_dir / "product_opt.xyz"),
            "trajectory_xyz": str(traj_xyz_path),
            "mp4": str(mp4_path) if mp4_ok and mp4_path.exists() else None,
            "run_dir": str(run_dir),
            "energy_curve": energy_curve,
            "reactant_e": r_opt_e,
            "product_e": p_opt_e,
            "delta_e_eh": delta_e,
            "delta_e_kjmol": delta_kj,
            "thermo": thermo,
            "method": method,
            "basis": basis,
            "n_frames": len(frames),
            "n_atoms": len(elements),
            "elements": elements,
            "elapsed_s": round(time.time() - t0, 1),
        }
        try:
            (run_dir / "result.json").write_text(
                json.dumps({"payload": payload, "result": result}, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            log(f"result.json 持久化失败: {e}")
        log(f"=== run done（耗时 {result['elapsed_s']}s） ===")
        return result

    except Exception as e:
        log(f"!!! ERROR: {e}")
        log(traceback.format_exc())
        raise


# ============ 历史记录（供 UI「历史 run」列表使用） ============


def list_runs(runs_dir: Path, limit: int = 50) -> list:
    """列出磁盘上所有 run（按时间倒序），完成的带 result 摘要，中断的标记 interrupted。"""
    runs_dir = Path(runs_dir)
    out = []
    if not runs_dir.is_dir():
        return out
    for d in runs_dir.iterdir():
        if not d.is_dir():
            continue
        info: dict = {"run_id": d.name}
        rj = d / "result.json"
        if rj.is_file():
            try:
                data = json.loads(rj.read_text())
                r = data.get("result", {})
                info.update(
                    {
                        "status": "done",
                        "time": rj.stat().st_mtime,
                        "n_atoms": r.get("n_atoms"),
                        "delta_e_kjmol": r.get("delta_e_kjmol"),
                        "payload": data.get("payload", {}),
                    }
                )
            except Exception:
                info.update({"status": "done", "time": rj.stat().st_mtime})
        else:
            lg = d / "log.txt"
            info.update(
                {
                    "status": "interrupted",
                    "time": lg.stat().st_mtime if lg.is_file() else 0,
                }
            )
        out.append(info)
    out.sort(key=lambda x: x.get("time", 0), reverse=True)
    return out[:limit]


def reaction_side_counts(specs: list) -> Counter:
    """按 label 统计一侧分子出现次数（用于 UI 展示化学计量数，如 2 H₂O）。"""
    c: Counter = Counter()
    for s in specs:
        c[str(s.get("label") or s.get("smiles") or s.get("name") or "?")] += 1
    return c
