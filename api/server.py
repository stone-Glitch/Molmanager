#!/usr/bin/env python3
"""MolManager 的可选 HTTP 接口层（FastAPI）。

启动：
    uvicorn api.server:app --reload --port 8000
    # 交互式文档： http://127.0.0.1:8000/docs

设计约定：
  1. **依赖可缺省** —— 没装 ``.[api]`` 时导入本模块会抛出带安装指引的 ImportError，
     而不是让人看不懂的 ``ModuleNotFoundError: No module named 'fastapi'``；
  2. **化学后端可缺省** —— OpenBabel 缺失时，需要它的端点返回 503 + 明确的指引，
     ``/health`` 与 ``/query``（纯 Python）照常可用；
  3. **不碰 GUI** —— 本层只调 ``chem`` / ``utils`` 的函数，不 import 任何 Tkinter 模块。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
except ImportError as _exc:  # pragma: no cover - 取决于是否安装了 [api] 依赖
    raise ImportError(f'接口层需要额外依赖，请先安装：\n    pip install -e ".[api]"\n（原始错误：{_exc}）') from _exc

from utils.version import APP_DISPLAY_NAME, APP_NAME, get_full_version

from . import capabilities
from .jobs import jobs
from .models import (
    Capabilities,
    ChemQueryRequest,
    ChemQueryResponse,
    DescriptorRequest,
    DescriptorResponse,
    ErrorResponse,
    HealthResponse,
    InChIKeyItem,
    InChIKeyRequest,
    InChIKeyResponse,
    JobStatus,
    JobSubmitResponse,
    Psi4ComputeRequest,
    ReactionAnimateRequest,
    SimilarityHit,
    SimilarityRequest,
    SimilarityResponse,
    SubstructureRequest,
    SubstructureResponse,
)

# ---------------------------------------------------------------- 应用

DESCRIPTION = f"""
**{APP_NAME} · {APP_DISPLAY_NAME}** 的 HTTP 接口层。

底层能力全部复用桌面版已有的实现（OpenBabel 指纹 / SMARTS、PSI4 计算、化学条件查询），
本层只做参数校验与结果封装，不重复实现任何化学逻辑。

