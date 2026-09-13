#!/usr/bin/env python3
"""Web 调度器：把 Service 的后台任务桥接到 ``api.jobs.JobManager``。

本模块让 ``services/`` 的同一批 Service 在 Web 路径下复用：
  - ``dispatch(fn, ...)`` → 经 ``JobManager.submit`` 投递到 PSI4（串行）/ IO 线程池；
    ``fn(emit, should_cancel)`` 的 ``emit`` 直接成为 WebSocket 事件、``should_cancel``
    按 **job_id** 查询（而非桌面那种实例级全局标志）。
  - 任务终态（done/error/cancelled）由 ``JobManager`` 统一记录并推送，
    因此这里不再重复调用 ``on_done``/``on_error``——它们由 Service 传入但 Web 侧
    以 job 状态为准（保留参数仅为接口一致）。

设计约束：本模块**不 import FastAPI**，只依赖 ``api.jobs`` 与 ``services.base``，
便于脱离 Web 直接单测。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from services.base import Scheduler


class _WebHandle:
    """Web 调度句柄：暴露 job_id 与取消查询。"""

    __slots__ = ("job_id", "_manager")

    def __init__(self, job_id: str, manager: Any) -> None:
        self.job_id = job_id
        self._manager = manager

    def cancel(self) -> bool:
        return bool(self._manager.cancel(self.job_id))


class WebDispatcher(Scheduler):
    """把一个 ``JobManager``（默认全局单例 ``api.jobs.jobs``）适配为 ``Scheduler``。"""

    def __init__(self, manager: Any = None) -> None:
        if manager is None:
            from .jobs import jobs as manager  # 延迟导入，避免循环

        self._manager = manager

    @property
    def manager(self) -> Any:
        return self._manager

    def dispatch(
        self,
        fn: Callable[..., Any],
        *,
        job_id: str,
        pool: str = "psi4",
        on_event: Optional[Callable[[dict[str, Any]], Any]] = None,
        on_done: Optional[Callable[[Any], Any]] = None,  # noqa: ARG002 - 终态由 JobManager 负责
        on_error: Optional[Callable[[str], Any]] = None,  # noqa: ARG002
        on_cancelled: Optional[Callable[[], Any]] = None,  # noqa: ARG002
        max_events: int = 1000,
    ) -> _WebHandle:
        """提交任务到 ``JobManager``，立即返回句柄（不阻塞）。

        ``fn`` 签名为 ``fn(*, emit, should_cancel, progress_callback, log)``——与
        桌面 ``TkinterScheduler`` 保持一致，Service 无需区分运行环境。
        """
        _emit = on_event if on_event is not None else (lambda _e: None)

        def _runner(*, emit: Callable[[dict], Any], should_cancel: Callable[[], bool]) -> Any:
            def _progress(percent: float, message: str = "") -> None:
                # ⚠️ Service 层注入的 progress_callback 沿用桌面约定，传的是 **0~100 百分比**；
                # 而统一事件契约（models.ProgressEvent / 前端进度条）要求 fraction 是 **0~1 比例**。
                # 这里是 Web 侧唯一的归一化收口点——不归一化会让 JobManager.progress 和前端
                # 进度条出现 500%/1350% 这类溢出值。
                pct = max(0.0, min(100.0, float(percent)))
                emit({"type": "stage", "fraction": pct / 100.0, "message": str(message)})

            def _log(message: str, level: str = "info") -> None:
                emit({"type": "log", "message": str(message), "level": level})

            def _emit_bridge(event: dict[str, Any]) -> None:
                # 同时送给 Service 的 on_event（若有）与 JobManager 的事件缓冲
                if on_event is not None:
                    on_event(event)
                emit(event)

            return fn(
                emit=_emit_bridge,
                should_cancel=should_cancel,
                progress_callback=_progress,
                log=_log,
            )

        self._manager.submit(job_id, _runner, pool=pool, max_events=max_events)
        return _WebHandle(job_id, self._manager)

    def should_cancel(self, handle: Any) -> bool:
        job_id = getattr(handle, "job_id", handle)
        if job_id is None:
            return False
        return bool(self._manager.is_cancelled(job_id))


__all__ = ["WebDispatcher"]
