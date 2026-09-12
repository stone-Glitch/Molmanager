"""分子分析 Service：收口分子式/元素分析、几何参数导出等纯领域调用。

框架无关：只依赖 ``chem.openbabel_utils``。UI 层（analytics_dialog）负责收集
选中文件、提供 ``on_done`` 回调做结果渲染，不再自建 ``TaskManager``。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from .base import ServiceBase


class AnalyticsService(ServiceBase):
    def analyze_formula(
        self,
        path: Any,
        *,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """后台跑分子式/元素分析，``on_done`` 收到 ``(res, basename)``。

        修复既有 Bug：analytics_dialog 原先自建 ``TaskManager(app, controller)``，
        与主窗口线程池彼此独立；现统一复用注入的共享调度器。
        """
        p = str(path)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            import chem.openbabel_utils as obu

            return obu.analyze_formula(p), os.path.basename(p)

        return self._run(_work, on_done=on_done, on_error=on_error)

    def export_geometry_csv(
        self,
        src: Any,
        target: Any,
        *,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """后台导出几何参数（键长/键角）CSV，``on_done`` 收到结果 dict。"""
        src_s, target_s = str(src), str(target)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            import chem.openbabel_utils as obu

            return obu.export_geometry_csv(src_s, target_s)

        return self._run(_work, on_done=on_done, on_error=on_error)

    @staticmethod
    def resolve_selected_path(sel0: str, work_dir: str) -> str:
        """按主界面语义把选中文件名解析为绝对路径（work_dir 为空或已是绝对路径则原样）。

        该纯函数不涉及后台/IO，供 UI 层直接复用，避免重复实现路径拼接。
        """
        work = (work_dir or "").strip()
        if work and not os.path.isabs(sel0):
            return str(Path(work) / sel0)
        return sel0
