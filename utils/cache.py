#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一缓存模块 —— 取代原先散落在各处的多套 ad-hoc 缓存。

提供三类可复用的缓存原语，并对「按文件内容缓存」这一最常见场景给出统一的键构造：

1. LRUCache
   线程安全的 LRU（最近最少使用）缓存，支持容量上限、淘汰回调、跨缓存共享锁。
   取代原先各自手写 ``OrderedDict`` + ``move_to_end`` + ``popitem`` 的重复实现
   （chem/openbabel_utils.py 的 ``_DESC_CACHE`` / ``_MOL_READ_CACHE``、
    chem/psi4/core.py 的 ``_XYZ_READ_CACHE``）。

2. TimedLRUCache
   在 LRU 基础上增加 TTL 过期，用于短生命周期场景
   （如 chem/reaction_animation.py 的逐帧 2D 渲染底图缓存 ``_raw_cache``）。

3. make_file_cache_key(path, max_hash_bytes)
   基于 (解析后路径, mtime_ns, 文件大小, 内容哈希) 构造文件缓存键。
   内容哈希用于抵御「同尺寸/同 mtime 但内容被原地覆盖」导致的陈旧命中；
   仅对不超过 ``max_hash_bytes`` 的小文件计算哈希，大文件跳过以保性能。
   与 chem/openbabel_utils._cache_key 的语义、字段顺序完全一致，统一到此一处。

设计原则：
- 所有公开方法均线程安全（内部 RLock）。
- 淘汰 / 清空均为原子操作，保证字典不会被并发读写损坏。
- 容量上限可经 ``.maxsize`` 读取；``clear()`` 返回淘汰条目数便于统计。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LRUCache:
    """线程安全的 LRU 缓存。

    参数
    ----
    maxsize:
        最大条目数；超过时淘汰最久未使用的条目。传 ``None`` 表示不限制
        （不推荐用于可能无限增长的场景）。
    on_evict:
        淘汰（含 ``clear()`` / 主动 ``pop()``）条目时的回调 ``(key, value)``，
        可用于释放外部资源。回调内抛错会被吞掉，不影响主流程。
    lock:
        可传入外部 RLock 让多个缓存共享同一把锁（需要跨缓存原子操作时）。
        默认每个实例独立持有一把 RLock。
    """

    def __init__(
        self,
        maxsize: int | None = 128,
        on_evict: Callable[[Any, Any], None] | None = None,
        lock: threading.RLock | None = None,
    ) -> None:
        if maxsize is not None and maxsize <= 0:
            raise ValueError("maxsize 必须为正整或 None")
        self._maxsize = maxsize
        self._on_evict = on_evict
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._lock = lock or threading.RLock()

    @property
    def maxsize(self) -> int | None:
        return self._maxsize

    def get(self, key: Any, default: Any = None) -> Any:
        """命中则返回值并标记为最近使用；未命中返回 ``default``。"""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return default

    def put(self, key: Any, value: Any) -> None:
        """写入/更新条目，标记为最近使用；超容量时淘汰最久未使用。"""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        # 调用方已持有 self._lock
        while self._maxsize is not None and len(self._data) > self._maxsize:
            k, v = self._data.popitem(last=False)
            self._invoke_evict(k, v)

    def _invoke_evict(self, key: Any, value: Any) -> None:
        if self._on_evict is None:
            return
        try:
            self._on_evict(key, value)
        except Exception:  # 回调不应中断主流程
            pass

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def pop(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                v = self._data.pop(key)
                self._invoke_evict(key, v)
                return v
            return default

    def peek(self, key: Any, default: Any = None) -> Any:
        """返回值但不更新 LRU 顺序（只读探查用）。"""
        with self._lock:
            return self._data.get(key, default)

    def clear(self) -> int:
        """清空全部条目，返回被清空的条目数。"""
        evicted = 0
        with self._lock:
            if self._on_evict is not None:
                for k, v in self._data.items():
                    self._invoke_evict(k, v)
            evicted = len(self._data)
            self._data.clear()
        return evicted

    def keys(self) -> list:
        with self._lock:
            return list(self._data.keys())

    def items(self) -> list:
        with self._lock:
            return list(self._data.items())

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._data), "maxsize": self._maxsize}


