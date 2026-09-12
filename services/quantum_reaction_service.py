"""量子反应能计算 Service：封装 ``chem.quantum_reaction.runner.run_reaction``。

框架无关：只依赖 ``chem.quantum_reaction`` 与注入的 ``task_manager``；
UI 关注的日志/阶段/取消/完成回调全部由调用方注入，Service 仅做编排与派发。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .base import ServiceBase


class QuantumReactionService(ServiceBase):
    def compute(
        self,
        payload: dict,
        run_dir: Any,
        *,
        on_log: Optional[Callable[[str], None]] = None,
        on_stage: Optional[Callable[[str, float], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        """在后台线程跑一次完整反应能计算。

        参数与 ``chem.quantum_reaction.runner.run_reaction`` 对齐：
          - ``payload``：``{reaction_id}`` 或 ``{custom: {...}}`` 等。
          - ``run_dir``：本 run 输出目录（自动创建），由调用方算好传入。
          - ``on_log``/``on_stage``/``should_cancel``：传入 ``run_reaction`` 的回调
            （通常由 UI 层包好 ``app.after(0, ...)`` 后注入）。
          - ``on_done``/``on_error``：经 ``run_async`` 自动回主线程。
        """
        from chem.quantum_reaction import run_reaction

        run_dir = Path(run_dir)

        def _work(_progress_callback=None, _log=None):
            return run_reaction(
                payload,
                run_dir=run_dir,
                on_log=on_log,
                on_stage=on_stage,
                should_cancel=should_cancel,
            )

        self._run(_work, on_done=on_done, on_error=on_error)
