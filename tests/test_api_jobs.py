#!/usr/bin/env python3
"""阶段1：PSI4 / 反应动画 over FastAPI + WebSocket 的验收测试。

覆盖：JobManager 编排（完成 / 协作式取消）、WebSocket 进度实时推送与迟到回放、
客户端取消、GET 轮询兜底、503 路径、以及「POST /psi4/compute → WS 收结果」的完整接线
（用 mock 替换 run_reaction，无需真实 PSI4）。
"""

import time

import pytest
from fastapi.testclient import TestClient

from api.jobs import JobManager, jobs
from api.server import create_app

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

pytestmark = pytest.mark.api


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------- JobManager 单测（框架无关）
def test_jobmanager_completes_with_events() -> None:
    mgr = JobManager()

    def fn(*, emit, should_cancel):
        emit({"type": "log", "message": "start"})
        emit({"type": "stage", "message": "half", "fraction": 0.5})
        emit({"type": "log", "message": "end"})
        return {"ok": True, "value": 42}

    state = mgr.submit("jm-done", fn, pool="io")
    for _ in range(200):
        st = mgr.get_status("jm-done")
        if st and st["status"] in ("done", "error"):
            break
        time.sleep(0.01)

    st = mgr.get_status("jm-done")
    assert st["status"] == "done"
    assert st["result"] == {"ok": True, "value": 42}
    assert st["progress"] == 1.0
    # 事件缓冲保留（迟到 WS 回放用）
    assert any(e["type"] == "stage" and e["fraction"] == 0.5 for e in state.buffer)


def test_jobmanager_cooperative_cancel() -> None:
    mgr = JobManager()

    def fn(*, emit, should_cancel):
        for _ in range(200):
            if should_cancel():
                emit({"type": "log", "message": "checked-cancel"})
                return {"interrupted": True}
            time.sleep(0.01)
        return {"done": True}

    mgr.submit("jm-cancel", fn, pool="io")
    time.sleep(0.05)
    mgr.cancel("jm-cancel")
    for _ in range(200):
        st = mgr.get_status("jm-cancel")
        if st and st["status"] in ("cancelled", "done", "error"):
            break
        time.sleep(0.01)

    assert mgr.get_status("jm-cancel")["status"] == "cancelled"


def test_jobmanager_error_captured() -> None:
    mgr = JobManager()

    def fn(*, emit, should_cancel):
        raise RuntimeError("boom")

    mgr.submit("jm-err", fn, pool="io")
    for _ in range(200):
        st = mgr.get_status("jm-err")
        if st and st["status"] in ("done", "error"):
            break
        time.sleep(0.01)

    st = mgr.get_status("jm-err")
    assert st["status"] == "error"
    assert "boom" in (st["error"] or "")


# ---------------------------------------------------------------- WebSocket 集成
def test_ws_streams_progress_and_result(client: TestClient) -> None:
    job_id = "ws-progress"

    def fn(*, emit, should_cancel):
        emit({"type": "log", "message": "a"})
        emit({"type": "stage", "message": "b", "fraction": 0.5})
        emit({"type": "log", "message": "c"})
        return {"out": "x"}

    jobs.submit(job_id, fn, pool="io")
    time.sleep(0.1)  # 让事件先缓冲

    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        received: list[dict] = []
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("type") in ("done", "error", "cancelled"):
                break

    types = [m["type"] for m in received]
    assert "log" in types
    assert "stage" in types
    assert "done" in types
    assert received[-1]["type"] == "done"
    assert received[-1]["result"] == {"out": "x"}


def test_ws_cancel_via_client_message(client: TestClient) -> None:
    job_id = "ws-cancel"

    def fn(*, emit, should_cancel):
        for _ in range(200):
            if should_cancel():
                emit({"type": "log", "message": "stopped"})
                return {"interrupted": True}
            time.sleep(0.01)
        return {"done": True}

    jobs.submit(job_id, fn, pool="io")

    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        ws.send_json({"action": "cancel"})
        received: list[dict] = []
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("type") in ("done", "error", "cancelled"):
                break

    assert received[-1]["type"] == "cancelled"


def test_ws_unknown_job_returns_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/jobs/does-not-exist") as ws:
        msg = ws.receive_json()
    assert msg.get("type") == "error"
    assert "does-not-exist" in (msg.get("error") or "")


# ---------------------------------------------------------------- 轮询兜底
def test_get_job_poll_after_done(client: TestClient) -> None:
    job_id = "poll-done"

    def fn(*, emit, should_cancel):
        emit({"type": "log", "message": "x"})
        return {"ok": 1}

    jobs.submit(job_id, fn, pool="io")
    for _ in range(200):
        st = jobs.get_status(job_id)
        if st and st["status"] == "done":
            break
        time.sleep(0.01)

    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"] == {"ok": 1}


def test_get_job_404(client: TestClient) -> None:
    r = client.get("/jobs/nonexistent-job-id")
    assert r.status_code == 404


# ---------------------------------------------------------------- 503 路径
def test_psi4_compute_503_when_missing(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"psi4": False})
    r = client.post(
        "/psi4/compute",
        json={"custom": {"reactants": ["O=O"], "products": ["[O]"]}},
    )
    assert r.status_code == 503
    assert "PSI4" in r.json()["detail"]


