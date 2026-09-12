"""目录同步 Service：收口两工作目录之间的一键复制/覆盖（纯文件 IO）。

框架无关：只操作传入的 ``model``（``core.model`` 的 ``compare_directories`` /
``copy_from_left_to_right`` 等）与标准库。UI 层（sync_dialog）负责收集左右目录与
选中项、在 ``on_done`` 里刷新差异视图，不再直接 ``app.helpers.run_task``。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .base import ServiceBase


class SyncService(ServiceBase):
    def copy(
        self,
        direction: Callable[[list, str, str], Any],
        names: list,
        left: str,
        right: str,
        *,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """后台执行一次目录复制。

        :param direction: ``model`` 上的方法（``copy_from_left_to_right`` /
            ``copy_from_right_to_left``），签名 ``(names, left, right)``。
        """
        names = list(names)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            return direction(names, left, right)

        if on_done is None:
            def _noop(_r: Any) -> None:
                return None

            on_done = _noop
        return self._run(_work, on_done=on_done, on_error=on_error)

    def overwrite(
        self,
        direction: Callable[[list, str, str], Any],
        names: list,
        left: str,
        right: str,
        *,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """后台执行一次同名文件覆盖（``sync_overwrite_left_to_right`` /
        ``sync_overwrite_right_to_left``）。"""
        names = list(names)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            return direction(names, left, right)

        if on_done is None:
            def _noop(_r: Any) -> None:
                return None

            on_done = _noop
        return self._run(_work, on_done=on_done, on_error=on_error)
