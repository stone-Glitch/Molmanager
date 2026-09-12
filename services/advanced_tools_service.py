"""高级工具 Service：把一次性后台任务提交收口到共享 ``task_manager``。

修复既有 Bug：高级工具对话框原先自建 ``TaskManager(app, controller=None)``，
与主窗口的线程池彼此独立、取消状态无法互通；现统一复用注入的共享实例。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .base import ServiceBase


class AdvancedToolsService(ServiceBase):
    def submit(
        self,
        fn: Callable[[], Any],
        *,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        """提交一个无参后台任务（``fn`` 内部自行组织领域调用）。

        返回 ``concurrent.futures.Future``，供调用方在对话框销毁时取消。

        协作式取消依赖调用方在 ``fn`` 中周期性检查 ``task_manager.is_cancelled()``；
        本方法不强制，沿用既有高级工具任务的取消语义。
        """

        def _wrapped(_progress_callback=None, _log=None):
            return fn()

        return self._run(_wrapped, on_done=on_done, on_error=on_error)