# ---------------------------------------------------------------- 完整接线（mock run_reaction）
def test_psi4_compute_wiring_with_mock_run_reaction(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"psi4": True})

    def fake_run_reaction(payload, *, run_dir, on_log, on_stage, should_cancel):
        on_log("starting")
        on_stage("optimizing", 0.3)
        on_stage("scanning", 0.8)
        assert should_cancel() is False
        return {"success": True, "delta_e_kjmol": 123.4, "method": payload.get("method")}

    monkeypatch.setattr("chem.quantum_reaction.run_reaction", fake_run_reaction)

    r = client.post(
        "/psi4/compute",
        json={
            "custom": {"reactants": ["O=O"], "products": ["[O]"]},
            "method": "b3lyp",
            "basis": "6-31g*",
        },
    )
    assert r.status_code == 200
    body = r.json()
    job_id = body["job_id"]
    assert body["status"] == "queued"
    assert body["ws_url"].endswith(job_id)

    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        received: list[dict] = []
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("type") in ("done", "error", "cancelled"):
                break

    assert received[-1]["type"] == "done"
    assert received[-1]["result"]["delta_e_kjmol"] == 123.4
    types = [m["type"] for m in received]
    assert "log" in types and "stage" in types


# ---------------------------------------------------------------- 模型校验
def test_psi4_request_requires_source(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("api.server.capabilities.detect", lambda *a, **k: {"psi4": True})
    # 既不给 reaction_id 也不给 custom → 422
    r = client.post("/psi4/compute", json={"method": "hf"})
    assert r.status_code == 422
    # 两者都给 → 422
    r2 = client.post(
        "/psi4/compute",
        json={"reaction_id": "X", "custom": {"reactants": ["O=O"], "products": ["[O]"]}},
    )
    assert r2.status_code == 422


# ---------------------------------------------------------------- 阶段2：api → services（WebDispatcher）
def test_webdispatcher_bridges_service_to_jobmanager() -> None:
    """ServiceBase 经 WebDispatcher 派发的任务应由 JobManager 记录并推送事件。"""
    from api.dispatcher import WebDispatcher
    from services.base import ServiceBase

    mgr = JobManager()
    svc = ServiceBase(scheduler=WebDispatcher(mgr))

    def _work(*, emit, should_cancel, progress_callback, log):
        progress_callback(0.5, "half")
        log("hi")
        return {"value": 7}

    svc._run(_work, dispatch_kwargs={"job_id": "svc-job", "pool": "io"})
    for _ in range(200):
        st = mgr.get_status("svc-job")
        if st and st["status"] in ("done", "error"):
            break
        time.sleep(0.01)

    st = mgr.get_status("svc-job")
    assert st["status"] == "done"
    assert st["result"] == {"value": 7}
    assert st["progress"] == 1.0


def test_webdispatcher_cancel_by_job_id() -> None:
    from api.dispatcher import WebDispatcher
    from services.base import ServiceBase

    mgr = JobManager()
    svc = ServiceBase(scheduler=WebDispatcher(mgr))

    def _work(*, emit, should_cancel, progress_callback, log):
        for _ in range(200):
            if should_cancel():
                return {"interrupted": True}
            time.sleep(0.01)
        return {"done": True}

    handle = svc._run(_work, dispatch_kwargs={"job_id": "svc-cancel", "pool": "io"})
    time.sleep(0.05)
    handle.cancel()
    for _ in range(200):
        st = mgr.get_status("svc-cancel")
        if st and st["status"] in ("cancelled", "done", "error"):
            break
        time.sleep(0.01)
    assert mgr.get_status("svc-cancel")["status"] == "cancelled"


def test_reaction_animate_routes_through_service(client: TestClient, monkeypatch, tmp_path) -> None:
    """POST /reaction/animate 单物种分支应经 ReactionService 落到领域函数。

    阶段3 起 reactants/products 是 file_id 或上传根内路径，故此用例改为先把文件
    放进上传根目录（``api.uploads.UPLOAD_ROOT``）再引用。
    """
    import chem.reaction_animation as ra
    from api import uploads

    seen = {}

    def fake_anim(r, p, out, **kw):
        seen["out"] = str(out)
        return {"success": True, "output": str(out), "n_frames": 3}

    # 在上传根内造两个合法文件。
    fid_a = uploads.save_upload("a.xyz", b"2\na b\nH 0 0 0\nH 0 0 0.74\n")
    fid_b = uploads.save_upload("b.xyz", b"2\na b\nH 0 0 0\nH 0 0 0.80\n")

    monkeypatch_orig = ra.generate_reaction_animation
    ra.generate_reaction_animation = fake_anim
    try:
        r = client.post(
            "/reaction/animate",
            json={"reactants": [fid_a], "products": [fid_b], "fmt": "gif"},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        st = None
        for _ in range(200):
            st = jobs.get_status(job_id)
            if st and st["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.01)
        assert st["status"] == "done"
        assert seen["out"].endswith("anim.gif")
    finally:
        ra.generate_reaction_animation = monkeypatch_orig
