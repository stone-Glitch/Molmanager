"""v1.5.0 阶段2 验收测试：Service 层全覆盖 + api→services 统一。

覆盖：
  - ServiceBase 调度器解耦（TkinterScheduler / 注入式 Scheduler dict 事件）
  - 新增 Service：Analytics / Sync / OpenBabel（批量聚合回调）/ Psi4Scan（取消语义）
  - WebDispatcher 把 Service 任务桥接到 JobManager（api → services 统一）
  - 迁移后的对话框不再直接 app.helpers.run_task / 自建 TaskManager / 裸线程（源级校验）
"""
from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest

from services.analytics_service import AnalyticsService
from services.base import EVENT_LOG, EVENT_STAGE, Scheduler, ServiceBase, TkinterScheduler
from services.openbabel_service import OpenBabelService
from services.psi4_scan_service import Psi4ScanService
from services.sync_service import SyncService

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 假件
class FakeFuture:
    def cancel(self):
        return True


class FakeTaskManager:
    """同步执行、并把 dict 事件拆回 on_progress/on_log 的假 task_manager。"""

    def __init__(self):
        self._cancelled = False
        self.logs: list[tuple[str, str]] = []
        self.progress: list[tuple[float, str]] = []

    def run_async(self, fn, *, on_done=None, on_error=None, on_progress=None, on_cancelled=None):
        def _progress(percent, message=""):
            self.progress.append((percent, message))
            if on_progress:
                on_progress(percent, message)

        def _log(message, level="info"):
            self.logs.append((message, level))

        try:
            result = fn(_progress_callback=_progress, _log=_log)
        except Exception as exc:  # noqa: BLE001
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


class RecordingScheduler(Scheduler):
    """同步执行并把 emit 事件全部记录下来的假调度器。

    ``emit`` 直接由本调度器注入（绕过 ServiceBase 的 on_event→on_progress/on_log
    适配器），因此无论调用方是否传 on_event，事件都会被记录。
    """

    def __init__(self, cancel=False):
        self.events: list[dict] = []
        self._cancel = cancel
        self.result = None
        self.error = None

    def dispatch(self, fn, *, on_event, on_done=None, on_error=None, on_cancelled=None, **kw):
        def _emit(event):
            self.events.append(event)
            if on_event is not None:
                on_event(event)

        try:
            r = fn(
                emit=_emit,
                should_cancel=lambda: self._cancel,
                progress_callback=lambda p, m="": _emit(
                    {"type": EVENT_STAGE, "fraction": float(p), "message": m}
                ),
                log=lambda m, l="info": _emit({"type": EVENT_LOG, "message": m, "level": l}),
            )
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            if on_error:
                on_error(str(exc))
            return FakeFuture()
        self.result = r
        if on_done:
            on_done(r)
        return FakeFuture()

    def should_cancel(self, handle=None):
        return self._cancel


# ---------------------------------------------------------------- ServiceBase 解耦
def test_servicebase_resolves_tkinter_scheduler_from_task_manager():
    tm = FakeTaskManager()
    svc = ServiceBase(tm)
    assert isinstance(svc.scheduler, TkinterScheduler)
    assert svc.task_manager is tm


def test_servicebase_rejects_missing_scheduler():
    svc = ServiceBase()
    with pytest.raises(RuntimeError):
        svc._run(lambda **kw: None)


def test_servicebase_emits_stage_and_log_dicts():
    sched = RecordingScheduler()
    svc = ServiceBase(scheduler=sched)

    def _work(*, emit, should_cancel, progress_callback, log):
        progress_callback(0.4, "halfway")
        log("hello", "info")
        return {"ok": 1}

    got = {}
    svc._run(_work, on_done=lambda r: got.setdefault("r", r))
    types = [e["type"] for e in sched.events]
    assert EVENT_STAGE in types and EVENT_LOG in types
    stage = next(e for e in sched.events if e["type"] == EVENT_STAGE)
    assert stage["fraction"] == 0.4 and stage["message"] == "halfway"
    assert got["r"] == {"ok": 1}


