"""Service 层基类：统一后台任务派发，框架无关（不依赖 tkinter/ui）。

设计要点（v1.5.0 解耦）：
  - Service 只 ``import chem.*`` / 标准库，**禁止 import ``ui.*``**，避免与 UI 包循环导入。
  - 后台执行通过**可注入的调度器**（``Scheduler`` 协议）完成，而不是硬编码
    ``task_manager``。这使同一批 Service 既能被 Tkinter 对话框复用（默认
    :class:`TkinterScheduler`），也能被 Web 层复用（``api.dispatcher.WebDispatcher``），
    从而消除 ``api/`` 与 ``services/`` 的重复领域调用。
  - 进度/日志统一以 **WebSocket 友好的 dict** 表达：
        {"type": "stage", "fraction": float, "message": str}
        {"type": "log",   "message": str}
        {"type": "error", "message": str}
    桌面调度器再把它映射回 ``on_progress``/``on_log`` 回调，保证既有桌面行为零变更。

向后兼容：
  - 既有调用方仍可传 ``task_manager``（位置参数）与 ``scheduler=callable``；
    两者都会被 :meth:`ServiceBase._resolve_scheduler` 归一化为一个 ``Scheduler``。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# 统一事件类型（dict 形态，WebSocket / 桌面共用）
EVENT_STAGE = "stage"
EVENT_LOG = "log"
EVENT_ERROR = "error"


# ---------------------------------------------------------------- 调度器协议
class Scheduler:
    """后台任务调度器协议（结构化，无需显式继承）。

    实现方需提供两个方法：

    - ``dispatch(fn, *, on_event, on_done, on_error, on_cancelled=None)``：
      在后台执行 ``fn(emit, should_cancel)``；``emit(event)`` 用于上报
      dict 形态的进度事件；返回一个可 ``cancel()`` 的句柄。
    - ``should_cancel(handle)``：查询句柄是否已被请求取消。
    """

    def dispatch(  # pragma: no cover - 协议声明
        self,
        fn: Callable[..., Any],
        *,
        on_event: Callable[[dict[str, Any]], Any],
        on_done: Optional[Callable[[Any], Any]] = None,
        on_error: Optional[Callable[[str], Any]] = None,
        on_cancelled: Optional[Callable[[], Any]] = None,
    ) -> Any:
        raise NotImplementedError

    def should_cancel(self, handle: Any) -> bool:  # pragma: no cover - 协议声明
        return False


# ---------------------------------------------------------------- 桌面调度器
class TkinterScheduler(Scheduler):
    """把 Service 的后台任务落到共享 ``task_manager`` + ``app.after(0)`` 主线程回调。

    这是既有桌面行为的等价实现：
      - 执行体经 ``task_manager.run_async`` 提交通用线程池；
      - ``emit({stage/log})`` → 桌面 ``on_progress`` / ``on_log``；
      - 取消沿用 ``task_manager.request_cancel()/is_cancelled()``（实例级协作式取消）。
    """

    def __init__(self, task_manager: Any):
        self._tm = task_manager

    @property
    def task_manager(self) -> Any:
        return self._tm

    def dispatch(
        self,
        fn: Callable[..., Any],
        *,
        on_event: Callable[[dict[str, Any]], Any],
        on_done: Optional[Callable[[Any], Any]] = None,
        on_error: Optional[Callable[[str], Any]] = None,
        on_cancelled: Optional[Callable[[], Any]] = None,
    ) -> Any:
        def _progress(percent: float, message: str = "") -> None:
            on_event({"type": EVENT_STAGE, "fraction": percent, "message": message})

        def _log(message: str, level: str = "info") -> None:
            on_event({"type": EVENT_LOG, "message": message, "level": level})

        def _body(*, _progress_callback=None, _log=None):  # noqa: ARG001 - 兼容签名
            return fn(
                emit=on_event,
                should_cancel=lambda: bool(self._tm.is_cancelled()),
                progress_callback=_progress,
                log=_log,
            )

        return self._tm.run_async(
            _body,
            on_done=on_done,
            on_error=on_error,
            on_progress=_progress,
            on_cancelled=on_cancelled,
        )

    def should_cancel(self, handle: Any = None) -> bool:  # noqa: ARG002 - 桌面为实例级
        try:
            return bool(self._tm.is_cancelled())
        except Exception:
            return False


class _CallableScheduler(Scheduler):
    """把 ``scheduler=callable`` 形式的旧式注入自适应为 Scheduler。

    ``callable`` 会被当作 ``dispatch(fn)`` 的简写；取消查询回退到全局标志。
    """

    def __init__(self, scheduler: Callable[..., Any]):
        self._fn = scheduler

    def dispatch(self, fn, *, on_event, on_done=None, on_error=None, on_cancelled=None):
        def _body(**_kw):
            return fn(
                emit=on_event,
                should_cancel=lambda: False,
                progress_callback=lambda *a: None,
                log=lambda *a: None,
            )

        try:
            return self._fn(_body)
        except TypeError:
            return self._fn()

    def should_cancel(self, handle: Any = None) -> bool:  # noqa: ARG002
        return False


class ServiceBase:
    """所有业务 Service 的基类：持有调度器，提供 ``_run`` 收口派发。"""

    def __init__(
        self,
        task_manager: Any = None,
        *,
        scheduler: Any = None,
    ):
        """
        :param task_manager: 共享 ``TaskManager``（桌面路径）；Web 路径可传 ``None``。
        :param scheduler: 可注入调度器。可传：
            * ``None`` → 若有 ``task_manager`` 则包成 :class:`TkinterScheduler`；
            * ``Scheduler`` 实例（如 ``WebDispatcher``）→ 直接使用；
            * ``callable`` → 适配为 :class:`_CallableScheduler`（兼容旧签名）。
        """
        self._tm = task_manager
        self._scheduler = self._resolve_scheduler(task_manager, scheduler)

    @staticmethod
    def _resolve_scheduler(task_manager: Any, scheduler: Any) -> Optional[Scheduler]:
        if scheduler is not None:
            if callable(scheduler) and not hasattr(scheduler, "dispatch"):
                return _CallableScheduler(scheduler)
            return scheduler
        if task_manager is not None:
            return TkinterScheduler(task_manager)
        return None

    @property
    def task_manager(self) -> Any:
        """兼容既有代码：返回注入的共享 ``task_manager``（Web 路径可能为 ``None``）。"""
        return self._tm

    @property
    def scheduler(self) -> Optional[Scheduler]:
        return self._scheduler

    def _run(
        self,
        fn: Callable[..., Any],
        *,
        on_event: Optional[Callable[[dict[str, Any]], Any]] = None,
        on_done: Optional[Callable[[Any], Any]] = None,
        on_error: Optional[Callable[[str], Any]] = None,
        on_cancelled: Optional[Callable[[], Any]] = None,
        on_progress: Optional[Callable[[float, str], Any]] = None,
        on_log: Optional[Callable[[str], Any]] = None,
        dispatch_kwargs: Optional[dict[str, Any]] = None,
    ) -> Any:
        """派发一个后台任务 ``fn(emit, should_cancel)``，返回可取消句柄。

        - ``on_event`` 优先；若未提供，则由 ``on_progress``/``on_log`` 合成事件回调
          （兼容既有 Service 的分离式回调签名）。
        - ``dispatch_kwargs`` 透传给调度器 ``dispatch``（例如 Web 路径的 ``job_id``/``pool``）。
        """
        if self._scheduler is None:
            raise RuntimeError("ServiceBase 未配置调度器（task_manager/scheduler 均为空）")

        if on_event is None:
            def _to_event(event: dict[str, Any]) -> None:
                etype = event.get("type")
                if etype == EVENT_STAGE and on_progress is not None:
                    on_progress(float(event.get("fraction") or 0.0), str(event.get("message") or ""))
                elif etype == EVENT_LOG and on_log is not None:
                    on_log(str(event.get("message") or ""))

            on_event = _to_event

        return self._scheduler.dispatch(
            fn,
            on_event=on_event,
            on_done=on_done,
            on_error=on_error,
            on_cancelled=on_cancelled,
            **(dispatch_kwargs or {}),
        )

    def with_scheduler(self, scheduler: Any) -> ServiceBase:
        """返回一个共享同一 ``task_manager`` 但替换调度器的新实例（浅拷贝）。

        用于 Web 路径：同一批 Service 定义，按请求绑定不同 ``WebDispatcher``/``job_id``。
        """
        clone = object.__new__(type(self))
        clone.__dict__.update(self.__dict__)
        clone._scheduler = ServiceBase._resolve_scheduler(self._tm, scheduler)
        return clone


__all__ = [
    "EVENT_ERROR",
    "EVENT_LOG",
    "EVENT_STAGE",
    "Scheduler",
    "ServiceBase",
    "TkinterScheduler",
]
