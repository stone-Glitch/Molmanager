#!/usr/bin/env python3
"""utils/structure_score —— 结构美观度评分测试。

红线守卫：缺失字段只影响相关项、不编造数值；分数收束 [0, 100]；免责声明恒在。
"""

from utils.structure_score import score_structure


_BENZENE = {
    "molecular_weight": 78.11,
    "logP": 1.69,
    "tpsa": 0.0,
    "heavy_atoms": 6,
    "bonds": 6,
    "hbd": 0,
    "hba": 0,
    "rotors": 0,
    "rings": 1,
    "formula": "C6H6",
    "num_atoms": 6,
}


def test_benzene_scores_high() -> None:
    r = score_structure(_BENZENE)
    assert 70 <= r["score"] <= 100
    assert r["grade"].startswith(("A", "B"))
    # 各加分项的说明都在
    joined = "\n".join(r["notes"])
    assert "环状骨架" in joined
    assert "无可旋转键" in joined
    assert "C6H6" in joined


def test_full_chain_molecule_scores_lower() -> None:
    # 长柔性链 + 极性大 + 分子量大 → 应明显低于苯
    ugly = dict(_BENZENE, molecular_weight=650.0, tpsa=180.0, rotors=12, hbd=5, hba=6, rings=0, formula="C40H80O10")
    a, b = score_structure(ugly), score_structure(_BENZENE)
    assert a["score"] < b["score"]
    assert a["grade"].startswith(("C", "D"))


def test_missing_descriptors_never_crash_and_never_fabricate() -> None:
    r = score_structure({})
    assert isinstance(r["score"], float) and r["score"] == 50.0  # 全缺 → 中性基线
    joined = "\n".join(r["notes"])
    for marker in ("环数未提供", "可旋转键数未提供", "TPSA 未提供", "氢键供/受体未提供", "分子量未提供"):
        assert marker in joined
    assert "免责声明" in joined


def test_partial_missing_only_affects_related_item() -> None:
    # 只给 rings=1：环加分生效，其余如实标注未提供
    r = score_structure({"rings": 1})
    assert r["score"] == 58.0  # 50 基线 + 环 8
    assert "可旋转键数未提供" in "\n".join(r["notes"])


def test_score_clamped_to_0_100() -> None:
    # 极端负分：柔性强极性巨分子 → 分数不允许低于 0
    awful = dict(_BENZENE, molecular_weight=9999.0, tpsa=999.0, rotors=99, hbd=50, hba=50, rings=0)
    r = score_structure(awful)
    assert 0.0 <= r["score"] <= 100.0


def test_grade_thresholds() -> None:
    """分档映射一致性：grade 必须与 score 的阈值判断严格对应。

    注：基线 50 + 最高加分 23 = 73，A 档（>=85）在当前评分尺度下不可达——
    这不是 bug（启发式尺度），故不强行构造 A，只验证映射一致性。
    """
    samples = [_BENZENE, {}, {"rings": 1, "rotors": 0, "tpsa": 0.0, "hbd": 0, "hba": 0, "molecular_weight": 150.0}]
    # 长柔性链巨分子 → 明显低于苯
    ugly = dict(_BENZENE, molecular_weight=650.0, tpsa=180.0, rotors=12, hbd=5, hba=6, rings=0)
    samples.append(ugly)
    grades = set()
    for d in samples:
        r = score_structure(d)
        s = r["score"]
        expect = "A（很规整）" if s >= 85 else "B（较规整）" if s >= 70 else "C（一般）" if s >= 55 else "D（复杂度/极性偏高）"
        assert r["grade"] == expect, (s, r["grade"])
        grades.add(r["grade"])
    assert len(grades) >= 2  # 至少覆盖两个等级，说明分档在动
    assert score_structure(_BENZENE)["grade"].startswith("B")
    assert score_structure(ugly)["grade"].startswith(("C", "D"))


def test_disclaimer_always_last() -> None:
    for d in (_BENZENE, {}, {"rings": None}):
        r = score_structure(d)  # type: ignore[arg-type]
        assert r["notes"][-1].startswith("免责声明")