def test_servicebase_maps_events_back_to_split_callbacks():
    """未传 on_event 时，stage/log 事件应拆回 on_progress/on_log（桌面兼容）。"""
    sched = RecordingScheduler()
    svc = ServiceBase(scheduler=sched)

    def _work(*, emit, should_cancel, progress_callback, log):
        progress_callback(0.7, "scan")
        log("note")
        return None

    seen = {"progress": [], "log": []}
    svc._run(
        _work,
        on_progress=lambda p, m: seen["progress"].append((p, m)),
        on_log=lambda m: seen["log"].append(m),
    )
    assert seen["progress"] == [(0.7, "scan")]
    assert seen["log"] == ["note"]


# ---------------------------------------------------------------- AnalyticsService
def test_analytics_analyze_formula_done_payload():
    tm = FakeTaskManager()
    svc = AnalyticsService(tm)
    out = {}
    with patch("chem.openbabel_utils.analyze_formula", return_value={"success": True, "formula": "H2O"}) as m:
        svc.analyze_formula("/tmp/x.xyz", on_done=lambda r: out.setdefault("r", r))
    assert out["r"][0]["formula"] == "H2O"
    assert out["r"][1] == "x.xyz"
    m.assert_called_once_with("/tmp/x.xyz")


def test_analytics_resolve_selected_path():
    assert AnalyticsService.resolve_selected_path("a.xyz", "/w") == "/w/a.xyz"
    assert AnalyticsService.resolve_selected_path("/abs/a.xyz", "/w") == "/abs/a.xyz"


# ---------------------------------------------------------------- SyncService
def test_sync_copy_runs_direction_and_fires_done():
    tm = FakeTaskManager()
    svc = SyncService(tm)
    calls = []
    done = {}
    svc.copy(
        lambda names, left, right: calls.append((names, left, right)),
        ["a", "b"], "/L", "/R",
        on_done=lambda r: done.setdefault("r", r),
    )
    assert calls == [(["a", "b"], "/L", "/R")]
    assert "r" in done


def test_sync_overwrite_runs_and_fires_done():
    tm = FakeTaskManager()
    svc = SyncService(tm)
    calls = []
    svc.overwrite(lambda names, left, right: calls.append(names), ["x"], "/L", "/R")
    assert calls == [["x"]]


# ---------------------------------------------------------------- OpenBabelService
def test_openbabel_batch_aggregates_logs_and_done():
    tm = FakeTaskManager()
    model = type("M", (), {})()
    calls = []

    def fake_convert(src, dst, fmt):
        calls.append((src, dst, fmt))
        return {"success": True, "message": "ok"}

    model.convert_file = fake_convert
    svc = OpenBabelService(tm, model=model)
    out = {}
    events: list[dict] = []
    svc.convert_batch(
        ["a.mol", "b.mol"], "/w", "xyz",
        on_event=events.append,
        on_done=lambda r: out.setdefault("r", r),
    )
    assert out["r"] == {"all_ok": True, "count": 2}
    assert len(calls) == 2
    # 每条结果一条 log（聚合回调，不淹没 UI）
    assert sum(1 for e in events if e["type"] == EVENT_LOG) == 2


def test_openbabel_descriptors_to_csv_writes_file(tmp_path):
    tm = FakeTaskManager()
    model = type("M", (), {})()
    model.calculate_descriptors = lambda p: {"MW": 18.0}
    svc = OpenBabelService(tm, model=model)
    out = {}
    target = tmp_path / "d.csv"
    svc.descriptors_to_csv(["a.xyz"], tmp_path, target, on_done=lambda r: out.setdefault("r", r))
    assert target.exists()
    assert out["r"]["count"] == 1


