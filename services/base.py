"""Service 层基类：统一后台任务派发，框架无关（不依赖 tkinter/ui）。

所有业务 Service 继承此类，通过注入的 ``task_manager`` 提交后台任务；
回调（``on_done``/``on_error``）由 ``task_manager.run_async`` 自动
``after(0)`` 回主线程，UI 层只负责提供回调与渲染。

约定：
  - Service 只 ``import chem.*`` / ``core.task_manager``，**禁止 import ``ui.*``**，
    避免与 UI 包相互循环导入。
  - ``task_manager`` 由调用方（主窗口）注入共享实例，Service 自身不创建线程池。
"""
from __future__ import annotations

from typing import Any, Callable, Optional


class ServiceBase:
    def __init__(
        self,
        task_manager: Any,
        *,
        scheduler: Optional[Callable[[Callable[[], Any]], Any]] = None,
    ):
        self._tm = task_manager
        # scheduler 预留：UI 可注入 app.after 以便 Service 自行回主线程调度；
        # 当前 run_async 已自动 after(0) 调度 on_done/on_error，故默认不依赖。
        self._scheduler = scheduler

    @property
    def task_manager(self) -> Any:
        return self._tm

    def _run(
        self,
        fn: Callable[..., Any],
        *,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> Any:
        """薄封装 ``task_manager.run_async``（模式2：自动 after(0) 调度回调）。

        返回底层 ``Future``，供调用方在窗口销毁时取消。
        """
        return self._tm.run_async(
            fn,
            on_done=on_done,
            on_error=on_error,
            on_progress=on_progress,
        )
