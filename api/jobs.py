#!/usr/bin/env python3
"""Web 层的后台任务编排器（框架无关，不依赖 FastAPI / asyncio）。

设计要点：
  1. **双线程池** —— PSI4 池 ``max_workers=1``（串行，尊重
     ``chem.quantum_reaction.quantum._PSI_LOCK`` 的全局锁，PSI4 非线程安全）；
     动画 / I/O 池 ``max_workers=4``（ffmpeg 走子进程，I/O 密集）。
  2. **协作式取消** —— ``cancel()`` 置 ``cancelled`` 标志，计算函数通过
     ``should_cancel`` 轮询主动退出；同时尝试 ``future.cancel()`` 抢在启动前取消。
  3. **事件缓冲 + 迟到回放** —— 每个任务缓冲最近 1000 条进度事件，WebSocket
     连接后才挂载时先回放缓冲再转实时，避免「连接前事件丢失」。
  4. **零异步依赖** —— 本模块纯线程 + 标准库，便于脱离 Web 直接单测。
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

# 任务终态集合
_TERMINAL = frozenset({"done", "error", "cancelled"})


class JobState:
    """单个后台任务的运行时状态（由 JobManager 内部管理）。"""

    __slots__ = (
        "job_id",
        "status",
        "progress",
        "message",
        "result",
        "error",
        "cancelled",
        "buffer",
        "emit_cb",
        "future",
        "lock",
    )

    def __init__(self, job_id: str, max_events: int = 1000) -> None:
        self.job_id = job_id
        self.status = "queued"
        self.progress = 0.0
        self.message = ""
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.cancelled = False
        self.buffer: deque[dict[str, Any]] = deque(maxlen=max_events)
        self.emit_cb: Callable[[dict[str, Any]], None] | None = None
        self.future: Future | None = None
        import threading

        self.lock = threading.Lock()


class JobManager:
    """进程内后台任务管理器（模块级单例 ``jobs``）。"""

    def __init__(self) -> None:
        self._psi4_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mm-psi4")
        self._io_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mm-io")
        self._jobs: dict[str, JobState] = {}
        import threading

        self._registry_lock = threading.Lock()

    # ---------------- 提交 ----------------
    def submit(self, job_id: str, fn: Callable[..., Any], pool: str = "psi4", max_events: int = 1000) -> JobState:
        """提交一个后台任务。

        ``fn`` 签名为 ``fn(*, emit, should_cancel)``：
          - ``emit(event)`` 推送进度事件（dict）
          - ``should_cancel() -> bool`` 协作式取消查询
        任务在对应线程池里运行，**立即返回**，不阻塞调用方。
        """
        with self._registry_lock:
            state = self._jobs.get(job_id)
            if state is None:
                state = JobState(job_id, max_events=max_events)
                self._jobs[job_id] = state

        executor = self._psi4_pool if pool == "psi4" else self._io_pool

        def _emit(event: dict[str, Any]) -> None:
            with state.lock:
                state.buffer.append(event)
                if event.get("type") == "stage":
                    if event.get("fraction") is not None:
                        state.progress = float(event["fraction"])
                    if event.get("message") is not None:
                        state.message = str(event["message"])
                elif event.get("type") == "log" and event.get("message") is not None:
                    state.message = str(event["message"])
                cb = state.emit_cb
            if cb is not None:
                cb(event)

        def _runner() -> None:
            with state.lock:
                state.status = "running"
            try:
                result = fn(emit=_emit, should_cancel=lambda: state.cancelled)
                if state.cancelled:
                    self._finish(state, "cancelled", error=None)
                else:
                    self._finish(state, "done", result=result)
            except Exception as exc:  # noqa: BLE001  # 统一兜底为 error 事件
                self._finish(state, "error", error=f"{type(exc).__name__}: {exc}")

        with state.lock:
            state.future = executor.submit(_runner)
        return state

    # ---------------- 进度订阅 ----------------
    def attach_emit(self, job_id: str, cb: Callable[[dict[str, Any]], None]) -> None:
        """挂载实时回调，**先回放缓冲再转实时**（解决迟到连接丢事件）。"""
        with self._registry_lock:
            state = self._jobs.get(job_id)
        if state is None:
            return
        with state.lock:
            # 回放已发生但尚未推送给本客户端的事件
            for event in list(state.buffer):
                cb(event)
            state.emit_cb = cb

    def detach_emit(self, job_id: str) -> None:
        with self._registry_lock:
            state = self._jobs.get(job_id)
        if state is None:
            return
        with state.lock:
            state.emit_cb = None

    # ---------------- 取消 / 状态 ----------------
    def cancel(self, job_id: str) -> bool:
        with self._registry_lock:
            state = self._jobs.get(job_id)
        if state is None:
            return False
        with state.lock:
            state.cancelled = True
            fut = state.future
        cancelled = False
        if fut is not None:
            try:
                cancelled = fut.cancel()
            except Exception:
                cancelled = False
        return cancelled

    def is_cancelled(self, job_id: str) -> bool:
        with self._registry_lock:
            state = self._jobs.get(job_id)
        if state is None:
            return False
        with state.lock:
            return state.cancelled

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        with self._registry_lock:
            state = self._jobs.get(job_id)
        if state is None:
            return None
        with state.lock:
            return {
                "job_id": state.job_id,
                "status": state.status,
                "progress": state.progress,
                "message": state.message,
                "result": state.result,
                "error": state.error,
            }

    # ---------------- 内部 ----------------
    @staticmethod
    def _finish(state: JobState, status: str, *, result: Any = None, error: str | None = None) -> None:
        with state.lock:
            state.status = status
            if status == "done":
                state.result = result if isinstance(result, dict) else {"value": result}
                state.progress = 1.0
            elif status == "error":
                state.error = error
            state.buffer.append({"type": status, "job_id": state.job_id, "result": state.result, "error": state.error})
            cb = state.emit_cb
        if cb is not None:
            cb({"type": status, "job_id": state.job_id, "result": state.result, "error": state.error})


# 模块级单例
jobs = JobManager()
