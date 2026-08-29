#!/usr/bin/env python3
"""
统一网络层（T13 / F18 · Phase 1 批次二）
────────────────────────────────────────
架构 §6.3 铁律：**整个项目只有本模块可以 `import requests`**，其余任何模块
（updater / dialogs / view / model …）都必须经过这里，不得自行发起网络请求。

设计要点
  1. **超时硬约束**：连接 3s / 读取 5s（架构 §3.4），不给"卡住 UI"留任何机会；
  2. **单次重试**：只对「连接类瞬时故障 / 5xx」重试一次，不做指数退避
     —— 更新检查是可有可无的锦上添花，宁可放弃也不能拖慢启动；
  3. **UA 标识**：统一带 `MolManager/<version>`，方便服务端识别与限流统计；
  4. **尊重系统代理**：`session.trust_env = True`（requests 默认），读取
     HTTP_PROXY / HTTPS_PROXY / NO_PROXY，公司内网环境无需额外配置；
  5. **优雅降级**：`requests` 未安装时 `is_available()` 返回 False，
     `get_json()/get_text()` 直接返回 None，**不抛异常**；只有显式调用
     `require_requests()` 才会抛可捕获的 `RequestsUnavailable`；
  6. **响应体上限**：默认 1 MB，防止误配置的地址返回巨大响应把内存吃满。

约束
  - 本模块**无 Tk 依赖**，可脱离 GUI 单测；
  - 本模块**不 import chem.psi4**（PSI4 命名陷阱，架构 §6.2）。
"""

from __future__ import annotations

import json as _json
from typing import Any

from utils.logger import default_logger as logger
from utils.version import get_user_agent

# ---------------------------------------------------------------- 常量

#: 连接超时（秒）。架构 §3.4 硬性要求。
CONNECT_TIMEOUT: float = 3.0

#: 读取超时（秒）。架构 §3.4 硬性要求。
READ_TIMEOUT: float = 5.0

#: 失败后的重试次数（不含首次请求）。1 = 总共最多请求 2 次。
DEFAULT_RETRIES: int = 1

#: 单次响应体的最大字节数（1 MB）。超出即判失败，返回 None。
MAX_RESPONSE_BYTES: int = 1024 * 1024

#: 流式读取的分块大小。
_CHUNK_SIZE: int = 8192

#: 视为「可重试」的 HTTP 状态码（服务端瞬时故障）。
_RETRYABLE_STATUS: frozenset[int] = frozenset({500, 502, 503, 504, 408, 429})


# ---------------------------------------------------------------- 异常


class NetError(Exception):
    """网络层基类异常。调用方一律可捕获，不会逃逸到 Tk 事件循环。"""


class RequestsUnavailable(NetError):
    """`requests` 未安装 / 导入失败。仅由 `require_requests()` 主动抛出。"""


# ---------------------------------------------------------------- requests 探测

_requests_mod: Any = None
_probe_done: bool = False
_probe_error: str = ""


def _load_requests() -> Any:
    """
    惰性探测并缓存 `requests` 模块。

    只探测一次：`requests` 首次 import 有 30~80ms 开销，启动阶段能省则省；
    失败也缓存（不会每次检查更新都重试一次失败的 import）。
    """
    global _requests_mod, _probe_done, _probe_error
    if _probe_done:
        return _requests_mod
    _probe_done = True
    try:
        import requests as _r  # noqa: PLC0415 - 唯一允许的 requests 入口

        _requests_mod = _r
        _probe_error = ""
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        _requests_mod = None
        _probe_error = f"{type(exc).__name__}: {exc}"
        logger.debug("requests 不可用，网络功能将静默降级: %s", _probe_error)
    return _requests_mod


def is_available() -> bool:
    """`requests` 是否可用。不可用时所有 GET 都会直接返回 None。"""
    return _load_requests() is not None


def unavailable_reason() -> str:
    """返回 requests 不可用的原因说明（可用时返回空串）。"""
    _load_requests()
    return _probe_error


def require_requests() -> Any:
    """
    返回 `requests` 模块，不可用时抛 `RequestsUnavailable`。

    给「必须联网」的调用方用；`check_update()` 这类"可有可无"的场景
    应该改用 `is_available()` 做前置判断，避免生成无意义的异常对象。
    """
    mod = _load_requests()
    if mod is None:
        raise RequestsUnavailable(
            "未安装 requests，无法发起网络请求（pip install requests）"
            + (f"；原因：{_probe_error}" if _probe_error else "")
        )
    return mod


def reset_probe_cache() -> None:
    """清空探测缓存（仅供单测在 mock requests 后复位使用）。"""
    global _requests_mod, _probe_done, _probe_error
    _requests_mod = None
    _probe_done = False
    _probe_error = ""


# ---------------------------------------------------------------- 请求头


