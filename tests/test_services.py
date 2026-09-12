"""Service 层试点验收测试（框架无关，无需启动 GUI / 真实计算）。

覆盖：
  - 三个 Service 复用注入的共享 task_manager（Bug1 关联断言在 advanced_tools_dialog 源级校验）
  - 回调透传：on_done 拿到领域函数返回值、on_error 捕获异常
  - ReactionService 的 preview_frame / start_animation 收口领域调用（mock 掉 ra.* 避免真实 ffmpeg/obabel）
  - Bug1（advanced_tools_dialog 不再自建 TaskManager）/ Bug2（reaction_dialog 不再写 app._anim_*）源级校验
"""
from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest

from services.advanced_tools_service import AdvancedToolsService
from services.quantum_reaction_service import QuantumReactionService
from services.reaction_service import ReactionService, _do_generate, _do_preview


class FakeFuture:
    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        return True


class FakeTaskManager:
    """同步执行 run_async 的假 task_manager：捕获调用、按 after(0) 语义直接回调。"""

    def __init__(self):
        self.calls = []
        self._cancelled = False

    def run_async(self, fn, *, on_done=None, on_error=None, on_progress=None):
        self.calls.append((fn, on_done, on_error, on_progress))

        def _prog(*_a):
            if on_progress:
                on_progress(0.5, "tick")

        try:
            result = fn(_progress_callback=_prog, _log=lambda *a: None)
        except Exception as exc:  # noqa: BLE001 - 模拟 run_async 的 on_error 分支
            if on_error:
                on_error(str(exc))
            return FakeFuture()
        if on_done:
            on_done(result)
        return FakeFuture()

    def request_cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled


# ----------------------------------------------------------------------------
# 基础：Service 复用注入的共享 task_manager（Bug1 的“共享”前提）
# ----------------------------------------------------------------------------
def test_services_inject_shared_task_manager():
    tm = FakeTaskManager()
    assert QuantumReactionService(tm).task_manager is tm
    assert AdvancedToolsService(tm).task_manager is tm
    assert ReactionService(tm).task_manager is tm


# ----------------------------------------------------------------------------
# QuantumReactionService
# ----------------------------------------------------------------------------
def test_quantum_compute_transfers_result_via_on_done():
    tm = FakeTaskManager()
    svc = QuantumReactionService(tm)
    payload = {"reaction_id": "X"}
    run_dir = pathlib.Path("/tmp/run_x")

    captured = {}

    with patch("chem.quantum_reaction.run_reaction", return_value={"delta_e_kjmol": -10.0}) as mock_run:
        svc.compute(
            payload,
            run_dir,
            on_log=lambda m: None,
            on_stage=lambda n, p: None,
            should_cancel=lambda: False,
            on_done=lambda r: captured.setdefault("r", r),
            on_error=lambda e: captured.setdefault("err", e),
        )

    # 领域函数被调用且参数透传
    assert mock_run.call_count == 1
    args, kwargs = mock_run.call_args
    assert args[0] is payload
    assert kwargs["run_dir"] == run_dir
    assert callable(kwargs["on_log"]) and callable(kwargs["on_stage"])
    # on_done 收到返回值
    assert captured["r"] == {"delta_e_kjmol": -10.0}


def test_quantum_compute_routes_exception_to_on_error():
    tm = FakeTaskManager()
    svc = QuantumReactionService(tm)
    captured = {}

    with patch("chem.quantum_reaction.run_reaction", side_effect=ValueError("boom")):
        svc.compute(
            {},
            pathlib.Path("/tmp/x"),
            on_done=lambda r: captured.setdefault("r", r),
            on_error=lambda e: captured.setdefault("err", e),
        )

    assert "err" in captured
    assert "boom" in captured["err"]


# ----------------------------------------------------------------------------
# AdvancedToolsService
# ----------------------------------------------------------------------------
def test_advanced_submit_runs_fn_and_returns_future():
    tm = FakeTaskManager()
    svc = AdvancedToolsService(tm)

    result = {}
    future = svc.submit(lambda: 42, on_done=lambda r: result.setdefault("r", r))
    assert future is not None
    assert result["r"] == 42


