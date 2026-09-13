#!/usr/bin/env python3
"""阶段3：文件上传端点 + 路径安全校验 + 前端单页挂载的验收测试。

覆盖：
  - ``POST /files/upload``：成功 / 扩展名拒绝 / 超大 413 / multipart 缺失 503；
  - ``_resolve_ref`` 的三种错误映射：越界/symlink → 403、不存在 → 404、其余 → 400；
  - ``POST /descriptors`` 支持 ``file_id``，且裸 ``path`` 收紧为上传根白名单；
  - ``POST /reaction/animate`` 的 reactants/products 逐个校验；
  - ``GET /`` 返回前端单页，``/docs`` 与 ``/openapi.json`` 不被静态挂载吃掉。

需要 ``pip install -e ".[api]"``；走真实上传路径的用例额外要求 python-multipart。
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import uploads
from api.server import ALLOW_SERVER_PATH_ENV, create_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

pytestmark = pytest.mark.api

HAS_MULTIPART = importlib.util.find_spec("multipart") is not None
requires_multipart = pytest.mark.skipif(not HAS_MULTIPART, reason="需要 python-multipart")

XYZ_A = b"3\nreactant\nO 0 0 0\nO 0 0 1.2\nH 0 0 -0.9\n"
XYZ_B = b"3\nproduct\nO 0 0 0\nH 0 0 1.0\nH 0 0 -1.0\n"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------- 前端单页挂载
def test_index_serves_single_page(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "MolManager" in r.text
    # 关键交互点存在
    assert "/files/upload" in r.text
    assert "/ws/jobs/" in r.text


def test_docs_and_openapi_not_shadowed(client: TestClient) -> None:
    """静态挂载不得占掉 /docs 与 /openapi.json（= 不能 StaticFiles(html=True) 挂根）。"""
    assert client.get("/openapi.json").status_code == 200
    r = client.get("/docs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert client.get("/redoc").status_code == 200


# ---------------------------------------------------------------- 上传端点
@requires_multipart
def test_upload_returns_file_id(client: TestClient) -> None:
    r = client.post("/files/upload", files={"file": ("benzene.xyz", XYZ_A, "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["name"] == "benzene.xyz"
    assert body["size"] == len(XYZ_A)
    p = uploads.resolve_file_id(body["file_id"])
    assert p.read_bytes() == XYZ_A


@requires_multipart
def test_upload_sanitizes_traversal_filename(client: TestClient) -> None:
    r = client.post("/files/upload", files={"file": ("../../etc/passwd.xyz", XYZ_A, "text/plain")})
    assert r.status_code == 200
    assert r.json()["name"] == "passwd.xyz"
    p = uploads.resolve_file_id(r.json()["file_id"])
    assert uploads.UPLOAD_ROOT.resolve() in p.parents


@requires_multipart
def test_upload_rejects_bad_extension(client: TestClient) -> None:
    r = client.post("/files/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert "不支持" in r.json()["detail"]


@requires_multipart
def test_upload_rejects_oversize(client: TestClient) -> None:
    big = b"x" * (uploads.MAX_BYTES + 1)
    r = client.post("/files/upload", files={"file": ("big.xyz", big, "text/plain")})
    assert r.status_code == 413
    assert "过大" in r.json()["detail"]


def test_upload_503_without_multipart(client: TestClient, monkeypatch) -> None:
    """multipart 依赖缺失时必须 503 + 安装指引，而不是 500。"""
    monkeypatch.setattr("api.server.importlib.util.find_spec", lambda name: None)
    r = client.post("/files/upload", files={"file": ("a.xyz", XYZ_A, "text/plain")})
    assert r.status_code == 503
    assert "python-multipart" in r.json()["detail"]


# ---------------------------------------------------------------- _resolve_ref 错误映射
def test_resolve_ref_missing_file_is_404(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    r = client.post("/descriptors", json={"path": "definitely-missing.xyz"})
    assert r.status_code == 404


def test_resolve_ref_outside_root_is_403(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    outside = Path("/etc/hosts")
    if not outside.is_file():  # pragma: no cover - 环境相关
        pytest.skip("缺少 /etc/hosts")
    r = client.post("/descriptors", json={"path": str(outside)})
    assert r.status_code == 403
    assert "越出" in r.json()["detail"] or "拒绝" in r.json()["detail"]


def test_descriptors_empty_input_is_400(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    r = client.post("/descriptors", json={})
    assert r.status_code == 400


def test_descriptors_smiles_and_file_id_conflict_is_400(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    r = client.post("/descriptors", json={"smiles": "CCO", "file_id": "x.xyz"})
    assert r.status_code == 400


# ---------------------------------------------------------------- descriptors via file_id
@requires_multipart
def test_descriptors_accepts_file_id(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    seen = {}

    def fake_calc(path):
        seen["path"] = str(path)
        return {"success": True, "message": "ok", "descriptors": {"MW": 18.02, "logP": -1.2}}

    monkeypatch.setattr("chem.openbabel_utils.calculate_descriptors", fake_calc)
    up = client.post("/files/upload", files={"file": ("mol.xyz", XYZ_A, "text/plain")}).json()

    r = client.post("/descriptors", json={"file_id": up["file_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["source"] == "file_id"
    assert body["descriptors"]["MW"] == 18.02
    assert seen["path"] == str(uploads.resolve_file_id(up["file_id"]))
    # 上传的文件不应被清理（归上传根统一管理）
    assert uploads.resolve_file_id(up["file_id"]).is_file()


@requires_multipart
def test_descriptors_bad_file_id_is_rejected(client: TestClient, monkeypatch) -> None:
    """`../` 不得穿越出去。file_id 分支会先在**上传根内**找该文件，找不到即 404。"""
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    r = client.post("/descriptors", json={"file_id": "../escape.xyz"})
    assert r.status_code in (400, 403, 404)
    assert "escape.xyz" in r.json()["detail"]


# ---------------------------------------------------------------- reaction/animate 校验
def test_reaction_animate_requires_nonempty_lists(client: TestClient) -> None:
    r = client.post("/reaction/animate", json={"reactants": [], "products": ["b.xyz"]})
    assert r.status_code == 400
    assert "不能为空" in r.json()["detail"]


def test_reaction_animate_missing_file_is_404(client: TestClient) -> None:
    r = client.post("/reaction/animate", json={"reactants": ["a.xyz"], "products": ["nope-xyz.xyz"]})
    assert r.status_code == 404


def test_reaction_animate_traversal_is_400(client: TestClient) -> None:
    r = client.post("/reaction/animate", json={"reactants": ["../../etc/passwd"], "products": ["b.xyz"]})
    assert r.status_code in (400, 403)


def test_reaction_animate_outside_root_is_403(client: TestClient) -> None:
    outside = Path("/etc/hosts")
    if not outside.is_file():  # pragma: no cover - 环境相关
        pytest.skip("缺少 /etc/hosts")
    r = client.post("/reaction/animate", json={"reactants": [str(outside)], "products": [str(outside)]})
    assert r.status_code == 403


# ---------------------------------------------------------------- 逃生开关
@requires_multipart
def test_allow_server_path_env_escape_hatch(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    """仅当显式设置环境变量时，裸 path 才允许指向上传根之外。"""
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"pybel": True})
    monkeypatch.setattr(
        "chem.openbabel_utils.calculate_descriptors",
        lambda path: {"success": True, "message": "ok", "descriptors": {"src": str(path)}},
    )
    outside = tmp_path / "outside.xyz"
    outside.write_text(XYZ_A.decode())

    # 默认关闭 → 403
    r = client.post("/descriptors", json={"path": str(outside)})
    assert r.status_code == 403

    # 显式开启 → 放行
    monkeypatch.setenv(ALLOW_SERVER_PATH_ENV, "1")
    r2 = client.post("/descriptors", json={"path": str(outside)})
    assert r2.status_code == 200
    assert r2.json()["descriptors"]["src"] == str(outside.resolve())
    monkeypatch.delenv(ALLOW_SERVER_PATH_ENV, raising=False)


# ---------------------------------------------------------------- 端到端：上传 → 动画(WS)
@requires_multipart
def test_end_to_end_upload_then_animate_over_websocket(client: TestClient, monkeypatch) -> None:
    import chem.reaction_animation as ra

    seen = {}

    def fake_anim(r, p, out, **kw):
        # 单物种分支：r / p 是单个路径字符串（不是列表），直接记录。
        seen["reactants"] = r
        seen["products"] = p
        # 回归：必须显式传 base_dir=输出目录，否则领域层会退回 CWD 判定并静默失败。
        seen["base_dir"] = kw.get("base_dir")
        return {"success": True, "output": str(out), "n_frames": 5}

    monkeypatch.setattr(ra, "generate_reaction_animation", fake_anim)

    up_a = client.post("/files/upload", files={"file": ("r.xyz", XYZ_A, "text/plain")}).json()
    up_b = client.post("/files/upload", files={"file": ("p.xyz", XYZ_B, "text/plain")}).json()

    r = client.post(
        "/reaction/animate",
        json={"reactants": [up_a["file_id"]], "products": [up_b["file_id"]], "fmt": "gif", "steps": 5},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        received: list[dict] = []
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("type") in ("done", "error", "cancelled"):
                break

    assert received[-1]["type"] == "done", received
    # ReactionService 的 done 结果是聚合摘要（msgs/viz_ok/viz_out…）
    assert received[-1]["result"]["viz_ok"] is True
    assert "5 帧" in " ".join(received[-1]["result"]["msgs"])
    # 领域层收到的必须是解析后的真实路径（上传根内）
    assert seen["reactants"].startswith(str(uploads.UPLOAD_ROOT))
    assert seen["products"].startswith(str(uploads.UPLOAD_ROOT))
    assert seen["reactants"].endswith("r.xyz")
    assert seen["products"].endswith("p.xyz")
    # 回归：base_dir 必须是输出文件所在目录（否则 CWD 判定会导致渲染静默失败）
    assert seen["base_dir"] is not None
    assert str(seen["base_dir"]).startswith("/tmp/mm_rxn_")


# ---------------------------------------------------------------- 进度归一化（回归）
def test_webdispatcher_normalizes_percent_to_fraction() -> None:
    """回归：Service 注入的 progress_callback 传 **0~100 百分比**，Web 事件契约要求 **0~1**。

    历史问题：``WebDispatcher._progress`` 把百分比原样塞进 ``fraction``，导致
    ``JobManager.progress`` 与前端进度条出现 500% / 1350% 这类溢出值。
    """
    from api.dispatcher import WebDispatcher
    from api.jobs import JobManager

    mgr = JobManager()
    d = WebDispatcher(mgr)
    harness = _ProgressHarness()
    handle = d.dispatch(harness.run, job_id="pct-norm", pool="io")
    st = None
    for _ in range(300):
        st = mgr.get_status("pct-norm")
        if st and st["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.01)
    assert st["status"] == "done"
    # 领域层给出的 0~100 进度必须被换算成 0~1
    assert st["progress"] == 1.0
    # 事件缓冲里记录的 fraction 也必须全部落在 [0,1]
    state = mgr._jobs.get(handle.job_id) if hasattr(mgr, "_jobs") else None
    events = list(getattr(state, "buffer", []) or [])
    fracs = [e["fraction"] for e in events if e.get("type") == "stage" and e.get("fraction") is not None]
    assert fracs, events
    assert all(0.0 <= f <= 1.0 for f in fracs), fracs


class _ProgressHarness:
    """用百分比调用 progress_callback（模拟 chem 领域层的既有约定）。"""

    def run(self, *, emit, should_cancel, progress_callback, log):  # noqa: ARG002
        for pct in (0.0, 25.0, 50.0, 100.0):
            progress_callback(pct, f"步 {pct:.0f}%")
        return {"ok": True}


def test_animation_progress_end_to_end_within_unit_interval(monkeypatch) -> None:
    """端到端：动画任务结束后 JobManager 的 progress 必须落在 [0, 1]。"""
    import chem.reaction_animation as ra
    from api.dispatcher import WebDispatcher
    from api.jobs import JobManager
    from services.reaction_service import ReactionService

    def fake_anim(r, p, out, *, progress_callback=None, **kw):
        if progress_callback:
            for pct in (0, 25, 50, 75, 100):
                progress_callback(pct, f"步 {pct}")
        return {"success": True, "output": str(out), "n_frames": 3}

    monkeypatch.setattr(ra, "generate_reaction_animation", fake_anim)

    mgr = JobManager()
    svc = ReactionService(scheduler=WebDispatcher(mgr))
    svc.start_animation(
        reactants=["a.xyz"],
        products=["b.xyz"],
        out="/tmp/unit_interval_anim.gif",
        traj="",
        mode="bounce",
        fmt="gif",
        resolution="hd",
        traj_fmt="xyz",
        spacing=5.0,
        steps=5,
        smooth=True,
        ffmpeg="ffmpeg",
        fps=15,
        scheduler_id="unit-interval",
        pool="io",
    )
    st = None
    for _ in range(300):
        st = mgr.get_status("unit-interval")
        if st and st["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.01)
    assert st["status"] == "done"
    assert 0.0 <= st["progress"] <= 1.0, st["progress"]


# ---------------------------------------------------------------- 上传根目录稳定性
def test_upload_root_inside_system_tempdir() -> None:
    import tempfile

    root = uploads.upload_root().resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    assert tmp in root.parents
    assert root.name.startswith("mm_upload_")


def test_upload_file_id_not_reused_across_same_name() -> None:
    a = uploads.save_upload("same.xyz", b"one")
    b = uploads.save_upload("same.xyz", b"two")
    assert a != b
    time.sleep(0)  # 保持导入使用（time 在文件顶部的 ws 用例中也用到）
    assert uploads.resolve_file_id(a).read_bytes() == b"one"
    assert uploads.resolve_file_id(b).read_bytes() == b"two"