def build_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    构造统一请求头：UA + Accept + 不缓存。

    `extra` 中的同名键会覆盖默认值（例如 GitHub 需要
    `Accept: application/vnd.github+json`）。
    """
    headers: dict[str, str] = {
        "User-Agent": get_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key is None or value is None:
                continue
            try:
                headers[str(key)] = str(value)
            except Exception:
                continue
    return headers


def _normalize_timeout(timeout: Any) -> tuple[float, float]:
    """把各种形态的 timeout 归一成 `(连接超时, 读取超时)` 元组。"""
    if timeout is None:
        return (CONNECT_TIMEOUT, READ_TIMEOUT)
    if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
        try:
            return (float(timeout[0]), float(timeout[1]))
        except (TypeError, ValueError):
            return (CONNECT_TIMEOUT, READ_TIMEOUT)
    try:
        single = float(timeout)
        if single <= 0:
            return (CONNECT_TIMEOUT, READ_TIMEOUT)
        return (min(single, CONNECT_TIMEOUT), single)
    except (TypeError, ValueError):
        return (CONNECT_TIMEOUT, READ_TIMEOUT)


# ---------------------------------------------------------------- GET


def get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: Any = None,
    retries: int = DEFAULT_RETRIES,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> str | None:
    """
    发起 GET 请求并返回响应正文（str）。

    契约：**永不抛异常**。以下情况一律返回 None 并只记 DEBUG 日志：
      - requests 未安装
      - URL 为空 / 非 http(s)
      - 连接失败 / 超时 / DNS 失败 / 代理不可达
      - HTTP 状态码非 2xx（含 403 限流、404 仓库不存在）
      - 响应体超过 `max_bytes`

    参数:
        url:       完整 URL（必须是 http/https）
        headers:   附加请求头（会与 `build_headers()` 合并）
        params:    查询参数
        timeout:   None=用默认 (3s, 5s)；也可传 (connect, read) 或单个秒数
        retries:   额外重试次数，默认 1
        max_bytes: 响应体字节上限，默认 1 MB
    """
    text = _get_raw_text(
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        retries=retries,
        max_bytes=max_bytes,
    )
    return text


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: Any = None,
    retries: int = DEFAULT_RETRIES,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Any | None:
    """
    发起 GET 请求并把响应正文解析为 JSON。

    契约与 `get_text()` 相同：**永不抛异常**；JSON 损坏时同样返回 None。
    """
    text = _get_raw_text(
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        retries=retries,
        max_bytes=max_bytes,
    )
    if not text:
        return None
    try:
        return _json.loads(text)
    except (ValueError, TypeError) as exc:
        logger.debug("网络响应不是合法 JSON（已忽略）: %s", exc)
        return None


def _get_raw_text(
    url: str,
    *,
    headers: dict[str, str] | None,
    params: dict[str, Any] | None,
    timeout: Any,
    retries: int,
    max_bytes: int,
) -> str | None:
    """`get_text` / `get_json` 的公共实现。任何异常都在此吞掉。"""
    if not isinstance(url, str) or not url.strip():
        logger.debug("网络请求被跳过：URL 为空")
        return None
    url = url.strip()
    if not (url.startswith(("http://", "https://"))):
        logger.debug("网络请求被跳过：仅支持 http/https，收到 %r", url[:120])
        return None

    mod = _load_requests()
    if mod is None:
        # requests 缺失是"预期内的降级"，不打 WARNING 免得刷屏
        logger.debug("网络请求被跳过：requests 不可用")
        return None

    try:
        attempts = max(1, int(retries) + 1)
    except (TypeError, ValueError):
        attempts = 2
    attempts = min(attempts, 3)  # 上限保护：更新检查不值得请求 3 次以上

    try:
        limit = int(max_bytes)
    except (TypeError, ValueError):
        limit = MAX_RESPONSE_BYTES
    if limit <= 0:
        limit = MAX_RESPONSE_BYTES

    conn_to, read_to = _normalize_timeout(timeout)
    final_headers = build_headers(headers)

    for attempt in range(attempts):
        session = None
        response = None
        try:
            session = mod.Session()
            # 尊重系统代理（HTTP_PROXY / HTTPS_PROXY / NO_PROXY）。
            # requests 默认即为 True，这里显式写出来是为了防止将来有人改默认值。
            session.trust_env = True
            response = session.get(
                url,
                headers=final_headers,
                params=params,
                timeout=(conn_to, read_to),
                stream=True,
                allow_redirects=True,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status < 200 or status >= 300:
                logger.debug("网络请求返回非 2xx 状态码 %s: %s", status, url)
                if status in _RETRYABLE_STATUS and attempt < attempts - 1:
                    continue
                return None

            # 有 Content-Length 时先做一次快速判断，避免白下载
            declared = getattr(response, "headers", {}) or {}
            try:
                length = int(declared.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length and length > limit:
                logger.debug("网络响应体过大（%d > %d 字节），已放弃: %s", length, limit, url)
                return None

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    logger.debug("网络响应体超过 %d 字节上限，已放弃: %s", limit, url)
                    return None
                chunks.append(chunk)
            raw = b"".join(chunks)
            if not raw:
                return ""
            encoding = getattr(response, "encoding", None) or "utf-8"
            try:
                return raw.decode(encoding, errors="replace")
            except (LookupError, TypeError):
                return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 契约：任何异常都不外溢
            if attempt < attempts - 1:
                logger.debug("网络请求失败（第 %d 次，将重试）: %s", attempt + 1, exc)
                continue
            logger.debug("网络请求最终失败（已静默放弃）: %s", exc)
            return None
        finally:
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass
    return None


__all__ = [
    "CONNECT_TIMEOUT",
    "READ_TIMEOUT",
    "DEFAULT_RETRIES",
    "MAX_RESPONSE_BYTES",
    "NetError",
    "RequestsUnavailable",
    "is_available",
    "unavailable_reason",
    "require_requests",
    "reset_probe_cache",
    "build_headers",
    "get_text",
    "get_json",
]
