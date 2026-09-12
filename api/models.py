#!/usr/bin/env python3
"""FastAPI 接口层的请求 / 响应模型。

只依赖 ``pydantic``（项目本就依赖它做配置校验），不依赖 FastAPI 本体，
因此没有装 ``.[api]`` 额外依赖时也能安全导入本模块。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------- InChIKey
class InChIKeyRequest(BaseModel):
    """SMILES → InChIKey（单条或批量二选一，都给了就合并处理）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"smiles_list": ["CCO", "c1ccccc1", "CC(=O)Oc1ccccc1C(=O)O"]},
        }
    )

    smiles: str | None = Field(default=None, description="单条 SMILES")
    smiles_list: list[str] = Field(default_factory=list, description="批量 SMILES")

    @field_validator("smiles_list", mode="before")
    @classmethod
    def _dedup(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        # 保序去重，空串直接丢弃
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


class InChIKeyItem(BaseModel):
    """单条 SMILES 的 InChIKey 结果。"""

    smiles: str
    success: bool
    inchikey: str | None = None
    skeleton_14: str | None = Field(default=None, description="InChIKey 第一段，骨架相同即近似命中")
    canonical_smiles: str | None = None
    formula: str | None = None
    error: str | None = None


class InChIKeyResponse(BaseModel):
    success: bool
    count: int = Field(default=0, description="成功解析的条数")
    total: int = Field(default=0, description="请求总条数")
    results: list[InChIKeyItem] = Field(default_factory=list)


# ---------------------------------------------------------------- 描述符
class DescriptorRequest(BaseModel):
    """分子描述符计算：给 SMILES（写临时文件）或给本地文件路径。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"},
        }
    )

    smiles: str | None = Field(default=None, description="SMILES 字符串（与 path 二选一）")
    path: str | None = Field(default=None, description="本地分子文件路径（mol/sdf/pdb/xyz…）")

    @field_validator("smiles", "path", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class DescriptorResponse(BaseModel):
    success: bool
    message: str = ""
    source: str | None = Field(default=None, description="实际计算的来源（smiles / path）")
    descriptors: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 子结构检索
class SubstructureRequest(BaseModel):
    """SMARTS 子结构检索。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "smarts": "C(=O)O",
                "molecules": ["CCO", "CC(=O)O", "c1ccccc1C(=O)O"],
            },
        }
    )

    smarts: str = Field(..., description="SMARTS 子结构模式，例如 C-O、[NH2]")
    molecules: list[str] = Field(..., description="待检索分子文本（SMILES 或 molfile）")
    fmt: str = Field(default="smi", description="分子输入格式：smi / mol")


class SubstructureResponse(BaseModel):
    success: bool
    message: str = ""
    smarts: str
    matched: list[str] = Field(default_factory=list)
    total: int = 0
    matched_count: int = 0


# ---------------------------------------------------------------- 相似性检索
class SimilarityRequest(BaseModel):
    """指纹相似性检索（OpenBabel FP2 等，零额外依赖）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "CC(=O)Oc1ccccc1C(=O)O",
                "molecules": ["CC(=O)Oc1ccccc1C(=O)O", "CCO", "c1ccccc1"],
                "threshold": 0.3,
                "top_n": 10,
            },
        }
    )

    query: str = Field(..., description="查询分子（SMILES 或 molfile 文本）")
    molecules: list[str] = Field(..., description="候选分子文本列表")
    fmt: str = Field(default="smi", description="分子输入格式：smi / mol")
    fptype: str = Field(default="FP2", description="指纹类型：FP2 / FP3 / FP4 / MACCS / EState")
    threshold: float = Field(default=0.3, ge=0.0, le=1.0, description="相似度下限 0~1")
    top_n: int | None = Field(default=None, ge=1, description="只返回相似度最高的 N 条")


class SimilarityHit(BaseModel):
    molecule: str
    similarity: float


class SimilarityResponse(BaseModel):
    success: bool
    message: str = ""
    query: str
    fptype: str
    hits: list[SimilarityHit] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------- 化学条件查询
class ChemQueryRequest(BaseModel):
    """对内存中的条目列表跑化学条件过滤（utils.chem_query 的 HTTP 封装）。

    支持 ``MW>200``、``logP<3`` 这类数值条件与自由文本，
    详见 ``utils/chem_query.py`` 的 ``parse_chem_query``。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "entries": [
                    {"name": "aspirin", "MW": 180.16, "logP": 1.2, "formula": "C9H8O4"},
                    {"name": "ethanol", "MW": 46.07, "logP": -0.18, "formula": "C2H6O"},
                ],
                "query": "MW>100 logP<3",
            },
        }
    )

    entries: list[dict[str, Any]] = Field(..., description="待过滤条目（任意键的 dict 列表）")
    query: str = Field(default="", description="查询串，例如 'MW>200 logP<3 芳香'")


class ChemQueryResponse(BaseModel):
    success: bool
    message: str = ""
    query: str
    matched: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    matched_count: int = 0


