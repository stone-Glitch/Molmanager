"""chem.quantum_reaction 子包回归测试（融合自 Quantum Reaction Visualizer）。

覆盖纯逻辑部分（不依赖 psi4 / rdkit / ffmpeg）：
- ``:N`` 多重度语法解析与组合多重度
- 预设反应库结构完整性（O₂ 三线态标注等化学正确性约定）
- 配平检查、XYZ 解析、Kabsch 对齐与插值
- psi4 包装的 SCF 选项选择 / 缓存 key 稳定性 / 单原子解析热化学理论校验
- IQmol XyzParser 兼容性解析往返
- runner 端到端编排（stub 掉 psi4 计算后跑通全流程：配平预检、取消、产物落盘）
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from chem.quantum_reaction import (
    REACTIONS,
    CancelledError,
    combined_mult,
    get_reaction,
    list_reactions,
    list_runs,
    make_trajectory,
    normalize_atom_counts,
    parse_species_token,
)
from chem.quantum_reaction.animate import kabsch_align, linear_interpolate
from chem.quantum_reaction.iqmol_check import parse_like_iqmol
from chem.quantum_reaction.molbuild import PRESET_XYZ, parse_xyz
from chem.quantum_reaction.quantum import (
    EH_TO_KJ_MOL,
    _opt_cache_key,
    _scf_options,
    _single_atom_thermo,
)
from chem.quantum_reaction.runner import _safe_mult, _spec_list_from_tokens, run_reaction

# ---------------- 多重度语法 ----------------


def test_parse_species_token_mult_suffix():
    assert parse_species_token("O=O:3") == ("O=O", 3)
    assert parse_species_token("[O]:2") == ("[O]", 2)
    assert parse_species_token("H2: 2") == ("H2", 2)


def test_parse_species_token_no_suffix():
    assert parse_species_token("O") == ("O", 1)
    assert parse_species_token("[H][H]") == ("[H][H]", 1)
    assert parse_species_token("  C  ") == ("C", 1)


def test_parse_species_token_invalid_mult_fallback():
    # :0 → 安全剥离并回退单重态（README 约定）
    base, mult = parse_species_token("X:0")
    assert base == "X" and mult == 1
    # :abc 不匹配数字后缀 → 原样保留，交给 rdkit 报「无法解析 SMILES」
    assert parse_species_token("X:abc") == ("X:abc", 1)


def test_safe_mult_and_combined_mult():
    assert _safe_mult({}) == 1
    assert _safe_mult({"multiplicity": 3}) == 3
    assert _safe_mult({"multiplicity": "bad"}) == 1
    assert _safe_mult({"multiplicity": 0}) == 1
    # 2H₂(1) + O₂(3) → 3（自旋守恒链式 |M1-M2|+1）
    specs = [{}, {}, {"multiplicity": 3}]
    assert combined_mult(specs) == 3
    # 2O₃(1) → 1
    assert combined_mult([{}, {}]) == 1
    # 非法 spec 不炸：None 回退单重态，单分子组合 = |1-1|+1 = 1
    assert combined_mult([None]) == 1
    # 双分子含非法项：1 与回退的 1 组合仍为 1
    assert combined_mult([{"multiplicity": 3}, None]) == 3  # |3-1|+1


def test_spec_list_from_tokens():
    specs = _spec_list_from_tokens(["O=O:3", " O ", ""])
    assert specs[0] == {"smiles": "O=O", "label": "O=O", "multiplicity": 3}
    assert specs[1] == {"smiles": "O", "label": "O"}
    assert len(specs) == 2


# ---------------- 预设反应库 ----------------


def test_reactions_library_structure():
    assert len(REACTIONS) == 8
    ids = [r["id"] for r in REACTIONS]
    assert len(set(ids)) == len(ids)
    for r in REACTIONS:
        assert r["reactants"] and r["products"], r["id"]
        for spec in r["reactants"] + r["products"]:
            assert "smiles" in spec and spec["smiles"], r["id"]
            if "multiplicity" in spec:
                assert int(spec["multiplicity"]) >= 1


def test_reactions_o2_triplet_annotation():
    """化学正确性：所有 O₂ spec 必须标三线态（singlet O₂ 是激发态，ΔE 系统性偏差）。"""
    for r in REACTIONS:
        for side in (r["reactants"], r["products"]):
            for spec in side:
                if spec["smiles"] == "O=O":
                    assert spec.get("multiplicity") == 3, r["id"]


def test_list_and_get_reaction():
    summaries = list_reactions()
    assert len(summaries) == 8
    assert get_reaction("water")["name"] == "水生成"
    assert get_reaction("nope") is None


# ---------------- 配平 / XYZ / 动画 ----------------


def test_normalize_atom_counts():
    ok, msg = normalize_atom_counts(["H", "H", "O"], ["O", "H", "H"])
    assert ok and "OK" in msg
    ok, msg = normalize_atom_counts(["H", "H"], ["O", "H", "H"])
    assert not ok and "未配平" in msg


def test_preset_xyz_parseable():
    for name, xyz in PRESET_XYZ.items():
        elems, coords = parse_xyz(xyz)
        assert len(elems) == 2 and coords.shape == (2, 3), name


def test_kabsch_align_rotation_translation_invariant():
    import numpy as np

    rng = np.random.default_rng(7)
    p = rng.normal(size=(10, 3)) * 2.0
    # 对 P 做已知旋转 + 平移得到 Q；Kabsch 应恢复到 P（RMSD≈0）
    theta = 0.7
    R = np.array(
        [
            [math.cos(theta), -math.sin(theta), 0],
            [math.sin(theta), math.cos(theta), 0],
            [0, 0, 1],
        ]
    )
    q = p @ R.T + np.array([3.0, -2.0, 1.5])
    q_aligned = kabsch_align(p, q)
    rmsd = float(np.sqrt(((q_aligned - p) ** 2).sum(axis=1).mean()))
    assert rmsd < 1e-8


def test_linear_interpolate_endpoints_and_count():
    import numpy as np

    a, b = np.zeros((2, 3)), np.ones((2, 3))
    frames = linear_interpolate(a, b, 7)
    assert frames.shape == (7, 2, 3)
    assert frames[0].sum() == 0 and frames[-1].sum() == 6
    # 最少 2 帧
    assert linear_interpolate(a, b, 1).shape[0] == 2


def test_make_trajectory_h2():
    r_xyz = PRESET_XYZ["H2"]
    # 产物：同元素、平移后的 H2（模拟断键重排）
    p_xyz = "2\nproduct\nH 5.0 5.0 5.0\nH 5.0 5.0 5.8\n"
    elements, frames = make_trajectory(r_xyz, p_xyz, n_frames=9)
    assert elements == ["H", "H"]
    assert len(frames) == 9
    assert frames[0].shape == (2, 3)


# ---------------- quantum 包装（纯逻辑部分） ----------------


def test_eh_to_kj_mol_constant():
    assert abs(EH_TO_KJ_MOL - 2625.499638) < 1e-4


def test_opt_cache_key_stable_and_distinct():
    k1 = _opt_cache_key("3\nm\nO 0 0 0\nH 0 0 1\nH 0 1 0\n", "hf", "sto-3g", 0, 1)
    k2 = _opt_cache_key("3\nm\nO 0 0 0\nH 0 0 1\nH 0 1 0\n", "hf", "sto-3g", 0, 1)
    k3 = _opt_cache_key("3\nm\nO 0 0 0\nH 0 0 1\nH 0 1 0\n", "hf", "sto-3g", 0, 3)
    assert k1 == k2
    assert k1 != k3


def test_scf_options_reference_selection():
    # 闭壳层 → RHF/RKS + SOSCF
    o1 = _scf_options("hf", 1)
    assert o1["REFERENCE"] == "RHF" and o1["SOSCF"] is True
    o2 = _scf_options("b3lyp", 1)
    assert o2["REFERENCE"] == "RKS"
    # 开壳层 → UHF/UKS + 显式关闭 SOSCF（psi4 全局选项残留防护）
    o3 = _scf_options("hf", 3)
    assert o3["REFERENCE"] == "UHF" and o3["SOSCF"] is False
    o4 = _scf_options("b3lyp", 3)
    assert o4["REFERENCE"] == "UKS"


def test_single_atom_thermo_hydrogen_theory():
    """H 原子解析热化学：G−E 与 Sackur–Tetrode 理论一致（README 声称 0.01 kJ/mol 内）。"""

    class _FakeMol:
        def mass(self, i):
            return 1.008  # amu

    E = -0.5
    res = _single_atom_thermo(_FakeMol(), E)
    # U = E + 3/2 RT；H = E + 5/2 RT（kJ/mol）
    RT = 8.314462618 * 298.15 / 1000.0
    assert abs((res["U"] - E) * EH_TO_KJ_MOL - 1.5 * RT) < 1e-6
    assert abs((res["H"] - E) * EH_TO_KJ_MOL - 2.5 * RT) < 1e-6
    assert res["G"] < res["H"]  # G = H − TS
    # H−E 与 G−E 差为 TS（正值）
    ts = (res["H"] - res["G"]) * EH_TO_KJ_MOL
    # JANAF：S°(H, g, 298.15 K, 1 bar) ≈ 108.96 J/(mol·K) → TS ≈ 32.5 kJ/mol
    assert 30.0 < ts < 36.0


# ---------------- IQmol 兼容解析 ----------------


def _write_traj(path: Path, n_frames=3, energies=(None, -1.2345678, float("nan"))):
    lines = []
    for i in range(n_frames):
        e = energies[i % len(energies)]
        comment = f"frame {i + 1}/{n_frames}"
        if e is not None and e == e:
            comment += f" E = {e:.8f} Eh"
        lines.append("2")
        lines.append(comment)
        lines.append("H 0.0 0.0 0.0")
        lines.append("H 0.0 0.0 0.74")
    path.write_text("\n".join(lines) + "\n")


def test_iqmol_parse_roundtrip(tmp_path):
    p = tmp_path / "trajectory.xyz"
    _write_traj(p, 3, (None, -1.2345678, None))
    frames = parse_like_iqmol(p)
    assert len(frames) == 3
    assert frames[0][1] is None  # 无能量注释 → None（IQmol 记 0）
    assert abs(frames[1][1] - (-1.2345678)) < 1e-6
    assert frames[0][2] == [("H", 0.0, 0.0, 0.0), ("H", 0.0, 0.0, 0.74)]


def test_iqmol_parse_rejects_truncated(tmp_path):
    p = tmp_path / "bad.xyz"
    p.write_text("2\nc\nH 0 0 0\n")  # 声明 2 原子只给 1 行坐标
    with pytest.raises(ValueError):
        parse_like_iqmol(p)


# ---------------- runner 编排（stub 掉 psi4） ----------------

H2_XYZ = PRESET_XYZ["H2"]


@pytest.fixture()
def stub_psi4(monkeypatch):
    """把 psi4 依赖替换成确定性 stub，端到端跑通编排逻辑。

    smiles_to_xyz 按输入返回不同分子（H₂/O₂），保证配平预检仍基于真实元素组成。
    """
    import chem.quantum_reaction.runner as runner

    xyz_by_smiles = {"O=O": PRESET_XYZ["O2"]}
    monkeypatch.setattr(runner, "smiles_to_xyz", lambda s: xyz_by_smiles.get(s, H2_XYZ))
    calls = {"opt": 0, "freq": 0}

    def _fake_opt(xyz, method, basis, charge=0, multiplicity=1, logger=None, cache=True, max_retries=2):
        calls["opt"] += 1
        return xyz.strip() + "\n", -1.0 - 0.001 * multiplicity, None

    def _fake_freq(xyz, method, basis, charge=0, multiplicity=1, logger=None, cache=True):
        calls["freq"] += 1
        return {"E_elec": -1.0, "U": -0.999, "H": -0.998, "G": -1.001}

    monkeypatch.setattr(runner, "optimize_geometry", _fake_opt)
    monkeypatch.setattr(runner, "frequency_analysis", _fake_freq)
    monkeypatch.setattr(runner, "render_mp4", lambda *a, **k: str(a[2]) if len(a) > 2 else "")
    return calls


def test_run_reaction_balance_check_fails_early(stub_psi4, tmp_path):
    """配平预检：H₂ → O₂ 两侧原子不一致，必须在进 psi4 优化前报错。"""
    payload = {"custom": {"reactants": ["[H][H]"], "products": ["O=O"]}}
    with pytest.raises(ValueError) as ei:
        run_reaction(payload, run_dir=tmp_path / "run1")
    assert "配平" in str(ei.value)


def test_run_reaction_end_to_end(stub_psi4, tmp_path):
    """2H₂ → 2H₂（stub 计算）：全流程跑通 + 产物落盘 + IQmol 往返一致。"""
    payload = {
        "custom": {"reactants": ["[H][H]", "[H][H]"], "products": ["[H][H]", "[H][H]"]},
        "method": "hf",
        "basis": "sto-3g",
        "n_frames": 6,
        "do_thermo": True,
        "do_traj_energy": False,
    }
    logs: list = []
    stages: list = []
    res = run_reaction(
        payload,
        run_dir=tmp_path / "run2",
        on_log=logs.append,
        on_stage=lambda s, p: stages.append(s),
    )
    # 结果字段
    assert res["method"] == "hf" and res["basis"] == "sto-3g"
    assert res["n_atoms"] == 4 and res["n_frames"] == 6
    assert res["delta_e_kjmol"] == pytest.approx(0.0, abs=1e-9)
    assert res["thermo"]["delta_h_kjmol"] == pytest.approx(0.0, abs=1e-9)
    assert res["elapsed_s"] >= 0
    # 产物落盘
    run_dir = Path(res["run_dir"])
    assert (run_dir / "reactant_opt.xyz").exists()
    assert (run_dir / "product_opt.xyz").exists()
    assert (run_dir / "result.json").exists()
    ec = json.loads((run_dir / "energy_curve.json").read_text())
    assert ec["delta_e_kjmol"] == pytest.approx(0.0, abs=1e-9)
    # IQmol 往返：初末帧带能量，中间帧不带
    frames = parse_like_iqmol(run_dir / "trajectory.xyz")
    assert len(frames) == 6
    assert frames[0][1] is not None and frames[-1][1] is not None
    assert all(f[1] is None for f in frames[1:-1])
    # 日志与阶段回调被调用
    assert any("ΔE" in ln for ln in logs)
    assert "done" in stages


def test_run_reaction_cancelled(stub_psi4, tmp_path):
    payload = {"custom": {"reactants": ["[H][H]"], "products": ["[H][H]"]}}
    with pytest.raises(CancelledError):
        run_reaction(payload, run_dir=tmp_path / "run3", should_cancel=lambda: True)


def test_list_runs_orders_and_flags(tmp_path):
    import time

    d1 = tmp_path / "r1"
    d1.mkdir()
    (d1 / "result.json").write_text(json.dumps({"payload": {}, "result": {"n_atoms": 2, "delta_e_kjmol": -10.0}}))
    time.sleep(0.02)
    d2 = tmp_path / "r2"
    d2.mkdir()
    (d2 / "log.txt").write_text("interrupted\n")
    runs = list_runs(tmp_path)
    assert runs[0]["run_id"] == "r2" and runs[0]["status"] == "interrupted"
    assert runs[1]["run_id"] == "r1" and runs[1]["delta_e_kjmol"] == pytest.approx(-10.0)
    assert list_runs(tmp_path / "missing") == []
