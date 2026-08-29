#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api/ —— FastAPI 接口层。

需要 ``pip install -e ".[api]"``（fastapi + httpx），缺失时整文件跳过。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason='接口层依赖未安装：pip install -e ".[api]"')
pytest.importorskip("httpx", reason="TestClient 需要 httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from utils.version import get_full_version  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------- 健康检查
def test_health_reports_capabilities(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["app"] == "MolManager"
    assert data["version"] == get_full_version()
    caps = data["capabilities"]
    for key in ("openbabel", "pybel", "psi4", "obabel_cli"):
        assert key in caps


def test_openapi_schema_is_generated(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for ep in ("/health", "/inchikey", "/descriptors", "/substructure", "/similarity", "/query"):
        assert ep in paths, f"README 承诺的端点 {ep} 未注册"


# ---------------------------------------------------------------- 纯 Python 端点
def test_query_filters_by_conditions(client: TestClient) -> None:
    r = client.post("/query", json={
        "entries": [
            {"name": "aspirin", "MW": 180.16, "formula": "C9H8O4"},
            {"name": "ethanol", "MW": 46.07, "formula": "C2H6O"},
        ],
        "query": "mw:>100",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["matched_count"] == 1
    assert data["matched"][0]["name"] == "aspirin"
    assert data["total"] == 2


def test_query_with_empty_query_returns_all(client: TestClient) -> None:
    entries = [{"name": "a"}, {"name": "b"}]
    r = client.post("/query", json={"entries": entries, "query": ""})
    assert r.json()["matched_count"] == 2


def test_query_accepts_empty_entries(client: TestClient) -> None:
    r = client.post("/query", json={"entries": [], "query": "mw:>1"})
    assert r.status_code == 200
    assert r.json()["matched"] == []


# ---------------------------------------------------------------- 参数校验
def test_inchikey_rejects_empty_payload(client: TestClient) -> None:
    r = client.post("/inchikey", json={})
    # 缺 OpenBabel 时先返回 503，装了则应有 400
    assert r.status_code in (400, 503)


def test_descriptors_rejects_empty_payload(client: TestClient) -> None:
    r = client.post("/descriptors", json={})
    assert r.status_code in (400, 503)


def test_substructure_rejects_empty_smarts(client: TestClient) -> None:
    r = client.post("/substructure", json={"smarts": "  ", "molecules": ["CCO"]})
    assert r.status_code in (400, 503)


def test_similarity_rejects_out_of_range_threshold(client: TestClient) -> None:
    r = client.post("/similarity", json={
        "query": "CCO", "molecules": ["CCO"], "threshold": 5.0,
    })
    assert r.status_code == 422  # pydantic 校验拦截


# ---------------------------------------------------------------- 需要 OpenBabel 的端点
def test_inchikey_endpoint(client: TestClient, requires_pybel: None) -> None:
    r = client.post("/inchikey", json={"smiles_list": ["CC(=O)Oc1ccccc1C(=O)O", "###bad###"]})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["count"] == 1
    first = data["results"][0]
    assert first["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert data["results"][1]["success"] is False


def test_descriptors_endpoint_from_smiles(client: TestClient, requires_pybel: None) -> None:
    r = client.post("/descriptors", json={"smiles": "CC(=O)Oc1ccccc1C(=O)O"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True, data
    assert data["source"] == "smiles"
    # SMILES 读入不显式加氢 → 13 个重原子（9 C + 4 O）；分子量按隐式氢计算
    assert data["descriptors"]["num_atoms"] == 13
    assert data["descriptors"]["molecular_weight"] == pytest.approx(180.16, abs=1.0)


def test_descriptors_endpoint_missing_file(client: TestClient, requires_pybel: None) -> None:
    r = client.post("/descriptors", json={"path": "/definitely/not/exist.mol"})
    assert r.status_code == 404


def test_substructure_endpoint(client: TestClient, requires_openbabel: None) -> None:
    r = client.post("/substructure", json={
        "smarts": "C(=O)O",
        "molecules": ["CCO", "CC(=O)O"],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["matched"] == ["CC(=O)O"]
    assert data["matched_count"] == 1


def test_similarity_endpoint(client: TestClient, requires_openbabel: None) -> None:
    r = client.post("/similarity", json={
        "query": "c1ccccc1",
        "molecules": ["c1ccccc1C", "CCO"],
        "threshold": 0.0,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["hits"][0]["molecule"] == "c1ccccc1C"
    assert 0.0 <= data["hits"][0]["similarity"] <= 1.0


# ---------------------------------------------------------------- 降级行为
def test_chem_endpoints_return_503_without_openbabel(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟后端缺失：必须返回 503 + 安装指引，而不是 500 或崩溃。"""
    from api import capabilities

    monkeypatch.setattr(capabilities, "detect", lambda refresh=False: {
        "openbabel": False, "pybel": False, "obabel_cli": None,
        "psi4": False, "psi4_version": None, "openbabel_version": None,
    })
    r = client.post("/inchikey", json={"smiles": "CCO"})
    assert r.status_code == 503
    assert "OpenBabel" in r.json()["detail"]
