#!/usr/bin/env python3
"""core/task_manager —— 后台任务管理器测试。

用 FakeApp（仅实现 after/config_data 契约）替代真实 Tk；
模式1手动驱动 _poll_results，模式2靠 add_done_callback 后手动派发。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from core.task_manager import TaskManager


class FakeApp:
    """最小 app 契约：after() 只记录回调，测试手动执行（等价主线程调度）。"""

    def __init__(self) -> None:
        self.config_data: dict[str, Any] = {"queue_concurrency": 2}
        self.scheduled: list[tuple[int, Any]] = []
        self.done_calls: list[tuple] = []
        self.error_calls: list[str] = []
        self.cancelled_calls: int = 0

    def after(self, ms: int, cb=None) -> None:
        self.scheduled.append((ms, cb))

    def on_task_done(self, result, job=None) -> None:
        self.done_calls.append((result, job))

    def on_task_error(self, error, job=None) -> None:
        self.error_calls.append(error)

    def on_task_cancelled(self, job=None) -> None:
        self.cancelled_calls += 1

    def run_scheduled(self) -> None:
        pending, self.scheduled = self.scheduled, []
        for _ms, cb in pending:
            cb()


@pytest.fixture()
def tm():
    app = FakeApp()
    mgr = TaskManager(app)
    yield mgr
    try:
        mgr.stop()
    except Exception:
        pass


def _wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- 模式2 run_async
def test_run_async_success(tm: TaskManager) -> None:
    box: list = []
    tm.run_async(lambda **kw: 42, on_done=lambda r: box.append(r))
    assert _wait_until(lambda: tm._has_one_shot_running() is False)
    app = tm.app
    assert isinstance(app, FakeApp)
    app.run_scheduled()  # 派发 after(0, on_done)
    assert box == [42]


def test_run_async_error_branch(tm: TaskManager) -> None:
    errs: list[str] = []

    def boom(**kw) -> None:
        raise RuntimeError("爆炸了")

    tm.run_async(boom, on_error=lambda m: errs.append(m))
    assert _wait_until(lambda: tm._has_one_shot_running() is False)
    tm.app.run_scheduled()
    assert errs == ["爆炸了"]


def test_run_async_progress_and_log_injection(tm: TaskManager) -> None:
    prog: list[tuple] = []
    logs: list[str] = []
    tm.app.helpers = None  # 无 helpers → 走 app.after 分支

    def work(_progress_callback=None, _log=None) -> None:
        _log("干活了", "info")
        _progress_callback(50.0, "一半")

    tm.run_async(work, on_progress=lambda p, m: prog.append((p, m)), on_done=lambda r: logs.append("done"))
    assert _wait_until(lambda: tm._has_one_shot_running() is False)
    tm.app.run_scheduled()
    assert prog == [(50.0, "一半")]
    assert "done" in logs


def test_run_async_cooperative_cancel(tm: TaskManager) -> None:
    """协作式取消：进度上报时检测取消标志 → InterruptedError → on_cancelled。"""
    cancelled: list[int] = []
    started = threading.Event()

    def work(_progress_callback=None, _log=None) -> str:
        started.set()
        for _ in range(50):
            _progress_callback(10.0, "step")
            time.sleep(0.02)
        return "完成"

    tm.run_async(work, on_cancelled=lambda: cancelled.append(1), on_done=lambda r: (_ for _ in ()).throw(AssertionError("不应成功")))
    assert started.wait(2.0)
    tm.request_cancel()
    assert _wait_until(lambda: tm._has_one_shot_running() is False)
    # add_done_callback 在 worker 线程异步执行：等它把派发回调挂到 app 上
    assert _wait_until(lambda: bool(tm.app.scheduled))
    tm.app.run_scheduled()
    assert cancelled == [1]


def test_request_cancel_state(tm: TaskManager) -> None:
    assert tm.is_cancelled() is False
    tm.request_cancel()
    assert tm.is_cancelled() is True
    tm.clear_cancel()
    assert tm.is_cancelled() is False


# ---------------------------------------------------------------- 模式1 常驻 worker
def test_submit_worker_success(tm: TaskManager) -> None:
    tm.start()
    tm.submit(lambda: {"n": 7})
    assert _wait_until(lambda: not tm._result_queue.empty())
    tm._poll_results()  # 手动派发一轮
    tm.app.run_scheduled()
    assert tm.app.done_calls and tm.app.done_calls[0][0] == {"n": 7}


def test_submit_worker_error(tm: TaskManager) -> None:
    tm.start()

    def bad() -> None:
        raise ValueError("常驻任务失败")

    tm.submit(bad)
    assert _wait_until(lambda: not tm._result_queue.empty())
    tm._poll_results()
    tm.app.run_scheduled()
    assert tm.app.error_calls == ["常驻任务失败"]


def test_submit_worker_interrupted_is_cancelled(tm: TaskManager) -> None:
    tm.start()
    tm.submit(lambda: (_ for _ in ()).throw(InterruptedError))
    assert _wait_until(lambda: not tm._result_queue.empty())
    tm._poll_results()
    tm.app.run_scheduled()
    assert tm.app.cancelled_calls == 1


def test_submit_with_job_tag(tm: TaskManager) -> None:
    tm.start()
    job = {"name": "演示任务"}
    tm.submit(lambda: "ok", job=job)
    assert _wait_until(lambda: not tm._result_queue.empty())
    tm._poll_results()
    tm.app.run_scheduled()
    assert tm.app.done_calls[0][1] is job  # job 原样回传


def test_is_busy(tm: TaskManager) -> None:
    release = threading.Event()

    def hold(**kw) -> None:
        release.wait(3.0)

    tm.run_async(hold)
    assert _wait_until(lambda: tm.is_busy())
    release.set()
    assert _wait_until(lambda: not tm.is_busy())
