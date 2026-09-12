"""PSI4 扫描 Service：收口 psi4_dialog 的三类后台计算（线性插值扫描 / 刚性扫描 / 批量）。

框架无关：**不 import ui.***，领域调用所需的 ``model`` 由 UI 层注入。批量任务保留
原有的**协作式取消**（``should_cancel`` 轮询）与**逐文件进度上报**语义：
  - 每完成一个文件发一条 log 事件 + 一条 stage 进度事件；
  - 取消后立即停止后续文件，并在结果里标记 ``cancelled``。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .base import EVENT_LOG, EVENT_STAGE, ServiceBase


def _emit_log(emit: Any, message: str, level: str = "info") -> None:
    if emit is not None:
        emit({"type": EVENT_LOG, "message": str(message), "level": level})


def _emit_stage(emit: Any, message: str = "", fraction: Optional[float] = None) -> None:
    if emit is not None:
        event: dict[str, Any] = {"type": EVENT_STAGE, "message": str(message)}
        if fraction is not None:
            event["fraction"] = float(fraction)
        emit(event)


class Psi4ScanService(ServiceBase):
    """``model`` 为 ``core.model`` 实例（提供 run_linear_scan / run_rigid_scan 等）。"""

    def __init__(self, task_manager: Any = None, *, model: Any = None, scheduler: Any = None):
        super().__init__(task_manager, scheduler=scheduler)
        self._model = model

    def bind_model(self, model: Any) -> None:
        self._model = model

    # ------------------------------------------------------------ 线性插值扫描
    def linear_scan(
        self,
        reactant_files: list,
        product_files: list,
        steps: int,
        method: str,
        basis: str,
        out_dir: Any,
        preset: Any = None,
        solvent: Any = None,
        d3: bool = False,
        charge: int = 0,
        mult: int = 1,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        reactant_files = list(reactant_files)
        product_files = list(product_files)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):
            _emit_log(emit, "🔬 开始线性插值扫描")
            _emit_log(emit, f"   反应物: {len(reactant_files)} 个文件")
            _emit_log(emit, f"   产物: {len(product_files)} 个文件")
            _emit_log(emit, f"   步数: {steps}, 方法: {method}, 基组: {basis}")
            res = self._model.run_linear_scan(
                reactant_files,
                product_files,
                steps,
                method,
                basis,
                out_dir,
                preset,
                solvent,
                d3,
                charge,
                mult,
                progress_callback=progress_callback,
            )
            return res

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ 刚性扫描
    def rigid_scan(
        self,
        file_path: Any,
        scan_atoms: tuple,
        distance_range: tuple,
        method: str,
        basis: str,
        out_dir: Any,
        preset: Any = None,
        solvent: Any = None,
        d3: bool = False,
        charge: int = 0,
        mult: int = 1,
        memory: str = "4 GB",
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        idx1, idx2 = scan_atoms
        start, end, steps = distance_range
        fname = str(file_path)
        path_s = str(file_path)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):
            _emit_log(emit, f"🔬 开始刚性扫描: {fname}")
            _emit_log(emit, f"   方法: {method}, 基组: {basis}")
            _emit_log(
                emit,
                f"   原子对: {idx1 + 1}-{idx2 + 1}, 距离: {start}~{end} Å, 步数: {steps}",
            )
            return self._model.run_rigid_scan(
                path_s,
                (idx1, idx2),
                (start, end, steps),
                method,
                basis,
                out_dir,
                preset,
                solvent,
                d3,
                charge,
                mult,
                progress_callback=progress_callback,
            )

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ 批量计算
    def batch_compute(
        self,
        files: list,
        work_dir: Any,
        task: str,
        method: str,
        basis: str,
        out_dir: Any,
        preset: Any = None,
        solvent: Any = None,
        d3: bool = False,
        charge: int = 0,
        mult: int = 1,
        memory: str = "4 GB",
        scf_options: Any = None,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """批量 PSI4 计算（保留协作式取消 + 逐文件进度）。

        ``on_done`` 收到 ``{"cancelled": bool, "results": [ {file, res} ... ]}``：
        所有领域结果都回传给 UI 层渲染（Service 不做任何文本拼装）。
        """
        from pathlib import Path

        from chem.psi4_compute import run_psi4_task_cancellable

        files = list(files)
        base_dir = Path(work_dir)
        total = len(files)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            cancelled_any = False
            results: list[dict] = []
            for idx, fname in enumerate(files):
                if should_cancel is not None and should_cancel():
                    cancelled_any = True
                    break
                file_path = base_dir / fname
                _emit_log(emit, f"--- ({idx + 1}/{total}) {fname} ---")
                try:
                    res = run_psi4_task_cancellable(
                        str(file_path),
                        task,
                        method,
                        basis,
                        out_dir,
                        preset,
                        solvent,
                        d3,
                        charge,
                        mult,
                        memory,
                        cancel_check=should_cancel,
                        extra_options=scf_options,
                    )
                except Exception as e:  # noqa: BLE001
                    _emit_log(emit, f"❌ 异常: {e}", "error")
                    results.append({"file": fname, "res": {"success": False, "error": str(e)}})
                    continue

                if res.get("cancelled"):
                    _emit_log(emit, f"⏹ PSI4 计算已取消: {fname}", "warning")
                    cancelled_any = True
                    break

                results.append({"file": fname, "res": res})
                if res.get("success"):
                    _emit_log(emit, f"✅ PSI4 计算完成: {fname}", "success")
                else:
                    _emit_log(emit, f"❌ PSI4 计算失败: {fname}", "error")
                _emit_stage(emit, f"{idx + 1}/{total}", (idx + 1) / max(1, total))

            return {"cancelled": cancelled_any, "results": results}

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)