# ----------------------------------------------------------------------------
# ReactionService（mock ra.* 避免真实 ffmpeg/obabel）
# ----------------------------------------------------------------------------
def test_reaction_preview_single_pair():
    tm = FakeTaskManager()
    svc = ReactionService(tm)
    out = {}

    with patch(
        "chem.reaction_animation.preview_first_frame",
        return_value={"success": True, "output": "p.png"},
    ) as mock_prev:
        svc.preview_frame(
            ["a.xyz"], ["b.xyz"], 5.0, "/tmp/preview.png",
            on_done=lambda r: out.setdefault("r", r),
        )

    assert out["r"] == {"success": True, "output": "p.png"}
    mock_prev.assert_called_once()


def test_reaction_preview_multi_pair_uses_concat_helpers():
    tm = FakeTaskManager()
    svc = ReactionService(tm)
    out = {}

    with patch(
        "chem.reaction_animation.preview_first_frame",
        return_value={"success": True, "output": "p.png"},
    ), patch(
        "chem.reaction_animation._concat_xyz_files",
        side_effect=lambda *a, **k: (["C"], ["C"], "C"),
    ), patch(
        "chem.reaction_animation._auto_reorder_atoms",
        return_value=(["C"], "C"),
    ), patch(
        "chem.reaction_animation._write_xyz", return_value="xyz"
    ):
        svc.preview_frame(
            ["a1.xyz", "a2.xyz"], ["b1.xyz"], 5.0, "/tmp/preview.png",
            on_done=lambda r: out.setdefault("r", r),
        )

    assert out["r"]["success"] is True


def test_reaction_start_animation_collects_messages():
    tm = FakeTaskManager()
    svc = ReactionService(tm)
    out = {}

    with patch(
        "chem.reaction_animation.generate_reaction_animation",
        return_value={"success": True, "output": "v.mp4", "n_frames": 10},
    ), patch(
        "chem.reaction_animation.generate_xyz_trajectory",
        return_value={"success": True, "output": "t.xyz", "n_frames": 10},
    ):
        svc.start_animation(
            reactants=["a.xyz"], products=["b.xyz"], out="v.mp4", traj="t.xyz",
            mode="forward", fmt="mp4", resolution="hd", traj_fmt="xyz",
            spacing=5.0, steps=15, smooth=True, ffmpeg="ffmpeg", fps=24,
            on_done=lambda r: out.setdefault("r", r),
        )

    res = out["r"]
    assert res["viz_ok"] is True and res["traj_ok"] is True
    assert any("可视化" in m for m in res["msgs"])
    assert any("IQmol 轨迹" in m for m in res["msgs"])


def test_do_generate_handles_atom_reorder_failure_gracefully():
    tm = FakeTaskManager()
    svc = ReactionService(tm)
    out = {}

    with patch(
        "chem.reaction_animation.generate_reaction_animation",
        return_value={"success": False, "error": "fail"},
    ), patch(
        "chem.reaction_animation._concat_xyz_files",
        side_effect=lambda *a, **k: (["C"], ["C"], "C"),
    ), patch(
        "chem.reaction_animation._auto_reorder_atoms",
        side_effect=RuntimeError("reorder boom"),
    ), patch(
        "chem.psi4.utils._write_xyz", return_value="xyz"
    ):
        svc.start_animation(
            reactants=["a1.xyz", "a2.xyz"], products=["b1.xyz"], out="v.mp4", traj="",
            mode="bounce", fmt="mp4", resolution="sd", traj_fmt="xyz",
            spacing=5.0, steps=15, smooth=False, ffmpeg="ffmpeg", fps=24,
            on_done=lambda r: out.setdefault("r", r),
        )

    res = out["r"]
    assert res["viz_ok"] is False
    assert any("原子对齐失败" in m for m in res["msgs"])


# ----------------------------------------------------------------------------
# Bug1 / Bug2 源级校验（dialog 不再直接污染 / 自建线程池）
# ----------------------------------------------------------------------------
def test_bug1_advanced_dialog_no_self_built_taskmanager():
    src = pathlib.Path(__file__).resolve().parents[1] / "ui" / "dialogs" / "advanced_tools_dialog.py"
    text = src.read_text(encoding="utf-8")
    # 不再自建独立 TaskManager 实例
    assert "TaskManager(app, controller=None)" not in text
    # 改用共享 service 派发
    assert "app.services.advanced.submit" in text


def test_bug2_reaction_dialog_no_global_anim_assignment():
    src = pathlib.Path(__file__).resolve().parents[1] / "ui" / "dialogs" / "reaction_dialog.py"
    text = src.read_text(encoding="utf-8")
    # 不再往 MainView 写 _anim_* 全局状态
    assert "app._anim_dialog" not in text
    assert "app._anim_state" not in text
    assert "app._anim_r_list" not in text
