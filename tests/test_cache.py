#!/usr/bin/env python3
"""utils/cache.py —— LRU / TTL 缓存与文件缓存键。

这一层被 chem 的描述符缓存、分子读取缓存、反应动画逐帧缓存共用，
淘汰语义错了会导致「算过的结果被重复计算」或「内存无限增长」。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from utils.cache import LRUCache, TimedLRUCache, make_file_cache_key


# ---------------------------------------------------------------- LRUCache
def test_lru_basic_get_put() -> None:
    c: LRUCache = LRUCache(maxsize=2)
    c.put("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None
    assert c.get("missing", "dft") == "dft"


def test_lru_evicts_least_recently_used() -> None:
    c: LRUCache = LRUCache(maxsize=2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")  # a 变为最近使用
    c.put("c", 3)  # 应淘汰 b 而不是 a
    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3


def test_lru_respects_maxsize() -> None:
    c: LRUCache = LRUCache(maxsize=3)
    for i in range(10):
        c.put(i, i)
    assert len(c) == 3
    assert c.stats()["size"] == 3
    assert c.stats()["maxsize"] == 3


def test_lru_unlimited_maxsize() -> None:
    c: LRUCache = LRUCache(maxsize=None)
    for i in range(50):
        c.put(i, i)
    assert len(c) == 50


def test_lru_rejects_non_positive_maxsize() -> None:
    with pytest.raises(ValueError, match="maxsize"):
        LRUCache(maxsize=0)
    with pytest.raises(ValueError, match="maxsize"):
        LRUCache(maxsize=-1)


def test_lru_on_evict_callback() -> None:
    evicted: list[tuple[object, object]] = []
    c: LRUCache = LRUCache(maxsize=1, on_evict=lambda k, v: evicted.append((k, v)))
    c.put("a", 1)
    c.put("b", 2)
    assert evicted == [("a", 1)]


def test_lru_on_evict_error_is_swallowed() -> None:
    """回调抛错不能中断缓存主流程。"""

    def boom(_k: object, _v: object) -> None:
        raise RuntimeError("boom")

    c: LRUCache = LRUCache(maxsize=1, on_evict=boom)
    c.put("a", 1)
    c.put("b", 2)  # 不应抛出
    assert c.get("b") == 2


def test_lru_clear_returns_count() -> None:
    c: LRUCache = LRUCache(maxsize=8)
    for i in range(5):
        c.put(i, i)
    assert c.clear() == 5
    assert len(c) == 0


def test_lru_pop_and_peek() -> None:
    c: LRUCache = LRUCache(maxsize=4)
    c.put("a", 1)
    assert c.peek("a") == 1
    assert c.pop("a") == 1
    assert c.get("a") is None
    assert c.pop("a", "dft") == "dft"


def test_lru_contains_and_keys() -> None:
    c: LRUCache = LRUCache(maxsize=4)
    c.put("a", 1)
    c.put("b", 2)
    assert "a" in c
    assert "z" not in c
    assert sorted(c.keys()) == ["a", "b"]


# ---------------------------------------------------------------- TimedLRUCache
def test_timed_lru_expires_after_ttl() -> None:
    c: TimedLRUCache = TimedLRUCache(maxsize=8, ttl=0.05)
    c.put("a", 1)
    assert c.get("a") == 1
    time.sleep(0.08)
    assert c.get("a") is None


def test_timed_lru_per_entry_ttl() -> None:
    c: TimedLRUCache = TimedLRUCache(maxsize=8, ttl=60.0)
    c.put("short", 1, ttl=0.05)
    c.put("long", 2)
    time.sleep(0.08)
    assert c.get("short") is None
    assert c.get("long") == 2


def test_timed_lru_sweep_removes_expired() -> None:
    c: TimedLRUCache = TimedLRUCache(maxsize=8, ttl=0.05)
    c.put("a", 1)
    c.put("b", 2)
    time.sleep(0.08)
    assert c.sweep() == 2
    assert len(c) == 0


def test_timed_lru_rejects_bad_params() -> None:
    with pytest.raises(ValueError, match="maxsize"):
        TimedLRUCache(maxsize=0)
    with pytest.raises(ValueError, match="ttl"):
        TimedLRUCache(ttl=0)


def test_timed_lru_contains_respects_expiry() -> None:
    c: TimedLRUCache = TimedLRUCache(maxsize=4, ttl=0.05)
    c.put("a", 1)
    assert "a" in c
    time.sleep(0.08)
    assert "a" not in c


# ---------------------------------------------------------------- make_file_cache_key
def test_file_cache_key_changes_with_content(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    key1 = make_file_cache_key(str(p))
    assert key1 is not None

    # 内容变长 → 至少 size 维度会变；即便 size 恰好相同，hash 维度也应兜住
    p.write_text("hello world", encoding="utf-8")
    key2 = make_file_cache_key(str(p))
    assert key2 is not None
    assert key1 != key2


def test_file_cache_key_stable_for_unchanged_file(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("stable", encoding="utf-8")
    assert make_file_cache_key(str(p)) == make_file_cache_key(str(p))


def test_file_cache_key_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert make_file_cache_key(str(tmp_path / "nope.txt")) is None


def test_file_cache_key_shape(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    key = make_file_cache_key(str(p))
    assert key is not None
    path, mtime_ns, size, content_hash = key
    assert path == str(p.resolve())
    assert isinstance(mtime_ns, int)
    assert size == 1
    assert content_hash is not None  # 小文件应带内容哈希


def test_file_cache_key_skips_hash_for_large_files(tmp_path: Path) -> None:
    """超过 max_hash_bytes 时跳过哈希（避免读巨文件拖慢缓存）。"""
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 4096)
    key = make_file_cache_key(str(p), max_hash_bytes=16)
    assert key is not None
    assert key[3] is None
