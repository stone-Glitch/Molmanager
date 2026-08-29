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

import os
import traceback

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
except ImportError as _exc:  # pragma: no cover - 取决于是否安装了 [api] 依赖
    raise ImportError(f'接口层需要额外依赖，请先安装：\n    pip install -e ".[api]"\n（原始错误：{_exc}）') from _exc

from utils.version import APP_DISPLAY_NAME, APP_NAME, get_full_version

from . import capabilities
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