class TimedLRUCache:
    """带 TTL 的 LRU 缓存。

    过期条目在访问或写入时惰性清理；也可调用 ``sweep()`` 主动回收。
    语义与 :class:`LRUCache` 一致，仅是每个条目额外带一个过期时间戳。
    """

    def __init__(
        self,
        maxsize: int | None = 256,
        ttl: float = 300.0,
        on_evict: Callable[[Any, Any], None] | None = None,
        lock: threading.RLock | None = None,
    ) -> None:
        if maxsize is not None and maxsize <= 0:
            raise ValueError("maxsize 必须为正整或 None")
        if ttl <= 0:
            raise ValueError("ttl 必须为正")
        self._maxsize = maxsize
        self._ttl = float(ttl)
        self._on_evict = on_evict
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = lock or threading.RLock()

    @property
    def maxsize(self) -> int | None:
        return self._maxsize

    @property
    def ttl(self) -> float:
        return self._ttl

    def _is_expired(self, expire_at: float) -> bool:
        return time.monotonic() > expire_at

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                expire_at, value = self._data[key]
                if self._is_expired(expire_at):
                    self._data.pop(key)
                    self._invoke_evict(key, value)
                    return default
                self._data.move_to_end(key)
                return value
            return default

    def put(self, key: Any, value: Any, ttl: float | None = None) -> None:
        ttl = self._ttl if ttl is None else float(ttl)
        expire_at = time.monotonic() + ttl
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = (expire_at, value)
            else:
                self._data[key] = (expire_at, value)
                self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while self._maxsize is not None and len(self._data) > self._maxsize:
            k, (ea, v) = self._data.popitem(last=False)
            self._invoke_evict(k, v)

    def _invoke_evict(self, key: Any, value: Any) -> None:
        if self._on_evict is None:
            return
        try:
            self._on_evict(key, value)
        except Exception:
            pass

    def sweep(self) -> int:
        """主动回收所有过期条目，返回回收数量。"""
        removed = 0
        with self._lock:
            now = time.monotonic()
            expired = [k for k, (ea, _v) in self._data.items() if now > ea]
            for k in expired:
                _ea, v = self._data.pop(k)
                self._invoke_evict(k, v)
                removed += 1
        return removed

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            expire_at, _ = self._data[key]
            if self._is_expired(expire_at):
                self._data.pop(key)
                return False
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> int:
        """清空全部条目，返回被清空的条目数。"""
        evicted = 0
        with self._lock:
            if self._on_evict is not None:
                for k, (_ea, v) in self._data.items():
                    self._invoke_evict(k, v)
            evicted = len(self._data)
            self._data.clear()
        return evicted

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._data), "maxsize": self._maxsize, "ttl": self._ttl}


def make_file_cache_key(
    path_str: str,
    max_hash_bytes: int = 2 * 1024 * 1024,
) -> tuple[str, int, int, str | None] | None:
    """为「按文件内容缓存」构造统一的缓存键。

    返回 ``(解析后绝对路径, mtime_ns, 文件大小, 内容哈希或 None)``。

    - 内容哈希仅对不超过 ``max_hash_bytes`` 的小文件计算（md5），大文件跳过以保性能；
      该维度可抵御「同尺寸/同 mtime 但内容被原地覆盖」导致的陈旧命中。
    - 任何 OSError（文件不存在 / 无权限等）返回 ``None``，调用方应视为「不缓存」。

    与 chem/openbabel_utils._cache_key 的语义、字段顺序完全一致，
    统一到本模块后，所有文件缓存键的构造规则只有这一处。
    """
    try:
        st = os.stat(path_str)
        path_resolved = os.fspath(Path(path_str).resolve())
        mtime_ns = int(st.st_mtime_ns)
        size = int(st.st_size)
        content_hash: str | None = None
        if 0 <= size <= max_hash_bytes:
            try:
                h = hashlib.md5()
                with open(path_str, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                content_hash = h.hexdigest()
            except OSError:
                content_hash = None
        return (path_resolved, mtime_ns, size, content_hash)
    except OSError:
        return None
