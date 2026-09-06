#!/usr/bin/env python3
"""utils/net —— 统一网络层测试。

策略：本地 http.server 做真实 HTTP（含超限/404/坏 JSON/5xx 重试），
monkeypatch 探测缓存模拟 requests 缺失降级。
"""

from __future__ import annotations

import http.server
import threading

import pytest

import utils.net as net


# ---------------------------------------------------------------- 静态契约
def test_build_headers_defaults_and_overrides() -> None:
    h = net.build_headers()
    assert "MolManager/" in h["User-Agent"]
    assert h["Cache-Control"] == "no-cache"
    h2 = net.build_headers({"Accept": "application/vnd.github+json", "X-Extra": 1, "Bad": None})
    assert h2["Accept"] == "application/vnd.github+json"  # extra 覆盖
    assert h2["X-Extra"] == "1"
    assert "Bad" not in h2  # None 值跳过


def test_normalize_timeout() -> None:
    assert net._normalize_timeout(None) == (net.CONNECT_TIMEOUT, net.READ_TIMEOUT)
    assert net._normalize_timeout((1.5, 9)) == (1.5, 9.0)
    assert net._normalize_timeout(7.0)[1] == 7.0  # 单值 → 读取超时
    assert net._normalize_timeout(7.0)[0] <= net.CONNECT_TIMEOUT
    assert net._normalize_timeout(-1) == (net.CONNECT_TIMEOUT, net.READ_TIMEOUT)
    assert net._normalize_timeout("garbage") == (net.CONNECT_TIMEOUT, net.READ_TIMEOUT)


# ---------------------------------------------------------------- 降级路径
@pytest.fixture()
def no_requests(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(net, "_probe_done", True)
    monkeypatch.setattr(net, "_requests_mod", None)
    monkeypatch.setattr(net, "_probe_error", "simulated: no requests")
    yield


def test_unavailable_degrades_silently(no_requests) -> None:
    assert net.is_available() is False
    assert "simulated" in net.unavailable_reason()
    assert net.get_text("https://example.com") is None  # 不抛
    assert net.get_json("https://example.com") is None  # 不抛
    with pytest.raises(net.RequestsUnavailable):
        net.require_requests()


def test_bad_urls_never_raise() -> None:
    assert net.get_text("") is None
    assert net.get_text("   ") is None
    assert net.get_text("ftp://example.com/file") is None
    assert net.get_text(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------- 真实 HTTP
@pytest.fixture()
def server():
    calls = {"flaky": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/json"):
                self._send(200, b'{"ok": true, "n": 1}', "application/json; charset=utf-8")
            elif self.path.startswith("/text"):
                self._send(200, "hello 世界".encode("utf-8"), "text/plain; charset=utf-8")
            elif self.path.startswith("/empty"):
                self._send(200, b"")
            elif self.path.startswith("/big"):
                self._send(200, b"x" * (net.MAX_RESPONSE_BYTES + 100))
            elif self.path.startswith("/broken"):
                self._send(200, b"{oops not json")
            elif self.path.startswith("/flaky"):
                calls["flaky"] += 1
                if calls["flaky"] < 2:
                    self._send(503, b"try again")
                else:
                    self._send(200, b"recovered")
            else:
                self._send(404, b"nope")

        def log_message(self, *args) -> None:
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _bypass_proxy(monkeypatch: pytest.MonkeyPatch):
    # trust_env=True 会读系统代理；本地回环必须绕过
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    yield


def test_get_text_and_json_success(server: str) -> None:
    assert net.get_text(f"{server}/text") == "hello 世界"
    j = net.get_json(f"{server}/json")
    assert j == {"ok": True, "n": 1}


def test_get_json_broken_returns_none(server: str) -> None:
    assert net.get_json(f"{server}/broken") is None


def test_404_returns_none(server: str) -> None:
    assert net.get_text(f"{server}/missing") is None
    assert net.get_json(f"{server}/missing") is None


def test_empty_body_returns_empty_string(server: str) -> None:
    assert net.get_text(f"{server}/empty") == ""


def test_oversize_body_rejected(server: str) -> None:
    assert net.get_text(f"{server}/big") is None
    # 自定义上限
    assert net.get_text(f"{server}/text", max_bytes=4) is None


def test_retry_on_5xx(server: str) -> None:
    assert net.get_text(f"{server}/flaky", retries=1) == "recovered"


def test_no_retry_when_retries_zero(server: str) -> None:
    assert net.get_text(f"{server}/flaky", retries=0) is None