> 需要 OpenBabel 的端点在后端缺失时会返回 **503**，可先访问 `/health` 查看可用能力。
"""


def create_app() -> FastAPI:
    """构造 FastAPI 应用（便于测试时注入不同配置）。"""
    application = FastAPI(
        title=f"{APP_NAME} API",
        description=DESCRIPTION,
        version=get_full_version(),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ---------------- 健康检查 ----------------
    @application.get(
        "/health",
        response_model=HealthResponse,
        summary="健康检查与后端能力探测",
        tags=["系统"],
    )
    def health(refresh: bool = False) -> HealthResponse:
        caps = capabilities.detect(refresh=refresh)
        return HealthResponse(
            status="ok",
            app=APP_NAME,
            version=get_full_version(),
            capabilities=Capabilities(**caps),
        )

    # ---------------- InChIKey ----------------
    @application.post(
        "/inchikey",
        response_model=InChIKeyResponse,
        responses={503: {"model": ErrorResponse}},
        summary="SMILES → InChIKey（支持批量）",
        tags=["分子标识"],
    )
    def inchikey(req: InChIKeyRequest) -> InChIKeyResponse:
        _require_pybel()
        from chem.openbabel_utils import smiles_to_inchikey

        smiles_list = list(req.smiles_list)
        if req.smiles:
            if req.smiles not in smiles_list:
                smiles_list.insert(0, req.smiles)

        if not smiles_list:
            raise HTTPException(status_code=400, detail="smiles 与 smiles_list 不能同时为空")

        items: list[InChIKeyItem] = []
        ok_count = 0
        for smi in smiles_list:
            try:
                r = smiles_to_inchikey(smi)
            except Exception as exc:  # 单个失败不影响整体
                items.append(InChIKeyItem(smiles=smi, success=False, error=_fmt_exc(exc)))
                continue
            if not r.get("success"):
                items.append(InChIKeyItem(smiles=smi, success=False, error=str(r.get("message") or "解析失败")))
                continue
            ok_count += 1
            items.append(
                InChIKeyItem(
                    smiles=smi,
                    success=True,
                    inchikey=r.get("inchikey"),
                    skeleton_14=r.get("skeleton_14"),
                    canonical_smiles=r.get("canonical_smiles"),
                    formula=r.get("formula"),
                )
            )

        return InChIKeyResponse(
            success=ok_count > 0,
            count=ok_count,
            total=len(items),
            results=items,
        )

    # ---------------- 描述符 ----------------
    @application.post(
        "/descriptors",
        response_model=DescriptorResponse,
        responses={503: {"model": ErrorResponse}},
        summary="分子描述符（MW / logP / TPSA / HBD / HBA …）",
        tags=["分子性质"],
    )
    def descriptors(req: DescriptorRequest) -> DescriptorResponse:
        _require_pybel()
        from chem.openbabel_utils import calculate_descriptors

        if not req.smiles and not req.path:
            raise HTTPException(status_code=400, detail="smiles 与 path 必须提供一个")

        # 给了 SMILES：落到临时文件再走既有实现（底层 API 只吃路径）
        if req.smiles:
            tmp_path = _write_smiles_temp(req.smiles)
            source = "smiles"
            cleanup = True
        else:
            tmp_path = str(req.path)
            source = "path"
            cleanup = False
            if not os.path.isfile(tmp_path):
                raise HTTPException(status_code=404, detail=f"文件不存在：{tmp_path}")

        try:
            result = calculate_descriptors(tmp_path)
        finally:
            if cleanup:
                _silent_unlink(tmp_path)

        return DescriptorResponse(
            success=bool(result.get("success")),
            message=str(result.get("message") or ""),
            source=source,
            descriptors=result.get("descriptors") or {},
        )

    # ---------------- 子结构检索 ----------------
    @application.post(
        "/substructure",
        response_model=SubstructureResponse,
        responses={503: {"model": ErrorResponse}},
        summary="SMARTS 子结构检索",
        tags=["检索"],
    )
    def substructure(req: SubstructureRequest) -> SubstructureResponse:
        _require_openbabel()
        from chem.openbabel_utils import substructure_search

        if not req.smarts.strip():
            raise HTTPException(status_code=400, detail="smarts 不能为空")

        try:
            matched = substructure_search(req.smarts, req.molecules, req.fmt)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"SMARTS 检索失败：{_fmt_exc(exc)}") from exc

        return SubstructureResponse(
            success=True,
            smarts=req.smarts,
            matched=matched,
            total=len(req.molecules),
            matched_count=len(matched),
        )

    # ---------------- 相似性检索 ----------------
    @application.post(
        "/similarity",
        response_model=SimilarityResponse,
        responses={503: {"model": ErrorResponse}},
        summary="指纹相似性检索（OpenBabel FP2，零额外依赖）",
        tags=["检索"],
    )
    def similarity(req: SimilarityRequest) -> SimilarityResponse:
        _require_openbabel()
        from chem.openbabel_utils import similarity_search

        try:
            hits = similarity_search(
                req.query,
                req.molecules,
                fmt=req.fmt,
                fptype=req.fptype,
                threshold=req.threshold,
                top_n=req.top_n,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"相似性检索失败：{_fmt_exc(exc)}") from exc

        return SimilarityResponse(
            success=True,
            query=req.query,
            fptype=req.fptype,
            hits=[SimilarityHit(molecule=m, similarity=round(float(s), 6)) for m, s in hits],
            total=len(req.molecules),
        )

    # ---------------- 化学条件查询 ----------------
    @application.post(
        "/query",
        response_model=ChemQueryResponse,
        summary="对条目列表跑化学条件过滤（纯 Python，无需 OpenBabel）",
        tags=["检索"],
    )
    def chem_query(req: ChemQueryRequest) -> ChemQueryResponse:
        from utils.chem_query import filter_entries

        try:
            matched = filter_entries(req.entries, req.query)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"查询解析失败：{_fmt_exc(exc)}") from exc

        return ChemQueryResponse(
            success=True,
            query=req.query,
            matched=matched,
            total=len(req.entries),
            matched_count=len(matched),
        )

    # ---------------- 量子反应计算（PSI4，后台任务 + WebSocket 进度） ----------------
    @application.post(
        "/psi4/compute",
        response_model=JobSubmitResponse,
        responses={503: {"model": ErrorResponse}},
        summary="提交 PSI4 量子反应能计算（后台执行，立即返回 job_id）",
        tags=["计算"],
    )
    def psi4_compute(req: Psi4ComputeRequest) -> JobSubmitResponse:
        _require_psi4()
        job_id = uuid.uuid4().hex
        payload: dict[str, Any] = {
            "method": req.method,
            "basis": req.basis,
            "n_frames": req.n_frames,
            "do_traj_energy": req.do_traj_energy,
            "do_thermo": req.do_thermo,
        }
        if req.reaction_id:
            payload["reaction_id"] = req.reaction_id
        else:
            assert req.custom is not None  # model_validator 已保证二选一
            payload["custom"] = {
                "reactants": list(req.custom.reactants),
                "products": list(req.custom.products),
            }
        run_dir = Path(tempfile.mkdtemp(prefix="mm_psi4_"))

        # 统一走 Service 层（api → services），消除与桌面端的领域调用重复。
        # 领域回调不传，由 WebDispatcher 的 emit 事件桥接为 WebSocket 推送。
        service = _get_quantum_service()
        service.compute(payload, run_dir, scheduler_id=job_id)

        return JobSubmitResponse(job_id=job_id, status="queued", ws_url=f"/ws/jobs/{job_id}")

    # ---------------- 反应动画 / IQmol 轨迹（后台任务 + WebSocket 进度） ----------------
    @application.post(
        "/reaction/animate",
        response_model=JobSubmitResponse,
        summary="生成反应动画 / IQmol 轨迹（后台执行，立即返回 job_id）",
        tags=["计算"],
    )
    def reaction_animate(req: ReactionAnimateRequest) -> JobSubmitResponse:
        job_id = uuid.uuid4().hex
        out_dir = Path(tempfile.mkdtemp(prefix="mm_rxn_"))
        single = len(req.reactants) == 1 and len(req.products) == 1

        out_ext = "mp4" if req.fmt == "mp4" else "gif"
        out_path = str(out_dir / f"anim.{out_ext}")
        traj_path = str(out_dir / f"traj.{req.traj_fmt}") if req.traj else ""

        # 统一走 Service 层（api → services）：把请求参数映射为 Service 参数，
        # 由 ReactionService 内部决定调用 generate_reaction_animation /
        # generate_xyz_trajectory / generate_reaction_multispecies。
        if req.traj:
            fmt = "none"  # 仅生成轨迹，不生成可视化动画
            out_path = ""
        elif single:
            fmt = req.fmt
        else:
            # 多物种可视化走 multispecies 分支（Service 内按 len(reactants) 自动分流）
            fmt = req.fmt

        service = _get_reaction_service()
        service.start_animation(
            reactants=list(req.reactants),
            products=list(req.products),
            out=out_path,
            traj=traj_path,
            mode=req.mode,
            fmt=fmt,
            resolution="hd",
            traj_fmt=req.traj_fmt,
            spacing=5.0,
            steps=req.steps,
            smooth=req.smooth,
            ffmpeg=req.ffmpeg_path,
            fps=req.fps,
            scheduler_id=job_id,
            pool="io",
        )

        return JobSubmitResponse(job_id=job_id, status="queued", ws_url=f"/ws/jobs/{job_id}")

    # ---------------- 任务状态查询（轮询兜底） ----------------
    @application.get(
        "/jobs/{job_id}",
        response_model=JobStatus,
        responses={404: {"model": ErrorResponse}},
        summary="查询后台任务状态（WebSocket 不可用时的轮询兜底）",
        tags=["计算"],
    )
    def get_job(job_id: str) -> JobStatus:
        st = jobs.get_status(job_id)
        if st is None:
            raise HTTPException(status_code=404, detail=f"未知 job_id: {job_id}")
        return JobStatus(**st)

    # ---------------- 任务进度（WebSocket 实时推送） ----------------
    @application.websocket("/ws/jobs/{job_id}")
    async def ws_job(websocket: WebSocket, job_id: str) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        jobs.attach_emit(job_id, lambda e: loop.call_soon_threadsafe(queue.put_nowait, e))
        try:
            # 未知任务：直接报错关闭，避免无限等待
            if jobs.get_status(job_id) is None:
                await websocket.send_json(
                    {"type": "error", "job_id": job_id, "error": f"未知 job_id: {job_id}"}
                )
                return
            while True:
                # 接收客户端消息（支持取消）
                try:
                    msg = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
                    if isinstance(msg, dict) and msg.get("action") == "cancel":
                        jobs.cancel(job_id)
                except asyncio.TimeoutError:
                    pass
                # 排空队列
                while not queue.empty():
                    event = queue.get_nowait()
                    await websocket.send_json(event)
                    if event.get("type") in ("done", "error", "cancelled"):
                        return
                # 兜底：任务已终态且队列已空 → 合成终态事件后关闭
                st = jobs.get_status(job_id)
                if st is not None and st["status"] in ("done", "error", "cancelled"):
                    await websocket.send_json(
                        {
                            "type": st["status"],
                            "job_id": job_id,
                            "result": st["result"],
                            "error": st["error"],
                        }
                    )
                    return
        except WebSocketDisconnect:
            pass
        finally:
            jobs.detach_emit(job_id)

    # ---------------- 全局异常兜底 ----------------
    @application.exception_handler(Exception)
    async def _unhandled(request, exc):  # type: ignore[no-untyped-def]  # FastAPI 要求此签名
        # 只在 DEBUG 下回传堆栈，避免把内部路径泄漏给调用方
        detail = _fmt_exc(exc)
        if os.environ.get("MOLMANAGER_DEBUG", "").lower() in ("1", "true", "yes"):
            detail += "\n" + traceback.format_exc()
        return JSONResponse(status_code=500, content={"success": False, "detail": detail})

    return application


app = create_app()


# ---------------------------------------------------------------- 内部辅助


def _get_quantum_service():
    """返回绑定到 WebDispatcher（共享 ``api.jobs.jobs``）的 QuantumReactionService。

    Service 复用桌面端的领域调用（``chem.quantum_reaction.run_reaction``），
    本例不再直连 chem.*，从而消除 ``api/`` 与 ``services/`` 的重复。
    """
    from services import QuantumReactionService

    from .dispatcher import WebDispatcher

    return QuantumReactionService(scheduler=WebDispatcher(jobs))


def _get_reaction_service():
    """返回绑定到 WebDispatcher 的 ReactionService（反应动画/轨迹）。"""
    from services import ReactionService

    from .dispatcher import WebDispatcher

    return ReactionService(scheduler=WebDispatcher(jobs))


def _require_openbabel() -> None:
    """OpenBabel（含命令行 obabel）不可用时抛 503。"""
    caps = capabilities.detect()
    if caps.get("openbabel") or caps.get("obabel_cli"):
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "后端缺少 OpenBabel，无法完成该请求。\n"
            "conda：conda install -c conda-forge openbabel\n"
            "pip  ：pip install openbabel-wheel"
        ),
    )


def _require_pybel() -> None:
    """需要 OpenBabel 的 **Python 绑定** 时调用（比 _require_openbabel 更严格）。"""
    caps = capabilities.detect()
    if caps.get("pybel"):
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "后端缺少 OpenBabel 的 Python 绑定（pybel），无法完成该请求。\n"
            "conda：conda install -c conda-forge openbabel\n"
            "pip  ：pip install openbabel-wheel\n"
            "仅装了命令行 obabel 时，请用桌面版 GUI 执行该操作。"
        ),
    )


def _require_psi4() -> None:
    """PSI4 不可用时抛 503（量子化学计算依赖它）。"""
    caps = capabilities.detect()
    if caps.get("psi4"):
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "后端缺少 PSI4，无法执行量子化学计算。\n"
            "conda：conda install -c conda-forge psi4\n"
            "仅装了 OpenBabel 时，请用桌面版 GUI 执行该操作。"
        ),
    )


def _write_smiles_temp(smiles: str) -> str:
    """把 SMILES 写成临时 .smi 文件，返回路径。"""
    import tempfile

    fd, path = tempfile.mkstemp(prefix="molmanager_api_", suffix=".smi")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(smiles.strip())
            f.write("\n")
    except Exception:
        _silent_unlink(path)
        raise
    return path


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _fmt_exc(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


__all__ = ["app", "create_app"]
