#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台任务管理器 - 使用队列与主线程事件循环通信

支持两种使用模式：
  1) 单例常驻模式 (view.py)：
        tm = TaskManager(app)
        tm.start()                       # 启动常驻 worker 线程
        tm.submit(func, *args, **kwargs, progress_callback=cb)
        tm.stop()

  2) 临时一次性模式 (controller.py / dialogs.py)：
        tm = TaskManager(app, controller=None)   # 第二个参数向后兼容
        tm.run_async(func, on_done=cb, on_error=cb, progress_callback=cb2)
        # 使用共享的 ThreadPoolExecutor，避免无限开线程；on_done/on_error 自动 after(0) 回主线程
"""
import atexit
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import os
import queue
import threading
import time
from typing import Any

from utils.logger import default_logger as logger


# ===== 审计 P-5（原报告 2.2）：一次性后台任务引用列表的防泄漏参数 =====
# 若某任务因死锁 / PSI4 僵尸进程 / 无限循环而永不完成（done() 恒为 False），
# 旧的「len > 512 才清扫已完成项」逻辑永远无法剔除它，列表会无限增长导致 OOM。
# 这里改用「时间戳 + 硬上限」双重防护：过期（卡死）或超量的 future 直接放弃跟踪引用，
# 仅影响 _has_one_shot_running() 等状态查询；其 add_done_callback 仍会正常派发结果。
_FUTURE_MAX_PENDING = 256
_FUTURE_MAX_AGE_S = 30 * 60  # 30 分钟内仍未完成的 future 视为卡死，放弃跟踪


# ===== 全局线程池：所有 TaskManager 共享，避免多个实例各自开池 =====
# max_workers = min(8, CPU+2)，防止老机器资源耗尽；退出时 atexit 自动 shutdown
_MAX_WORKERS = min(8, (os.cpu_count() or 2) + 2)
_global_executor: "ThreadPoolExecutor | None" = ThreadPoolExecutor(
    max_workers=_MAX_WORKERS, thread_name_prefix="TmPool"
)
_global_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """懒获取全局 executor；若已 shutdown 则重建一个新的。"""
    global _global_executor
    with _global_executor_lock:
        if _global_executor is None or getattr(_global_executor, "_shutdown", False):
            _global_executor = ThreadPoolExecutor(
                max_workers=_MAX_WORKERS, thread_name_prefix="TmPool"
            )
        return _global_executor


def _shutdown_global_executor(wait: bool = True) -> None:
    """程序退出时调用；外部 stop() 时也可以手动触发。"""
    global _global_executor
    with _global_executor_lock:
        ex, _global_executor = _global_executor, None
    if ex is not None:
        try:
            ex.shutdown(wait=wait, cancel_futures=True)  # type: ignore[call-arg]
        except TypeError:
            # L-3 修复：旧版 concurrent.futures (<= Python 3.8) 不支持 cancel_futures
            # 降级时记录 warning，便于排查"老环境下程序退出很慢"这类问题
            try:
                logger.warning(
                    "当前 Python 版本 concurrent.futures 不支持 cancel_futures 参数，"
                    "已降级为不取消未完成任务（退出可能稍慢）。"
                )
            except Exception:
                pass
            try:
                ex.shutdown(wait=wait)
            except Exception:
                pass
        except Exception:
            pass


atexit.register(lambda: _shutdown_global_executor(wait=False))


class TaskManager:
    def __init__(self, app, controller: Any = None):
        """
        :param app: 必须，用于调用 app.after(0, cb) 把回调调度回主线程
        :param controller: 可选，向后兼容（历史代码传了第二个参数，内部不再使用）
        """
        self.app = app
        # 模式1：常驻 worker 池（调用 start/stop 才会用到）
        # 并发度来自 config.queue_concurrency，真正并行执行 submit 队列里的任务。
        self._running = False
        self._task_queue: queue.Queue[tuple] = queue.Queue()
        self._result_queue: queue.Queue[tuple] = queue.Queue()
        self._stop_event = threading.Event()
        # 协作式取消：worker 运行任务期间置位；进度回调检测到后即中止任务
        self._cancel_ev = threading.Event()
        # 当前正在运行的任务数（用于关闭拦截 / 取消按钮显隐；并发下用计数而非布尔）
        self._active_count = 0
        self._active_lock = threading.Lock()
        # —— 并发 worker 池 ——
        self._concurrency = max(1, int((getattr(app, "config_data", {}) or {}).get("queue_concurrency", 2) or 2))
        self._desired_workers = self._concurrency
        self._worker_threads: list[threading.Thread] = []
        self._workers_alive = 0
        self._workers_lock = threading.Lock()
        self._worker_seq = 0
        self._scale_down_pending = 0
        # 任务 id -> job 映射（让结果回传时精准标记对应 job，支持并发）
        self._task_seq = 0
        self._pending_jobs: dict[int, dict] = {}
        self._pending_jobs_lock = threading.Lock()
        # 模式2：run_async（使用全局线程池，这里仅记录 futures 方便 stop 时取消）
        self._one_shot_futures: list[Future] = []
        # 审计 P-5：id(fut) -> 提交时间戳（time.monotonic），用于检测「卡死」的 future
        self._future_submitted_at: dict[int, float] = {}
        self._futures_lock = threading.Lock()
        # 设计落地 Phase 5：统一任务队列记录（供「任务队列」页渲染）。
        # 仅新增属性，不改动既有 submit/run_async 的逻辑与调用方。
        self.jobs: list[dict] = []
        self._active_job: dict | None = None
        self._jobs_lock = threading.Lock()

    # ================================================================
    # 模式1：常驻 worker（app 启动时 start，退出时 stop）
    # ================================================================
    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        try:
            self._concurrency = max(1, int((getattr(self.app, "config_data", {}) or {}).get("queue_concurrency", 2) or 2))
        except Exception:
            self._concurrency = 2
        self._desired_workers = self._concurrency
        # 启动 _desired_workers 个常驻 worker 线程，真正并行消费任务队列
        for _ in range(self._desired_workers):
            self._spawn_worker()
        self._poll_results()

    def stop(self):
        # 先置取消标志：正在跑的协作式任务会在下一次进度上报时自行中止，
        # 尽量让池内任务在关闭前自己收尾，避免残留工作拖到解释器退出阶段。
        try:
            self.request_cancel()
        except Exception:
            pass
        # ———— 再停全局线程池（TmPool 线程），避免 run_async 的线程在退出时被强制终止 ————
        try:
            # wait=False：不阻塞等待池内正在执行的任务（如一个长时间的 PSI4 计算）跑完，
            # 否则关窗口会卡住。request_cancel() 已通知协作式任务中止，
            # 剩余清理交给 atexit 注册的 _shutdown_global_executor 在解释器退出时完成。
            _shutdown_global_executor(wait=False)
        except Exception:
            pass
        self._running = False
        self._stop_event.set()
        # 让所有常驻 worker 尽快退出（stop_event 置位后，_worker 在取空队列或下次循环即结束）
        with self._workers_lock:
            threads = list(self._worker_threads)
        if threads:
            logger.info("等待 %d 个 TaskManager 工作线程退出...", len(threads))
            # 5s 内每 0.1s join 一次，既不阻塞太久又能让 99% 情况正常结束
            for t in threads:
                if t.is_alive():
                    t.join(timeout=5.0)
            alive = [t for t in threads if t.is_alive()]
            if alive:
                logger.warning("%d 个工作线程 5 秒内未退出，继续关闭（守护=False 时解释器仍会等它）", len(alive))
        # 把 run_async 挂到这个实例上的未完成 futures 取消一下（尽力而为）
        with self._futures_lock:
            fs = list(self._one_shot_futures)
            self._one_shot_futures.clear()
            self._future_submitted_at.clear()
        for f in fs:
            try:
                f.cancel()
            except Exception:
                pass

    def submit(self, func, *args, progress_callback=None, job=None, **kwargs):
        # 新任务入队前重置取消标志（上一任务已结束，避免残留的取消状态影响本次）
        self._cancel_ev.clear()
        # 生成任务 id 并把 job 关联进去，结果回传时据此精准标记对应 job（支持并发）
        with self._pending_jobs_lock:
            self._task_seq += 1
            tid = self._task_seq
            if job is not None:
                self._pending_jobs[tid] = job
        self._task_queue.put((tid, func, args, kwargs, progress_callback))

    # ================================================================
    # 协作式取消 / 活动状态查询（供 UI 取消按钮 + 关闭拦截使用）
    # ================================================================
    def clear_cancel(self):
        """清除取消请求（新任务开始前调用）。"""
        try:
            self._cancel_ev.clear()
        except Exception:
            pass

    def request_cancel(self):
        """请求取消当前正在运行的任务。任务会在下一次进度回调时中止。"""
        try:
            self._cancel_ev.set()
        except Exception:
            pass

    def is_cancelled(self) -> bool:
        try:
            return self._cancel_ev.is_set()
        except Exception:
            return False

    def _has_one_shot_running(self) -> bool:
        with self._futures_lock:
            return any((not f.done()) for f in self._one_shot_futures)

    def is_busy(self) -> bool:
        """
        是否有任务正在进行中（任一模式）。
        用于关闭窗口时拦截，避免「杀掉正在写的文件导致产物损坏」。
        """
        with self._active_lock:
            active = self._active_count > 0
        return bool(active) or self._has_one_shot_running()

    # ================================================================
    # 模式1 常驻 worker 池：N 个线程并行消费 _task_queue
    # ================================================================
    def _spawn_worker(self):
        """启动一个常驻 worker 线程（非守护，解释器会等它结束）。"""
        with self._workers_lock:
            self._workers_alive += 1
            self._worker_seq += 1
            seq = self._worker_seq
        t = threading.Thread(target=self._worker, daemon=False, name=f"TmWorker-{seq}")
        with self._workers_lock:
            self._worker_threads.append(t)
        t.start()

    def _should_scale_down(self) -> bool:
        """队列空且存活 worker 多于目标并发度时，允许一个空闲 worker 退出（带预约，避免一次缩太多）。"""
        with self._workers_lock:
            if self._stop_event.is_set():
                return False
            if (self._workers_alive > self._desired_workers
                    and self._scale_down_pending < (self._workers_alive - self._desired_workers)
                    and self._task_queue.empty()):
                self._scale_down_pending += 1
                return True
        return False

    def set_concurrency(self, n):
        """
        运行期调整并发度（供队列页下拉调用）。
        调大：立即补足 worker；调小：空闲 worker 自行退出（绝不杀正在跑的任务）。
        """
        try:
            n = max(1, int(n))
        except Exception:
            return
        self._desired_workers = n
        with self._workers_lock:
            alive = self._workers_alive
        if alive < n:
            for _ in range(n - alive):
                self._spawn_worker()

    def _worker(self):
        try:
            while not self._stop_event.is_set():
                try:
                    item = self._task_queue.get(timeout=0.5)
                except queue.Empty:
                    # 缩容：若存活 worker 多于目标且队列空，本 worker 预约退出
                    if self._should_scale_down():
                        return
                    continue
                tid, func, args, kwargs, progress_callback = item
                try:
                    if progress_callback:
                        kwargs = dict(kwargs)
                        kwargs['_progress_callback'] = progress_callback
                    with self._active_lock:
                        self._active_count += 1
                    try:
                        # 任务函数内部通过 _progress_callback 周期性上报进度；
                        # 若用户请求取消，progress_callback 会抛 InterruptedError，
                        # 这里捕获后把结果标记为 'cancelled'（而非 error）。
                        result = func(*args, **kwargs)
                        self._result_queue.put((tid, 'success', result, None))
                    except InterruptedError:
                        # 协作式取消：用户点了「取消」，任务主动中止
                        self._result_queue.put((tid, 'cancelled', None, None))
                    except Exception as e:
                        logger.exception("常驻任务失败: %s", e)
                        self._result_queue.put((tid, 'error', None, str(e)))
                    finally:
                        with self._active_lock:
                            self._active_count -= 1
                        try:
                            self._task_queue.task_done()
                        except Exception:
                            pass
                except Exception as e:
                    logger.exception("工作线程异常: %s", e)
                    try:
                        self._task_queue.task_done()
                    except Exception:
                        pass
        finally:
            with self._workers_lock:
                self._workers_alive -= 1
                if self._scale_down_pending > 0:
                    self._scale_down_pending -= 1
            # 保证哪怕 while 里抛未知异常，也能留下停止日志，避免 "worker 去哪了" 不好排查
            try:
                logger.info("常驻 TaskManager 工作线程已停止")
            except Exception:
                pass

    def _poll_results(self):
        try:
            while True:
                tid, typ, result, error = self._result_queue.get_nowait()
                job = self._pop_job(tid)
                try:
                    if typ == 'success':
                        self.app.after(0, lambda r=result, j=job: self._safe_dispatch_done(r, j))
                    elif typ == 'cancelled':
                        self.app.after(0, lambda j=job: self._safe_dispatch_cancelled(j))
                    else:
                        self.app.after(0, lambda e=error, j=job: self._safe_dispatch_error(e, j))
                finally:
                    self._result_queue.task_done()
        except queue.Empty:
            pass
        except Exception as e:
            logger.debug("_poll_results 轮询异常: %s", e)
        finally:
            # stop() 后停止递归调度，不要再给已经在 destroy 的 app.after() 塞回调
            if self._running:
                try:
                    # 轮询间隔从 100ms 收紧到 30ms：worker 捕获取消后，UI 最快约 30ms 内
                    # 收到 cancelled 结果，取消「体感延迟」显著下降；该间隔仍远小于单帧开销，
                    # 不会给主线程带来可感知负担。
                    self.app.after(30, self._poll_results)
                except Exception:
                    pass

    def _pop_job(self, tid):
        with self._pending_jobs_lock:
            return self._pending_jobs.pop(tid, None)

    def _safe_dispatch_done(self, result, job=None):
        cb = getattr(self.app, 'on_task_done', None)
        if callable(cb):
            try:
                cb(result, job=job)
            except TypeError:
                # 兼容旧签名 on_task_done(result)
                try:
                    cb(result)
                except Exception as e:
                    logger.exception("on_task_done 异常: %s", e)
            except Exception as e:
                logger.exception("on_task_done 异常: %s", e)

    def _safe_dispatch_error(self, error, job=None):
        cb = getattr(self.app, 'on_task_error', None)
        if callable(cb):
            try:
                cb(error, job=job)
            except TypeError:
                try:
                    cb(error)
                except Exception as e:
                    logger.exception("on_task_error 异常: %s", e)
            except Exception as e:
                logger.exception("on_task_error 异常: %s", e)

    def _safe_dispatch_cancelled(self, job=None):
        cb = getattr(self.app, 'on_task_cancelled', None)
        if callable(cb):
            try:
                cb(job=job)
            except TypeError:
                try:
                    cb()
                except Exception as e:
                    logger.exception("on_task_cancelled 异常: %s", e)
            except Exception as e:
                logger.exception("on_task_cancelled 异常: %s", e)

    # ================================================================
    # 模式2：run_async —— 全局线程池提交（不再每次开线程）
    # ================================================================
    def run_async(
        self,
        func: Callable[..., Any],
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> None:
        """
        把 func 提交到共享 ThreadPoolExecutor 中执行；完成后自动 after(0) 回主线程调 on_done/on_error。

        func 可通过 kwargs 接收：
          - _progress_callback(percent: float, message: str)
          - _log(message: str, level: str = 'info')

        取消支持：调用 request_cancel() 后，_progress_callback 会在下一次上报时
        抛出 InterruptedError，任务随之中止并走 on_cancelled 分支。
        注意这是**协作式**取消——完全不上报进度的纯计算任务无法被中断。
        """
        # 新任务开始前清除残留的取消标志；但若本实例还有任务在跑，
        # 说明用户的取消意图可能针对那些任务，此时不清除，避免误放行。
        if not self._has_one_shot_running():
            self.clear_cancel()

        def _progress_wrapper(percent, message):
            # 取消检测放在最前面，且不受 on_progress 是否为 None 影响：
            # 否则未传 on_progress 的任务永远无法被取消。
            if self.is_cancelled():
                raise InterruptedError("任务已被用户取消")
            if on_progress is None:
                return
            try:
                hlp = getattr(self.app, 'helpers', None)
                up = getattr(hlp, 'update_progress', None)
                if callable(up):
                    try:
                        up(float(percent), str(message))
                        return
                    except Exception:
                        pass
                self.app.after(0, lambda p=percent, m=message: on_progress(p, m))
            except Exception as e:
                logger.debug("progress wrapper 异常: %s", e)

        def _log_wrapper(message: str, level: str = 'info'):
            hlp = getattr(self.app, 'helpers', None)
            on_log = getattr(hlp, 'on_log', None)
            if callable(on_log):
                try:
                    on_log(str(message), str(level))
                except Exception as e:
                    logger.debug("log wrapper 异常: %s", e)

        def _pool_body() -> Any:
            # 排队期间就被取消的任务，不必再进入函数体
            if self.is_cancelled():
                raise InterruptedError("任务已被用户取消")
            return func(
                _progress_callback=_progress_wrapper,
                _log=_log_wrapper,
            )

        def _dispatch_cancelled() -> None:
            cb = on_cancelled
            if cb is None:
                cb = getattr(getattr(self.app, 'helpers', None), 'on_task_cancelled', None)
            if cb is None:
                cb = getattr(self.app, 'on_task_cancelled', None)
            if callable(cb):
                try:
                    self.app.after(0, cb)
                    return
                except Exception as e:
                    logger.debug("调度 on_cancelled 失败: %s", e)
            # 没有任何取消回调时，至少给用户一条日志，避免「点了取消没反应」
            hlp = getattr(self.app, 'helpers', None)
            on_log = getattr(hlp, 'on_log', None)
            if callable(on_log):
                try:
                    on_log("任务已取消", "warning")
                except Exception:
                    pass

        def _on_future_done(fut: "Future") -> None:
            # ---- 从实例的 futures 引用列表中移除自己，避免无限增长 ----
            try:
                with self._futures_lock:
                    try:
                        self._one_shot_futures.remove(fut)
                    except ValueError:
                        pass
                    self._future_submitted_at.pop(id(fut), None)
            except Exception:
                pass

            # ---- 把结果 / 异常调度回主线程 ----
            exc = fut.exception()
            if exc is None:
                result = fut.result()
                if on_done is not None:
                    try:
                        # 用默认参数绑定 result，避免 on_done 在调度前被其他 future 覆盖
                        self.app.after(0, lambda r=result: on_done(r))
                    except Exception as e:
                        logger.debug("调度 on_done 失败: %s", e)
                return

            # ---- 取消分支：协作式取消不是错误，不应弹错误框/记 exception ----
            if isinstance(exc, InterruptedError):
                logger.info("一次性后台任务已被用户取消")
                _dispatch_cancelled()
                return

            # ---- 异常分支 ----
            logger.exception("一次性后台任务异常: %s", exc)
            err_msg = str(exc)
            if on_error is not None:
                try:
                    # 同样用默认参数绑定 err_msg
                    self.app.after(0, lambda m=err_msg: on_error(m))
                except Exception as e2:
                    logger.debug("调度 on_error 失败: %s", e2)
            elif on_done is None:
                hlp = getattr(self.app, 'helpers', None)
                if hlp is not None:
                    on_log = getattr(hlp, 'on_log', None)
                    if callable(on_log):
                        try:
                            on_log(f"后台任务失败：{err_msg}", "error")
                        except Exception:
                            pass

        executor = _get_executor()
        try:
            fut = executor.submit(_pool_body)
        except RuntimeError:
            # 某些旧环境在 interpreter 清理阶段可能会抛 shutdown 中异常
            logger.warning("线程池已关闭，run_async 退化为同步直接执行")
            try:
                _on_future_done_result: Any = None
                _on_future_done_exc: BaseException | None = None
                class _FakeFuture:
                    def __init__(self):
                        self._r = None
                        self._e: BaseException | None = None
                    def exception(self):
                        return self._e
                    def result(self):
                        if self._e is not None:
                            raise self._e
                        return self._r
                ff = _FakeFuture()
                try:
                    ff._r = _pool_body()
                except BaseException as _be:
                    ff._e = _be
                _on_future_done(ff)
            except Exception:
                pass
            return

        with self._futures_lock:
            self._one_shot_futures.append(fut)
            self._future_submitted_at[id(fut)] = time.monotonic()
            # 审计 P-5：清扫已完成或已卡死（超过 _FUTURE_MAX_AGE_S）的 future，
            # 再对仍超硬上限的未完成任务放弃跟踪（其回调不受影响、仍会触发）。
            now = time.monotonic()
            kept = []
            for f in self._one_shot_futures:
                fid = id(f)
                age = now - self._future_submitted_at.get(fid, now)
                if f.done() or age > _FUTURE_MAX_AGE_S:
                    self._future_submitted_at.pop(fid, None)
                    continue
                kept.append(f)
            if len(kept) > _FUTURE_MAX_PENDING:
                dropped = kept[:-_FUTURE_MAX_PENDING]
                kept = kept[-_FUTURE_MAX_PENDING:]
                for f in dropped:
                    self._future_submitted_at.pop(id(f), None)
                logger.warning(
                    "一次性后台任务挂起数超过上限 %d，放弃跟踪最早的 %d 个（其回调仍会触发）",
                    _FUTURE_MAX_PENDING, len(dropped),
                )
            self._one_shot_futures = kept
        fut.add_done_callback(_on_future_done)
        return fut