# ---------------------------------------------------------------- 健康检查
class Capabilities(BaseModel):
    """后端能力探测结果：缺哪个依赖一眼可见。"""

    openbabel: bool = False
    pybel: bool = False
    obabel_cli: str | None = None
    psi4: bool = False
    psi4_version: str | None = None
    openbabel_version: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str = "MolManager"
    version: str = ""
    capabilities: Capabilities = Field(default_factory=Capabilities)


class ErrorResponse(BaseModel):
    """统一的错误响应体。"""

    success: bool = False
    detail: str


# ---------------------------------------------------------------- 量子反应计算（PSI4）
class CustomReaction(BaseModel):
    """自定义反应物 / 产物（沿用桌面端 ``:N`` 多重态语法，例如 ``O=O:3``）。"""

    reactants: list[str] = Field(default_factory=list, description="反应物列表（SMILES，可带 :N 多重态）")
    products: list[str] = Field(default_factory=list, description="产物列表（SMILES，可带 :N 多重态）")


class Psi4ComputeRequest(BaseModel):
    """PSI4 量子反应能计算请求。

    预设反应（``reaction_id``）与自定义反应（``custom``）二选一。
    字段与 ``chem.quantum_reaction.runner.run_reaction`` 的 payload 1:1 对应。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "custom": {"reactants": ["O=O"], "products": ["[O]"]},
                "method": "hf",
                "basis": "sto-3g",
                "n_frames": 15,
            },
        }
    )

    reaction_id: str | None = Field(default=None, description="预设反应 id（与 custom 二选一）")
    custom: CustomReaction | None = Field(default=None, description="自定义反应物/产物（与 reaction_id 二选一）")
    method: str = Field(default="hf", description="计算方法，例如 hf / b3lyp / wb97x-d")
    basis: str = Field(default="sto-3g", description="基组，例如 sto-3g / 6-31g*")
    n_frames: int = Field(default=15, description="反应路径采样帧数", ge=1, le=200)
    do_traj_energy: bool = Field(default=False, description="是否在轨迹每帧附能量 E")
    do_thermo: bool = Field(default=True, description="是否做热力学量计算")

    @model_validator(mode="after")
    def _require_reaction_source(self) -> "Psi4ComputeRequest":
        if not self.reaction_id and not self.custom:
            raise ValueError("reaction_id 与 custom 必须至少提供一个")
        if self.reaction_id and self.custom:
            raise ValueError("reaction_id 与 custom 不能同时提供，二选一")
        return self

    @field_validator("method", "basis", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ReactionAnimateRequest(BaseModel):
    """反应动画 / IQmol 轨迹生成请求。

    阶段1 直接接收**服务端可访问的文件路径**（XYZ）。Phase3 将改为上传 +
    ``utils/path_utils`` 校验（路径遍历在 Web 下风险更大）。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reactants": ["/data/reactant.xyz"],
                "products": ["/data/product.xyz"],
                "fmt": "gif",
                "steps": 30,
            },
        }
    )

    reactants: list[str] = Field(default_factory=list, description="反应物 XYZ 文件路径")
    products: list[str] = Field(default_factory=list, description="产物 XYZ 文件路径")
    fmt: str = Field(default="gif", description="可视化格式：gif / mp4 / none")
    steps: int = Field(default=30, description="插值步数", ge=1, le=500)
    mode: str = Field(default="bounce", description="插值模式：bounce / linear …")
    smooth: bool = Field(default=True, description="是否平滑插值")
    fps: int = Field(default=15, description="视频帧率（fmt=mp4 时有效）", ge=1, le=120)
    ffmpeg_path: str = Field(default="ffmpeg", description="ffmpeg 可执行路径")
    traj: bool = Field(default=False, description="是否同时生成 IQmol 轨迹")
    traj_fmt: str = Field(default="xyz", description="轨迹格式：xyz / mol2 …")


# ---------------------------------------------------------------- 异步任务状态
class JobSubmitResponse(BaseModel):
    """提交后台任务后立刻返回的句柄。"""

    job_id: str = Field(description="任务唯一 id，用于查询 / 订阅进度")
    status: str = Field(default="queued", description="初始状态")
    ws_url: str = Field(description="WebSocket 进度订阅地址，例如 /ws/jobs/{job_id}")


class JobStatus(BaseModel):
    """后台任务轮询状态（GET /jobs/{job_id}）。"""

    job_id: str
    status: str = Field(description="queued / running / done / error / cancelled")
    progress: float = Field(default=0.0, description="0~1 进度（来自末次 stage 事件）")
    message: str = Field(default="", description="末条日志")
    result: dict[str, Any] | None = Field(default=None, description="成功时的结果字典")
    error: str | None = Field(default=None, description="失败时的错误信息")


class ProgressEvent(BaseModel):
    """WebSocket 推送的进度事件。"""

    job_id: str
    type: str = Field(description="log / stage / done / error / cancelled")
    message: str | None = Field(default=None)
    fraction: float | None = Field(default=None, description="stage 事件的进度 0~1")
    result: dict[str, Any] | None = Field(default=None)
    error: str | None = Field(default=None)
