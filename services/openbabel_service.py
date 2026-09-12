"""OpenBabel Service：收口 openbabel_dialog 的批量领域调用与后台派发。

框架无关：**不 import ui.***，也不持有全局 model；领域调用所需的 ``model`` 由调用方
（UI 层）在构造或调用时传入，Service 只负责「批量循环 + 聚合回调 + 后台派发」。

批量任务在 Service 内**聚合回调**：每条结果发一条 log 事件、进度按
``已完成/总数`` 计算，避免 UI 线程被逐条 ``after(0)`` 淹没。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Callable, Optional

from .base import EVENT_LOG, EVENT_STAGE, ServiceBase


def _emit_log(emit: Any, message: str, level: str = "info") -> None:
    if emit is not None:
        emit({"type": EVENT_LOG, "message": str(message), "level": level})


def _emit_stage(emit: Any, done: int, total: int) -> None:
    if emit is not None and total > 0:
        emit({"type": EVENT_STAGE, "fraction": done / total, "message": f"{done}/{total}"})


class OpenBabelService(ServiceBase):
    """``model`` 为 ``core.model`` 实例（提供 convert_file / optimize_geometry 等）。"""

    def __init__(self, task_manager: Any = None, *, model: Any = None, scheduler: Any = None):
        super().__init__(task_manager, scheduler=scheduler)
        self._model = model

    def bind_model(self, model: Any) -> None:
        """绑定/更新领域 model（UI 层可在装配后统一注入）。"""
        self._model = model

    # ------------------------------------------------------------ 描述符
    def calculate_descriptors(
        self,
        path: Any,
        *,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        p = str(path)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            import chem.openbabel_utils as obu

            return obu.calculate_descriptors(p)

        return self._run(_work, on_done=on_done, on_error=on_error)

    def descriptors_to_csv(
        self,
        items: list,
        work_dir: Any,
        out_path: Any,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        """批量计算描述符并写出 CSV，``on_done`` 收到 ``{"rows", "out_path", "count"}``。"""
        items = list(items)
        base_dir = Path(work_dir)
        out_s = str(out_path)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            rows: list[dict] = []
            fieldnames: list[str] = ["file"]
            total = len(items)
            for idx, fname in enumerate(items):
                if should_cancel is not None and should_cancel():
                    break
                path = base_dir / fname
                base = os.path.basename(fname)
                try:
                    desc = self._model.calculate_descriptors(str(path))
                    if "error" in desc:
                        row = {"file": base, "error": desc["error"]}
                    else:
                        row = {"file": base, **desc}
                        for k in desc:
                            if k not in fieldnames:
                                fieldnames.append(k)
                except Exception as e:  # noqa: BLE001 - 单条失败不阻断整体
                    row = {"file": base, "error": str(e)}
                if "error" in row and "error" not in fieldnames:
                    fieldnames.append("error")
                rows.append(row)
                _emit_log(emit, f"📊 ({idx + 1}/{total}) {base}")
                _emit_stage(emit, idx + 1, total)

            with open(out_s, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            return {"rows": rows, "out_path": out_s, "count": len(rows)}

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ 批量转换
    def convert_batch(
        self,
        items: list,
        work_dir: Any,
        out_fmt: str,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        items = list(items)
        base_dir = Path(work_dir)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            all_ok = True
            total = len(items)
            for idx, name in enumerate(items):
                if should_cancel is not None and should_cancel():
                    break
                input_path = base_dir / name
                output_path = base_dir / f"{input_path.stem}.{out_fmt}"
                try:
                    res = self._model.convert_file(str(input_path), str(output_path), out_fmt)
                    success = bool(res.get("success", False))
                    msg = res.get("message", "")
                    _emit_log(
                        emit,
                        f"{'✅' if success else '❌'} 转换 {name}: {msg}",
                        "success" if success else "error",
                    )
                    if not success:
                        all_ok = False
                except Exception as e:  # noqa: BLE001
                    _emit_log(emit, f"❌ 转换 {name} 异常: {e}", "error")
                    all_ok = False
                _emit_stage(emit, idx + 1, total)
            return {"all_ok": all_ok, "count": total}

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ 批量优化
    def optimize_batch(
        self,
        items: list,
        work_dir: Any,
        forcefield: str,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        items = list(items)
        base_dir = Path(work_dir)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            all_ok = True
            total = len(items)
            for idx, name in enumerate(items):
                if should_cancel is not None and should_cancel():
                    break
                input_path = base_dir / name
                output_path = input_path.parent / f"{input_path.stem}_opt{input_path.suffix}"
                try:
                    res = self._model.optimize_geometry(str(input_path), str(output_path), forcefield)
                    success = bool(res.get("success", False))
                    msg = res.get("message", "")
                    _emit_log(
                        emit,
                        f"{'✅' if success else '❌'} 优化 {name}: {msg}",
                        "success" if success else "error",
                    )
                    if not success:
                        all_ok = False
                except Exception as e:  # noqa: BLE001
                    _emit_log(emit, f"❌ 优化 {name} 异常: {e}", "error")
                    all_ok = False
                _emit_stage(emit, idx + 1, total)
            return {"all_ok": all_ok, "count": total}

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ SMILES 批量
    def smiles_batch(
        self,
        lines: list,
        gen3d: bool,
        opt: bool,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        lines = list(lines)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            all_ok = True
            total = len(lines)
            for idx, line in enumerate(lines):
                if should_cancel is not None and should_cancel():
                    break
                parts = line.split(None, 1)
                smiles = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else f"smi_idx_{idx + 1:03d}"
                if not smiles:
                    continue
                try:
                    res = self._model.generate_from_smiles(smiles, name, generate_3d=gen3d, optimize=opt)
                    if res.get("error"):
                        _emit_log(emit, f"❌ SMILES 生成失败 {name}: {res['error']}", "error")
                        all_ok = False
                    else:
                        _emit_log(emit, f"✅ 生成成功 {name}: {os.path.basename(res['mol'])}", "success")
                except Exception as e:  # noqa: BLE001
                    _emit_log(emit, f"❌ SMILES 生成异常 {name}: {e}", "error")
                    all_ok = False
                _emit_stage(emit, idx + 1, total)
            return {"all_ok": all_ok, "count": total}

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ 分子叠加
    def align_batch(
        self,
        ref_name: str,
        items: list,
        work_dir: Any,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        items = list(items)
        base_dir = Path(work_dir)
        ref_path = base_dir / ref_name
        ref_stem = ref_path.stem

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            all_ok = True
            total = len(items)
            for idx, mob_name in enumerate(items):
                if should_cancel is not None and should_cancel():
                    break
                mob_path = base_dir / mob_name
                out_path = base_dir / f"{mob_path.stem}_aligned_to_{ref_stem}.xyz"
                try:
                    res = self._model.align_molecules(str(ref_path), str(mob_path), str(out_path))
                    success = bool(res.get("success", False))
                    msg = res.get("message", "")
                    _emit_log(
                        emit,
                        f"{'✅' if success else '❌'} 叠加 {mob_name}: {msg}",
                        "success" if success else "error",
                    )
                    if not success:
                        all_ok = False
                except Exception as e:  # noqa: BLE001
                    _emit_log(emit, f"❌ 叠加 {mob_name} 异常: {e}", "error")
                    all_ok = False
                _emit_stage(emit, idx + 1, total)
            return {"all_ok": all_ok, "count": total}

        return self._run(_work, on_event=on_event, on_done=on_done, on_error=on_error)

    # ------------------------------------------------------------ 2D 预览
    def render_2d(
        self,
        fname: str,
        *,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Any:
        name = str(fname)

        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            return self._model.render_png_2d(name)

        return self._run(_work, on_done=on_done, on_error=on_error)
