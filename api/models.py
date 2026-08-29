#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI 接口层的请求 / 响应模型。

只依赖 ``pydantic``（项目本就依赖它做配置校验），不依赖 FastAPI 本体，
因此没有装 ``.[api]`` 额外依赖时也能安全导入本模块。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- InChIKey
class InChIKeyRequest(BaseModel):
    """SMILES → InChIKey（单条或批量二选一，都给了就合并处理）。"""

    model_config = ConfigDict(json_schema_extra={
        "example": {"smiles_list": ["CCO", "c1ccccc1", "CC(=O)Oc1ccccc1C(=O)O"]},
    })

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

    model_config = ConfigDict(json_schema_extra={
        "example": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"},
    })

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

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "smarts": "C(=O)O",
            "molecules": ["CCO", "CC(=O)O", "c1ccccc1C(=O)O"],
        },
    })

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

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "query": "CC(=O)Oc1ccccc1C(=O)O",
            "molecules": ["CC(=O)Oc1ccccc1C(=O)O", "CCO", "c1ccccc1"],
            "threshold": 0.3,
            "top_n": 10,
        },
    })

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

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "entries": [
                {"name": "aspirin", "MW": 180.16, "logP": 1.2, "formula": "C9H8O4"},
                {"name": "ethanol", "MW": 46.07, "logP": -0.18, "formula": "C2H6O"},
            ],
            "query": "MW>100 logP<3",
        },
    })

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
