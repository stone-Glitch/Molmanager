"""反应动画 Service：把 ``chem.reaction_animation`` 的领域调用与后台派发收口。

框架无关：只依赖 ``chem.reaction_animation`` / ``chem.psi4.utils`` 与注入的
``task_manager``。UI 层（reaction_dialog）负责收集参数、提供 ``on_done`` 回调
做结果渲染，不再直接碰 ``task_manager`` 或 ``run_task``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .base import ServiceBase


def _do_preview(reactants, products, spacing, preview_path) -> dict:
    """生成首帧预览图（领域逻辑，纯计算，无 UI 依赖）。"""
    import chem.reaction_animation as ra

    if len(reactants) == 1 and len(products) == 1:
        return ra.preview_first_frame(
            reactants[0], products[0], preview_path, width=800, height=600
        )

    import tempfile

    from chem.reaction_animation import (
        _auto_reorder_atoms,
        _concat_xyz_files,
        _write_xyz,
    )

    with tempfile.TemporaryDirectory(prefix="ms_preview_") as td:
        tdp = Path(td)
        nR, aR, cR = _concat_xyz_files(reactants, translate_spacing=spacing)
        nP, aP, cP = _concat_xyz_files(products, translate_spacing=spacing)
        aP2, cP2 = _auto_reorder_atoms(aR, cR, aP, cP)
        rx = tdp / "R.xyz"
        px = tdp / "P.xyz"
        rx.write_text(_write_xyz(nR, aR, cR), encoding="utf-8")
        px.write_text(_write_xyz(nP, aP2, cP2), encoding="utf-8")
        return ra.preview_first_frame(
            str(rx), str(px), preview_path, width=800, height=600
        )


def _do_generate(
    reactants,
    products,
    out,
    traj,
    *,
    mode,
    fmt,
    resolution,
    traj_fmt,
    spacing,
    steps,
    smooth,
    ffmpeg,
    fps,
    progress_callback=None,
) -> dict:
    """生成可视化动画 +（可选）IQmol 轨迹（领域逻辑，纯计算，无 UI 依赖）。"""
    import tempfile as _tf

    import chem.reaction_animation as ra
    from chem.psi4.utils import _write_xyz

    msgs: list[str] = []
    viz_ok = traj_ok = False
    viz_out = traj_out = None

    if fmt != "none" and out:
        if progress_callback:
            progress_callback(0, "开始生成可视化动画")
        if len(reactants) == 1 and len(products) == 1:
            r = ra.generate_reaction_animation(
                reactants[0],
                products[0],
                out,
                steps=steps,
                mode=mode,
                smooth=smooth,
                fmt=fmt,
                resolution=resolution,
                ffmpeg_path=ffmpeg,
                fps=fps,
                progress_callback=progress_callback,
            )
        else:
            with _tf.TemporaryDirectory(prefix="ms_viz_") as _td:
                _tdp = Path(_td)
                _nR, _aR, _cR = ra._concat_xyz_files(reactants, translate_spacing=spacing)
                _nP, _aP, _cP = ra._concat_xyz_files(products, translate_spacing=spacing)
                try:
                    _aP2, _cP2 = ra._auto_reorder_atoms(_aR, _cR, _aP, _cP)
                except Exception as _e:
                    msgs.append("❌ 可视化（反应物/产物）原子对齐失败: " + str(_e))
                    r = {"success": False, "error": "原子对齐失败"}
                    _aP2, _cP2 = _aP, _cP
                else:
                    _rx = _tdp / "R.xyz"
                    _px = _tdp / "P.xyz"
                    _rx.write_text(_write_xyz(_nR, _aR, _cR), encoding="utf-8")
                    _px.write_text(_write_xyz(_nP, _aP2, _cP2), encoding="utf-8")
                    r = ra.generate_reaction_animation(
                        str(_rx),
                        str(_px),
                        out,
                        steps=steps,
                        mode=mode,
                        smooth=smooth,
                        fmt=fmt,
                        resolution=resolution,
                        ffmpeg_path=ffmpeg,
                        fps=fps,
                        progress_callback=progress_callback,
                    )
        viz_ok = bool(r.get("success"))
        viz_out = r.get("output")
        if viz_ok:
            msgs.append(f"✅ 可视化: {viz_out} （{r.get('n_frames')} 帧）")
        else:
            msgs.append("❌ 可视化: " + (r.get("error") or "未知错误"))
            if r.get("frames_dir"):
                msgs.append("   帧目录已保留: " + r["frames_dir"])

    if traj:
        if progress_callback:
            progress_callback(0, "开始生成 IQmol 轨迹")
        if len(reactants) == 1 and len(products) == 1:
            rr = ra.generate_xyz_trajectory(
                reactants[0],
                products[0],
                traj,
                steps=steps,
                mode=mode,
                smooth=smooth,
                trajectory_format=traj_fmt,
                progress_callback=progress_callback,
            )
        else:
            rr = ra.generate_reaction_multispecies(
                reactants,
                products,
                traj,
                steps=steps,
                mode=mode,
                smooth=smooth,
                trajectory_format=traj_fmt,
                translate_spacing=spacing,
                progress_callback=progress_callback,
            )
        traj_ok = bool(rr.get("success"))
        traj_out = rr.get("output")
        if traj_ok:
            tag = "（含每帧能量 E）" if rr.get("energies_written") else ""
            msgs.append(f"✅ IQmol 轨迹: {traj_out} （{rr.get('n_frames')} 帧） {tag}")
        else:
            msgs.append("❌ IQmol 轨迹: " + (rr.get("error") or "未知错误"))

    return {
        "msgs": msgs,
        "viz_ok": viz_ok,
        "traj_ok": traj_ok,
        "viz_out": viz_out,
        "traj_out": traj_out,
    }


class ReactionService(ServiceBase):
    def preview_frame(
        self,
        reactants,
        products,
        spacing,
        preview_path,
        *,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            return _do_preview(reactants, products, spacing, preview_path)

        self._run(_work, on_done=on_done, on_error=on_error)

    def start_animation(
        self,
        *,
        reactants,
        products,
        out,
        traj,
        mode,
        fmt,
        resolution,
        traj_fmt,
        spacing,
        steps,
        smooth,
        ffmpeg,
        fps,
        on_done: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        scheduler_id: Optional[str] = None,
        pool: str = "io",
    ):
        def _work(*, emit=None, should_cancel=None, progress_callback=None, log=None):  # noqa: ARG001
            def _pc(frac: float, msg: str = "") -> None:
                # 优先调用方注入的 progress_callback；否则走统一 emit 事件。
                if progress_callback is not None:
                    progress_callback(frac, msg)
                if emit is not None:
                    emit({"type": "stage", "message": str(msg), "fraction": float(frac)})

            return _do_generate(
                reactants,
                products,
                out,
                traj,
                mode=mode,
                fmt=fmt,
                resolution=resolution,
                traj_fmt=traj_fmt,
                spacing=spacing,
                steps=steps,
                smooth=smooth,
                ffmpeg=ffmpeg,
                fps=fps,
                progress_callback=_pc,
            )

        kwargs = {}
        if scheduler_id:
            kwargs["dispatch_kwargs"] = {"job_id": scheduler_id, "pool": pool}
        self._run(_work, on_done=on_done, on_error=on_error, **kwargs)