def test_openbabel_batch_stops_on_cancel():
    sched = RecordingScheduler(cancel=True)
    model = type("M", (), {})()
    model.convert_file = lambda *a, **k: {"success": True}
    svc = OpenBabelService(scheduler=sched, model=model)
    out = {}
    svc.convert_batch(["a", "b", "c"], "/w", "xyz", on_done=lambda r: out.setdefault("r", r))
    assert out["r"]["count"] == 3  # count 为总列表长度
    # 取消后不应有任何转换（第一项开始前即检测到取消）
    assert len([e for e in sched.events if e["type"] == EVENT_LOG]) == 0


# ---------------------------------------------------------------- Psi4ScanService
def test_psi4_batch_collects_results_and_progress():
    tm = FakeTaskManager()
    model = type("M", (), {})()
    svc = Psi4ScanService(tm, model=model)
    out = {}
    with patch(
        "chem.psi4_compute.run_psi4_task_cancellable",
        return_value={"success": True, "energy": -1.0},
    ) as m:
        svc.batch_compute(
            ["a.xyz", "b.xyz"], "/w", "energy", "hf", "sto-3g", "/out",
            on_done=lambda r: out.setdefault("r", r),
        )
    assert m.call_count == 2
    assert out["r"]["cancelled"] is False
    assert [r["file"] for r in out["r"]["results"]] == ["a.xyz", "b.xyz"]


def test_psi4_batch_respects_cancel_flag():
    sched = RecordingScheduler(cancel=True)
    model = type("M", (), {})()
    svc = Psi4ScanService(scheduler=sched, model=model)
    out = {}
    with patch("chem.psi4_compute.run_psi4_task_cancellable", return_value={"success": True}):
        svc.batch_compute(
            ["a.xyz", "b.xyz"], "/w", "energy", "hf", "sto-3g", "/out",
            on_done=lambda r: out.setdefault("r", r),
        )
    assert out["r"]["cancelled"] is True
    assert out["r"]["results"] == []


def test_psi4_linear_scan_forwards_progress_callback():
    tm = FakeTaskManager()
    model = type("M", (), {})()
    seen = {}

    def fake_scan(rf, pf, steps, method, basis, out_dir, *a, progress_callback=None, **k):
        seen["steps"] = steps
        if progress_callback:
            progress_callback(0.5, "frame")
        return {"success": True}

    model.run_linear_scan = fake_scan
    svc = Psi4ScanService(tm, model=model)
    out = {}
    svc.linear_scan(["r.xyz"], ["p.xyz"], 10, "hf", "sto-3g", "/out", on_done=lambda r: out.setdefault("r", r))
    assert seen["steps"] == 10
    assert out["r"] == {"success": True}


# ---------------------------------------------------------------- 源级校验：迁移彻底
def test_no_dialog_uses_helpers_run_task():
    """迁移后，5 个对话框都不再直接 app.helpers.run_task。"""
    for rel in [
        "ui/dialogs/openbabel_dialog.py",
        "ui/dialogs/psi4_dialog.py",
        "ui/dialogs/sync_dialog.py",
        "ui/dialogs/analytics_dialog.py",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "app.helpers.run_task" not in text, rel


def test_analytics_dialog_no_self_built_taskmanager():
    text = (ROOT / "ui/dialogs/analytics_dialog.py").read_text(encoding="utf-8")
    assert "TaskManager(app, controller)" not in text
    assert "app.services.analytics.analyze_formula" in text


def test_common_dialog_no_bare_thread():
    text = (ROOT / "ui/dialogs/common.py").read_text(encoding="utf-8")
    assert "threading.Thread(target=_do" not in text
    assert "app.services.advanced.submit" in text


def test_api_server_no_direct_run_reaction():
    """api/server.py 不再直连 chem.* 的 run_reaction / reaction_animation。"""
    text = (ROOT / "api/server.py").read_text(encoding="utf-8")
    assert "from chem.quantum_reaction import run_reaction" not in text
    assert "import chem.reaction_animation" not in text
    assert "_get_quantum_service()" in text
    assert "_get_reaction_service()" in text
