#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分片并发工具 —— 把「对一批独立条目逐一执行同一函数」安全地并行化。

典型用途：批量计算描述符（每个分子文件相互独立，但底层 OpenBabel 的线程安全性
在本机/CI 环境难以保证）。因此本模块默认 ``max_workers=1``（即完全顺序执行），
与改造前行为逐字节一致；仅当用户显式开启（config ``descriptor_workers`` > 1 或
环境变量 ``MM_DESCRIPTOR_WORKERS`` > 1）才真正分片并行。

无论串行还是并行，本模块都保证：
- 结果按 ``items`` 的原始顺序返回（调用方无需关心乱序）。
- 进度回调 ``on_progress(done, total)`` 线程安全、次数准确。
- 通过 ``is_cancelled`` 支持中途取消（取消后不再派发新任务，已派发的会跑完）。
- 单个条目抛错不会中断整体，错误被收集到返回结果中（``_exc`` 字段）。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# 单函数最长运行时间无统一上限；这里仅约束线程池生命周期
_DEFAULT_MAX_WORKERS = 1


def _resolve_workers(requested: int | None) -> int:
    """把用户请求并发度收敛到合法范围 [1, 32]。"""
    if requested is None:
        return _DEFAULT_MAX_WORKERS
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_WORKERS
    if n < 1:
        return 1
    if n > 32:
        return 32
    return n


def run_sharded(
    items: Sequence[Any],
    worker_fn: Callable[[Any], Any],
    max_workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list:
    """对 ``items`` 中的每个元素调用 ``worker_fn(item)``，返回与 ``items`` 等长的
    结果列表（保持原始顺序）。

    参数
    ----
    items: 待处理的条目序列（可为任意可哈希/不可哈希对象，仅作入参）。
    worker_fn: 接收单个条目、返回其结果的函数。抛错会被捕获并存入结果的 ``_exc``。
    max_workers:
        并发线程数。<=1 或 None 时退化为顺序执行（与改造前完全一致）。
    on_progress: ``(done, total)`` 进度回调，始终在主调用线程之外安全调用。
    is_cancelled: 返回 True 时停止派发新任务（已派发的仍会执行完毕）。

    返回
    ----
    list：第 i 项为 ``worker_fn(items[i])`` 的返回值；若该项抛错则为
    ``{"_exc": exception_instance, "_item": items[i]}``。
    """
    total = len(items)
    if total == 0:
        return []

    workers = _resolve_workers(max_workers)
    results: list = [None] * total
    done = 0
    done_lock = threading.Lock()

    def _report():
        nonlocal done
        with done_lock:
            done += 1
            d = done
        if on_progress is not None:
            on_progress(d, total)

    def _safe_worker(idx: int, item: Any):
        try:
            results[idx] = worker_fn(item)
        except Exception as exc:  # 单条失败不影响整体
            results[idx] = {"_exc": exc, "_item": item}
        finally:
            _report()

    if workers <= 1:
        # 顺序执行：与改造前逐条行为完全一致
        for idx, item in enumerate(items):
            if is_cancelled is not None and is_cancelled():
                break
            _safe_worker(idx, item)
        return results

    # 并行执行：用线程池分片派发；ThreadPoolExecutor 保证结果可被后续读取
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for idx, item in enumerate(items):
            if is_cancelled is not None and is_cancelled():
                break
            futures.append(ex.submit(_safe_worker, idx, item))
        # 等待全部完成（已派发的会跑完；不 cancel 以避免半写入状态）
        for f in futures:
            try:
                f.result()
            except Exception:
                # _safe_worker 已捕获业务异常；这里仅防御执行器层面的异常
                pass
    return results
