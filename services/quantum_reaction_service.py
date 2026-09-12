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
        scheduler_id: Optional[str] = None,
        pool: str = "psi4",
    ):
        """在后台线程跑一次完整反应能计算。

        参数与 ``chem.quantum_reaction.runner.run_reaction`` 对齐：
          - ``payload``：``{reaction_id}`` 或 ``{custom: {...}}`` 等。
          - ``run_dir``：本 run 输出目录（自动创建），由调用方算好传入。
          - ``on_log``/``on_stage``/``should_cancel``：可选显式回调；不传时由调度器的
            统一 ``emit`` 事件桥接（桌面 → on_progress/on_log；Web → WebSocket）。
          - ``on_done``/``on_error``：经调度器自动回主线程（桌面）或写入 job 终态（Web）。
        """
        from chem.quantum_reaction import run_reaction

        run_dir = Path(run_dir)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):
            # 领域回调优先用调用方显式注入的；否则从统一 emit 事件桥接。
            _on_log = on_log
            _on_stage = on_stage
            _should_cancel = should_cancel
            if _on_log is None and emit is not None:
                _on_log = lambda m: emit({"type": "log", "message": str(m)})  # noqa: E731
            if _on_stage is None and emit is not None:
                _on_stage = lambda name, frac: emit(  # noqa: E731
                    {"type": "stage", "message": str(name), "fraction": float(frac)}
                )
            if _should_cancel is None and should_cancel is not None:
                _should_cancel = should_cancel
            return run_reaction(
                payload,
                run_dir=run_dir,
                on_log=_on_log,
                on_stage=_on_stage,
                should_cancel=_should_cancel,
            )

        kwargs = {}
        if scheduler_id:
            kwargs["dispatch_kwargs"] = {"job_id": scheduler_id, "pool": pool}
        self._run(_work, on_done=on_done, on_error=on_error, **kwargs)
