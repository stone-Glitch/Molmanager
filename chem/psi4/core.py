#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 核心模块 - run_psi4_task, check_psi4_installed, 基础辅助函数
"""
import os
import re
import json
import csv
import shutil
import subprocess
import tempfile
import threading
import logging  # ← 添加这一行！
import time
import sys
import signal
import atexit
from pathlib import Path
from typing import Dict, Optional, Callable, Any, List, Tuple

from utils.logger import default_logger as logger, performance_timer
from utils.constants import PSI4_PRESETS
from utils.path_utils import secure_output_path, default_base_dir_from_input, win_longpath
from utils.cache import LRUCache, make_file_cache_key
import chem.openbabel_utils as ob_utils

# ---------- NumPy 兼容性补丁 ----------
try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _np = None
    _HAS_NUMPY = False

def _apply_numpy_cumproduct_compat_patch() -> None:
    if not _HAS_NUMPY:
        return
    if hasattr(_np, "cumproduct"):
        return
    from utils.logger import default_logger as _log
    try:
        _np.cumproduct = _np.cumprod
        _log.warning(
            "numpy 2.x 已移除 cumproduct：已临时补上 cumproduct=cumprod 别名，"
            "以兼容仍调用 numpy.cumproduct 的旧依赖（如旧版 pint/qcelemental）。属正常防御性补丁，无需处理。"
        )
    except Exception as _e:
        try:
            _log.debug("应用 numpy cumproduct 兼容性补丁时发生非致命错误: %s", _e)
        except Exception:
            import sys as _sys
            print(f"[compat] numpy cumproduct 补丁非致命错误: {_e}", file=_sys.stderr)

# ---------- PSI4 库的延迟导入 ----------
# 这里一并修复两个问题：
#   1) 原先写的是 `import chem.psi4 as psi4` —— 那是**本项目自己的路由包**（即本文件所在的包），
#      不是 conda 安装的量化库。它没有 geometry / set_memory / energy 等 API，
#      导致所有计算任务在 psi4.geometry 处失败（"has no attribute 'geometry'"），
#      而环境自检却因「导入成功」误报 PSI4 可用。真实库是顶层的 `import psi4`。
#   2) 真实 psi4 导入约需 10 秒。若放在模块层同步导入，
#      chem.reaction_animation → chem.psi4_compute → 本模块 这条链会让应用**启动即冻结 10 秒**。
#      故改为首次实际使用时才加载。numpy 兼容补丁仍在模块层提前打好（开销可忽略），
#      以免其它模块中函数级的 `import psi4` 抢先执行时踩到 pint 崩溃。
_apply_numpy_cumproduct_compat_patch()

_psi4_mod: Any = None
_psi4_load_failed = False
_psi4_load_lock = threading.Lock()


def _load_psi4() -> Any:
    """真正导入 psi4 库；失败只告警一次并返回 None。线程安全 + 结果缓存。"""
    global _psi4_mod, _psi4_load_failed
    if _psi4_mod is not None or _psi4_load_failed:
        return _psi4_mod
    with _psi4_load_lock:
        if _psi4_mod is not None or _psi4_load_failed:
            return _psi4_mod
        try:
            import psi4 as _real
        except Exception as _first_err:
            logger.warning(
                "PSI4 首次导入失败（可能是 numpy/pint 不兼容），应用兼容补丁后重试: %s", _first_err)
            _apply_numpy_cumproduct_compat_patch()
            try:
                import psi4 as _real  # type: ignore[no-redef]
            except Exception as _second_err:
                logger.warning("PSI4 第二次导入仍失败，将标记为不可用: %s", _second_err)
                _psi4_load_failed = True
                return None
        _psi4_mod = _real
        return _psi4_mod


def psi4_is_available() -> bool:
    """PSI4 库是否可真正加载（首次调用会触发约 10 秒的导入）。"""
    return _load_psi4() is not None


class _Psi4Lazy:
    """
    `psi4.xxx` 访问代理：保持既有 40+ 处调用点原样不动，同时把导入开销推迟到首次使用。
    未安装时抛 AttributeError，与「模块无此属性」语义一致，
    因此 `getattr(psi4, 'energy', None)` 这类带默认值的探测仍能正确返回 None。
    """
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        mod = _load_psi4()
        if mod is None:
            raise AttributeError(f"PSI4 未安装或导入失败，无法访问 psi4.{name}")
        return getattr(mod, name)

    def __bool__(self) -> bool:
        return _load_psi4() is not None


psi4 = _Psi4Lazy()

# ---------- 内存参数归一化 ----------
_MEMORY_RE = re.compile(
    r"^\s*([0-9]*\.?[0-9]+)\s*"
    r"(k|ki|m|mi|g|gi|t|ti)?"
    r"(b|bytes?)?\s*$",
    re.IGNORECASE,
)
_DEFAULT_MEMORY = "4 GB"


def normalize_psi4_memory(value: Any, default: str = _DEFAULT_MEMORY) -> str:
    """
    把各种形式的内存设置归一化成 PSI4 能接受的 "<数值> <单位>" 字符串。

    背景：psi4.set_memory 不接受裸数字字符串——
        psi4.set_memory("4")  -> ValidationError: Invalid memory specification: '4'.
        psi4.set_memory(4)    -> ValidationError: 请求 3.81e-06 MiB，低于 250 MiB 下限
    而 UI 侧的 memory_var 是 IntVar，`str(memory_var.get())` 得到的正是裸 "4"，
    且 `or "4 GB"` 兜底永远不会触发（非空字符串为真），导致所有计算任务在
    set_memory 处直接崩溃。这里统一补全单位，裸数字一律按 GB 解释。

    >>> normalize_psi4_memory(4)          -> '4 GB'
    >>> normalize_psi4_memory('4')        -> '4 GB'
    >>> normalize_psi4_memory('4 GB')     -> '4 GB'
    >>> normalize_psi4_memory('2000 MB')  -> '2000 MB'
    >>> normalize_psi4_memory('')         -> '4 GB'
    """
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    m = _MEMORY_RE.match(text)
    if not m:
        # 无法识别的写法：不猜测，回退默认值并告警，避免把非法串丢给 PSI4
        logger.warning("无法解析内存设置 %r，回退为 %s", text, default)
        return default
    num, unit, _suffix = m.group(1), m.group(2), m.group(3)
    # 数值合法性校验：PSI4 最低要求约 250 MiB
    try:
        num_val = float(num)
    except ValueError:
        return default
    if num_val <= 0:
        logger.warning("内存设置 %r 非正数，回退为 %s", text, default)
        return default
    if not unit:
        # 裸数字按 GB 解释（与 UI Spinbox 的语义一致）
        return f"{num.rstrip('.') or '0'} GB"
    unit_norm = unit.upper()
    if not unit_norm.endswith("I"):
        unit_norm += "B"          # G  -> GB
    else:
        unit_norm += "B"          # GI -> GIB
    return f"{num} {unit_norm}"


# ---------- 读取缓存（统一到 utils.cache.LRUCache） ----------
# 原实现为普通 dict + RLock + 手动 FIFO 淘汰；现统一为线程安全 LRU。
# 键构造委托 make_file_cache_key（含内容哈希，抵御同尺寸/mtime 的就地覆盖陈旧命中）。
_XYZ_READ_CACHE_MAX = 512
xyz_read_cache: "LRUCache" = LRUCache(maxsize=_XYZ_READ_CACHE_MAX)
# 区分「缓存未命中」与「命中但值为 None（解析失败）」的哨兵
_XYZ_CACHE_MISS = object()


# ---------- 环境检测 ----------
def check_psi4_installed() -> Tuple[bool, str, Dict[str, Any]]:
    """
    增强版 PSI4 安装与功能支持检测
    返回 (可用性, 消息, 详情字典)
    """
    details: Dict[str, Any] = {
        "version": None,
        "has_energy": False,
        "has_optimize": False,
        "has_frequency": False,
        "has_cphf_nmr": False,
        "has_pcm": False,
        "warnings": [],
    }
    wl: list[str] = details["warnings"]

    # 注意：psi4 现在是延迟加载代理，永远不为 None，必须走 psi4_is_available()
    if not psi4_is_available():
        return False, "PSI4 未安装或导入失败", details

    try:
        details["version"] = str(getattr(psi4, "__version__", None) or
                                  getattr(psi4.core, "version", lambda: "unknown")())
    except Exception as _ve:
        logger.debug("PSI4 版本探测失败: %s", _ve)

    for attr, key in (("energy", "has_energy"),
                      ("optimize", "has_optimize"),
                      ("frequency", "has_frequency")):
        details[key] = callable(getattr(psi4, attr, None))

    details["has_cphf_nmr"] = callable(getattr(psi4, "cphf", None))
    if not details["has_cphf_nmr"]:
        wl.append("PSI4 编译时未启用 CPHF 模块，¹H NMR 模拟将自动降级为经验化学位移库")

    try:
        details["has_pcm"] = callable(getattr(psi4.core, "set_local_option", None))
    except Exception:
        details["has_pcm"] = False

    msg_parts = [f"PSI4 已安装（版本={details['version'] or '未知'}）"]
    caps = []
    if details["has_energy"]: caps.append("单点能")
    if details["has_optimize"]: caps.append("几何优化")
    if details["has_frequency"]: caps.append("频率分析")
    if details["has_cphf_nmr"]: caps.append("CPHF NMR")
    if details["has_pcm"]: caps.append("PCM 溶剂")
    if caps: msg_parts.append(f"支持功能：{'/'.join(caps)}")
    if wl: msg_parts.append(f"警告 {len(wl)} 条")

    return True, "，".join(msg_parts), details


def check_psi4_installed_simple() -> bool:
    ok, _, _ = check_psi4_installed()
    return ok


def get_preset_info(preset_name: str) -> Dict:
    return PSI4_PRESETS.get(preset_name, {})


def sanitize_filename(name: str) -> str:
    illegal_chars = r'[\\/:*?"<>|]'
    return re.sub(illegal_chars, '_', name)


# ---------- 子进程运行 ----------
def _run_process_with_timeout(
    args: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> int:
    """安全地运行子进程"""
    if not args:
        raise ValueError("_run_process_with_timeout: args 不能为空")
    arg0 = str(args[0])
    resolved: str | None = None

    def _validate_abs_path(path_str: str) -> str | None:
        try:
            p = Path(path_str)
            if not p.is_absolute():
                return None
            rp = p.resolve(strict=True)
            import tempfile as _tempfile
            unsafe_roots = []
            for _cand in (_tempfile.gettempdir(), os.getcwd()):
                try:
                    unsafe_roots.append(Path(_cand).resolve(strict=False))
                except Exception:
                    continue
            for root in unsafe_roots:
                try:
                    rp.relative_to(root)
                    logger.warning("拒绝执行在可写目录下的可执行文件: %s", rp)
                    return None
                except ValueError:
                    pass
            if not rp.is_file():
                return None
            return str(rp)
        except OSError:
            return None

    validated = _validate_abs_path(arg0)
    if validated is not None:
        resolved = validated
        args = [resolved] + list(args[1:])
    else:
        try:
            if arg0 in ("obabel", "obabel.exe"):
                try:
                    resolved_exe = ob_utils._resolve_obabel_cli()
                    if resolved_exe:
                        validated2 = _validate_abs_path(resolved_exe)
                        if validated2 is not None:
                            resolved = validated2
                            args = [resolved] + list(args[1:])
                except Exception:
                    pass
            if resolved is None:
                w = shutil.which(arg0)
                if w:
                    validated3 = _validate_abs_path(w)
                    if validated3 is not None:
                        resolved = validated3
                        args = [resolved] + list(args[1:])
        except Exception:
            pass
    try:
        cp = subprocess.run(
            list(args),
            cwd=None if cwd is None else str(cwd),
            timeout=float(timeout),
            env=env,
            shell=False,
            capture_output=bool(capture_output),
            check=False,
        )
        return int(cp.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("子进程超时 (%.1fs): %s", timeout, args)
        return 124
    except FileNotFoundError as e:
        logger.error("子进程可执行文件不存在: %s", arg0)
        return 127
    except OSError as e:
        logger.error("子进程启动失败 args=%s: %s", args, e)
        return 126


# ---------- OpenBabel 转换 ----------
def convert_with_obabel(input_file: str, output_file: str) -> bool:
    try:
        res = ob_utils.convert_file(input_file, output_file, os.path.splitext(output_file)[1][1:] or 'xyz')
        success = res.get("success", False)
        output_path = res.get("output_path")
        ok = success and output_path and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if not ok:
            logger.debug("OpenBabel 转换失败 %s → %s", input_file, output_file)
        return ok
    except Exception as e:
        logger.warning("OpenBabel 转换异常 %s → %s: %s", input_file, output_file, e)
        return False


# ---------- 读取 XYZ ----------
def read_xyz_content(file_path: str) -> Optional[str]:
    key = make_file_cache_key(file_path)
    # 查缓存（命中即返回；LRUCache 内部加锁，字典读写原子）
    if key is not None:
        cached = xyz_read_cache.get(key, _XYZ_CACHE_MISS)
        if cached is not _XYZ_CACHE_MISS:
            return cached
    encodings = ('utf-8', 'gbk', 'gb2312', 'latin-1')
    content: str | None = None
    for enc in encodings:
        try:
            with open(win_longpath(file_path), 'r', encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue
        except OSError:
            break
    if content is None:
        try:
            with open(win_longpath(file_path), 'rb') as f:
                raw = f.read()
                content = raw.decode('utf-8', errors='replace')
        except OSError:
            content = None
    # 解析在锁外进行，避免文件 IO 阻塞其他并发读取（GIL 保证 dict 单操作原子，
    # 此处锁只保护「查-算-写」组合的非原子性）
    if content is None:
        result: str | None = None
    else:
        lines = content.splitlines()
        if len(lines) < 2:
            result = None
        else:
            try:
                atom_count = int(lines[0].strip())
            except ValueError:
                atom_count = 0
            if atom_count <= 0:
                result = None
            else:
                coord_lines = []
                _atom_re = re.compile(r'^[A-Za-z][a-z]?$')
                for line in lines[2:]:
                    if not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                        except ValueError:
                            continue
                        atom = parts[0]
                        if _atom_re.match(atom):
                            coord_lines.append((atom, x, y, z))
                if not coord_lines:
                    result = None
                else:
                    n = len(coord_lines)
                    out_lines = [str(n), "Converted by OpenBabel"]
                    out_lines.extend([f"{a:2s}  {x:12.6f}  {y:12.6f}  {z:12.6f}" for (a, x, y, z) in coord_lines])
                    result = "\n".join(out_lines) + "\n"
    # 写回缓存（含 LRU 淘汰，统一由 LRUCache 内部处理）
    if key is not None:
        xyz_read_cache.put(key, result)
    return result


# ---------- 解析 PSI4 输出 ----------
def parse_psi4_output(log_file: str, task_type: str = 'energy') -> Dict:
    result = {"energy": None, "optimized_xyz": None}
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        en_patterns = [
            r'@.*?Final\s+energy\s+([-\d.]+)',
            r'Total energy\s+=\s+([-\d.]+)',
            r'SCF\s+Done:\s+E\s*=\s*([-\d.]+)',
        ]
        for pattern in en_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                result["energy"] = float(matches[-1])
                break
        if task_type == 'optimize':
            coords = []
            in_coords = False
            coord_started = False
            for line in content.splitlines():
                if 'Standard nuclear orientation' in line or 'Current geometry' in line:
                    in_coords = True
                    coord_started = False
                    coords = []
                    continue
                if in_coords and '-----' in line:
                    if coord_started and coords:
                        break
                    coord_started = True
                    continue
                if in_coords and coord_started and re.match(r'\s*\d+\s+', line):
                    parts = line.split()
                    if len(parts) >= 5:
                        atom_num = int(parts[1])
                        element_map = {1: 'H', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 15: 'P', 16: 'S', 17: 'Cl', 35: 'Br', 53: 'I'}
                        atom_symbol = element_map.get(atom_num, f"X{atom_num}")
                        x, y, z = parts[2:5]
                        coords.append(f"{atom_symbol}  {x}  {y}  {z}")
            if coords:
                result["optimized_xyz"] = f"{len(coords)}\nOptimized geometry\n" + "\n".join(coords)
    except Exception as e:
        result["error"] = str(e)
        logger.debug("解析 PSI4 输出失败: %s", e)
    return result


# ---------- run_psi4_task ----------
@performance_timer(name="psi4.run_psi4_task", level=logging.DEBUG, min_ms=50.0)
def run_psi4_task(
    input_file: str,
    task_type: str = 'energy',
    method: str = 'b3lyp',
    basis: str = '6-31g*',
    output_dir: Optional[str] = None,
    preset_name: Optional[str] = None,
    solvent: Optional[str] = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = '4 GB',
    xyz_content: Optional[str] = None,
    base_name: Optional[str] = None,
    **kwargs
) -> Dict:
    """运行单个 PSI4 任务。

    P-03 增强：当 ``xyz_content`` 提供时，跳过一切基于文件路径的校验
    （存在性 / 符号链接 / 非 ASCII 临时目录），直接用内存中的 XYZ 文本构建分子，
    从而让线性扫描等场景免于为每个插值帧落盘临时 XYZ 文件。
    ``base_name`` 用于输出文件前缀（否则从 input_file 派生）。
    两者均向后兼容：``xyz_content=None`` 时行为与改造前逐字节一致。
    """

    if not check_psi4_installed_simple():
        return {"success": False, "error": "PSI4 未安装"}

    # P-03：内存 XYZ 模式——跳过文件存在性检查
    if xyz_content is None and not os.path.exists(input_file):
        return {"success": False, "error": f"文件不存在: {input_file}"}

    progress_callback: Optional[Callable] = kwargs.get('_progress_callback', None)
    extra_options: Dict[str, Any] = kwargs.get('extra_options', None) or {}
    extra_post_hook = kwargs.get('_extra_post_hook', None)

    def report(percent: float, msg: str) -> None:
        if progress_callback:
            progress_callback(percent, msg)
        logger.debug("[PSI4 进度] %3d%% - %s", int(percent), msg)

    # TempDirGuard
    def _rmtree_with_retry(p: str, attempts: int = 3) -> None:
        """清理临时目录，失败指数退避重试；最终仍失败记 warning（而非静默忽略）。"""
        import time as _time
        last_err: Exception | None = None
        for i in range(attempts):
            try:
                shutil.rmtree(p, ignore_errors=False)
                return
            except Exception as _e:
                last_err = _e
                logger.debug("TempDirGuard 清理临时目录失败(第%d次) %s: %s", i + 1, p, _e)
                _time.sleep(0.5 * (2 ** i))  # 0.5s / 1.0s / 2.0s 指数退避
        if os.path.exists(p):
            logger.warning("TempDirGuard 多次尝试仍无法清理临时目录 %s: %s", p, last_err)

    class _TempDirGuard:
        def __init__(self):
            self.path: str | None = None
            self.active: bool = True
            self.extra_paths: list[str] = []

        def acquire(self, prefix: str = "psi4_temp_") -> str:
            if self.path is not None:
                return self.path
            self.path = tempfile.mkdtemp(prefix=prefix)
            return self.path

        def assign(self, existing_path: str | None) -> None:
            self.path = existing_path

        def register_extra(self, p: str) -> None:
            if p and os.path.exists(p) and p not in self.extra_paths:
                self.extra_paths.append(p)

        def release(self) -> None:
            if not self.active:
                return
            self.active = False
            for ep in self.extra_paths:
                try:
                    if os.path.isdir(ep):
                        shutil.rmtree(ep, ignore_errors=True)
                    elif os.path.isfile(ep):
                        os.unlink(ep)
                except Exception as _re:
                    logger.debug("TempDirGuard 清理额外临时路径失败 %s: %s", ep, _re)
            self.extra_paths = []
            p, self.path = self.path, None
            if p:
                _rmtree_with_retry(p)  # 审计 S-3 修复：加重试与告警，避免临时目录静默残留

    _td = _TempDirGuard()

    def _finalize():
        _td.release()
        try:
            psi4.core.clean()
        except Exception as _ce:
            logger.debug("PSI4 core.clean() 失败: %s", _ce)

    try:
        in_memory = xyz_content is not None
        input_path = Path(input_file)
        has_non_ascii = any(ord(c) > 127 for c in str(input_path.resolve()))
        print(f"路径检测: has_non_ascii = {has_non_ascii}, 路径 = {input_file}")

        _base_dir = default_base_dir_from_input(input_file)
        try:
            _raw_orig_dir = output_dir if output_dir is not None else str(input_path.parent)
            _safe_orig = secure_output_path(
                _raw_orig_dir,
                is_dir=True,
                base_dir=_base_dir,
                create_parent=True,
                allow_outside=False,
            )
            original_output_dir = str(_safe_orig)
        except ValueError as _v:
            return {"success": False, "error": f"输出目录非法: {_v}"}
        output_dir = None

        use_temp: bool = False
        temp_dir: str | None = None

        def _switch_to_temp_dir() -> str:
            nonlocal use_temp, temp_dir, output_dir
            if temp_dir is None:
                td = _td.acquire(prefix="psi4_temp_")
                temp_dir = td
            out = str(temp_dir)
            use_temp = True
            output_dir = out
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as m_err:
                raise RuntimeError(f"无法创建 PSI4 临时目录: {output_dir}") from m_err
            print(f"ℹ️  使用 PSI4 临时目录：{output_dir}")
            return output_dir

        if (not in_memory) and has_non_ascii:
            _switch_to_temp_dir()
        else:
            if output_dir is None:
                output_dir = original_output_dir
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as m_err:
                raise RuntimeError(f"无法创建 PSI4 输出目录: {output_dir}") from m_err

        # ---- P-03：内存 XYZ 模式 vs 文件模式 ----
        if xyz_content is not None:
            report(5, "读取分子结构(内存)...")
            try:
                mol = psi4.geometry(xyz_content)
            except Exception as load_err:
                return {"success": False, "error": f"PSI4 读取内存 XYZ 失败: {load_err}"}
        else:
            report(5, "读取分子结构...")
            mol = None

            try:
                real_input_path = Path(input_file).resolve(strict=True)
            except OSError as exc:
                return {"success": False, "error": f"分子文件无法解析为真实路径: {exc}"}
            if not real_input_path.is_file() or real_input_path.is_symlink():
                return {"success": False,
                        "error": f"分子文件必须是真实文件（禁止符号链接）: {input_file}"}

            def _load_from_realpath(full_temp_dir: str | None) -> tuple:
                work = Path(full_temp_dir) if full_temp_dir else real_input_path.parent
                converted_xyz = os.fspath(work / "molecule.xyz")
                if full_temp_dir is None and not real_input_path.suffix.lower() == ".xyz":
                    _td.register_extra(converted_xyz)
                src_is_xyz = real_input_path.suffix.lower() == ".xyz"
                if src_is_xyz:
                    xyz = read_xyz_content(os.fspath(real_input_path))
                else:
                    if not convert_with_obabel(os.fspath(real_input_path), converted_xyz):
                        return None, "OpenBabel 转换失败"
                    xyz = read_xyz_content(converted_xyz)
                if xyz is None:
                    return None, "无法解析 XYZ"
                try:
                    return psi4.geometry(xyz), None
                except Exception as load_err:
                    return None, f"PSI4 读取失败: {load_err}"

            try:
                if use_temp:
                    mol, err = _load_from_realpath(temp_dir)
                    if err:
                        return {"success": False, "error": err}
                else:
                    try:
                        mol, _load_err = _load_from_realpath(None)
                        if mol is None:
                            td = tempfile.mkdtemp(prefix="psi4_temp_")
                            _td.assign(td)
                            temp_dir = td
                            _switch_to_temp_dir()
                            mol, err = _load_from_realpath(temp_dir)
                            if err:
                                return {"success": False, "error": err}
                    except Exception:
                        td = tempfile.mkdtemp(prefix="psi4_temp_")
                        _td.assign(td)
                        temp_dir = td
                        _switch_to_temp_dir()
                        mol, err = _load_from_realpath(temp_dir)
                        if err:
                            return {"success": False, "error": err}
            except Exception as e:
                logger.warning("准备分子失败(加载): %s", e, exc_info=True)
                return {"success": False, "error": f"准备分子失败: {e}"}

        # ---- 两种模式共用的分子后处理 ----
        try:
            if mol is None:
                return {"success": False, "error": "未能构建分子"}
            try:
                mol.set_molecular_charge(charge)
            except AttributeError:
                try:
                    mol.set_charge(charge)
                except Exception as _ch:
                    logger.debug("设置分子电荷失败 q=%d: %s", charge, _ch)
            try:
                mol.set_multiplicity(multiplicity)
            except AttributeError as _ma:
                logger.debug("设置分子多重度失败 mult=%d: %s", multiplicity, _ma)
        except Exception as e:
            logger.warning("准备分子失败: %s", e, exc_info=True)
            return {"success": False, "error": f"准备分子失败: {e}"}

        results: Dict[str, Any] = {
            "success": False,
            "energy": None,
            "optimized_xyz": None,
            "frequencies": None,
            "fchk_file": None,
            "log_file": None,
            "output_files": [],
            "error": None,
        }
        wfn = None
        log_file = None
        output_prefix = None

        try:
            if use_temp:
                base = "molecule"
            elif base_name:
                base = sanitize_filename(base_name)
            else:
                base = sanitize_filename(os.path.splitext(os.path.basename(input_file))[0])

            safe_method = sanitize_filename(method)
            safe_basis = sanitize_filename(basis)
            suffix = f"_{task_type}"
            if preset_name:
                suffix += f"_{sanitize_filename(preset_name)}"
            else:
                suffix += f"_{safe_method}_{safe_basis}"
            if solvent:
                suffix += f"_{sanitize_filename(solvent)}"
            if d3:
                suffix += "_d3"

            output_prefix = os.path.join(output_dir, base + suffix)
            log_file = output_prefix + ".log"
            results["log_file"] = log_file
            logger.info("PSI4 日志文件将保存到: %s", log_file)

            psi4.set_output_file(log_file, append=False)

            # 统一归一化：UI 传进来的可能是裸 "4"，直接给 set_memory 会抛 ValidationError
            psi4.set_memory(normalize_psi4_memory(memory))
            psi4.set_options({
                'basis': basis,
                'scf_type': 'pk',
                'e_convergence': 1e-8,
                'd_convergence': 1e-8,
            })
            if extra_options:
                try:
                    psi4.set_options(dict(extra_options))
                except Exception as _eo_err:
                    logger.warning("应用 extra_options 失败：%s", _eo_err)
            if d3:
                try:
                    psi4.set_options({'dft_dispersion': 'd3'})
                except Exception as _d3_err:
                    logger.warning("D3 色散校正启用失败，回退为不加 D3: %s", _d3_err)

            _pcm_enabled_here = False
            if solvent:
                # 审计 #2 修复：溶剂下 optimize/frequency/thermo 也需启用 PCM，
                # 否则溶剂下的热化学/优化/频率实际仍是气相。
                _pcm_try_tasks = {"energy", "optimize", "frequency", "thermo"}
                if task_type in _pcm_try_tasks:
                    try:
                        psi4.set_options({'pcm': True, 'solvent': solvent})
                        try:
                            psi4.core.set_local_option("PCM", "Solver", "IEFPCM")
                            psi4.core.set_local_option("PCM", "Medium", "UniformDielectric")
                            psi4.core.set_local_option("PCM", "SolverEnzyme", False)
                            psi4.core.set_local_option("PCM", "Cavity", "UFF")
                            psi4.core.set_local_option("PCM", "Scaling", True)
                            psi4.core.set_local_option("PCM", "RadiiSet", "UFF")
                            psi4.core.set_local_option("PCM", "Area", 0.3)
                        except Exception as _pcm_opts_err:
                            logger.debug("PCM 局部选项设置失败: %s", _pcm_opts_err)
                        _pcm_enabled_here = True
                    except Exception as _pcm_err:
                        logger.warning("启用 PCM 隐式溶剂失败: %s", _pcm_err)
                        try:
                            psi4.set_options({'pcm': False, 'solvent': solvent})
                        except Exception:
                            pass
                else:
                    try:
                        psi4.set_options({'solvent': solvent})
                    except Exception as _solv_meta_err:
                        logger.warning("写入溶剂元数据选项失败: %s", _solv_meta_err)

            # ---- 审计 UX2：进度嗅探线程 ----
            # 重定向 PSI4 输出到临时文件，后台线程轮询提取 SCF 迭代/优化步，解决
            # 「长时间计算无中间反馈」导致用户误以为程序卡死的问题。
            # 嗅探线程只通过 progress_callback（写 jsonl 文件）回传，不触碰 tkinter，线程安全。
            _progress_out = os.path.join(
                output_dir or tempfile.gettempdir(),
                f"psi4_progress_{os.getpid()}_{id(results)}.out",
            )
            _progress_stop = threading.Event()
            try:
                psi4.core.set_output_file(_progress_out)
            except Exception:
                _progress_out = None
            def _poll_progress():
                import re as _re
                _last = ("", 0)
                while not _progress_stop.is_set():
                    if _progress_out and os.path.exists(_progress_out):
                        try:
                            with open(_progress_out, "r", errors="replace") as _fh:
                                _txt = _fh.read()
                            _opt = _re.findall(r"[Oo]ptimization Step\s+(\d+)", _txt)
                            _step = int(_opt[-1]) if _opt else 0
                            _scf = _re.findall(r"SCF Iteration\s+(\d+)", _txt)
                            _it = int(_scf[-1]) if _scf else 0
                            if _step and _step != _last[1]:
                                report(min(95, 40 + _step), f"优化第 {_step} 步…")
                                _last = ("opt", _step)
                            elif _it and _it != _last[1]:
                                report(min(90, 30 + _it), f"SCF 第 {_it} 次迭代…")
                                _last = ("scf", _it)
                        except Exception:
                            pass
                    _progress_stop.wait(1.5)
            _progress_thread = threading.Thread(target=_poll_progress, daemon=True)
            _progress_thread.start()
            report(10, "开始计算...")
            _pcm_safe_rollback_done = False

            def _rollback_pcm_if_needed():
                nonlocal _pcm_safe_rollback_done, _pcm_enabled_here
                if _pcm_safe_rollback_done or not _pcm_enabled_here:
                    return False
                _pcm_safe_rollback_done = True
                try:
                    psi4.set_options({'pcm': False})
                except Exception:
                    pass
                logger.warning("PCM 求解失败，已自动回退为气相 energy 重新计算")
                return True

            # 执行任务
            if task_type == 'energy':
                report(30, "计算单点能...")
                try:
                    energy, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
                except Exception as _e1:
                    if _rollback_pcm_if_needed():
                        energy, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
                        results["pcm_rolled_back"] = True
                        results["solvent_rollback_reason"] = str(_e1)[:200]
                    else:
                        raise
                results["energy"] = energy
                results["success"] = True

            elif task_type == 'optimize':
                report(30, "开始几何优化...")
                try:
                    energy, wfn = psi4.optimize(method, molecule=mol, return_wfn=True)
                except Exception as _e1:
                    if _rollback_pcm_if_needed():
                        energy, wfn = psi4.optimize(method, molecule=mol, return_wfn=True)
                        results["pcm_rolled_back"] = True
                        results["solvent_rollback_reason"] = str(_e1)[:200]
                    else:
                        raise
                results["energy"] = energy
                opt_mol = wfn.molecule()
                results["optimized_xyz"] = opt_mol.save_string_xyz()
                results["success"] = True

            elif task_type == 'frequency':
                report(30, "计算频率...")
                try:
                    energy, wfn = psi4.frequency(method, molecule=mol, return_wfn=True)
                except Exception as _e1:
                    if _rollback_pcm_if_needed():
                        energy, wfn = psi4.frequency(method, molecule=mol, return_wfn=True)
                        results["pcm_rolled_back"] = True
                        results["solvent_rollback_reason"] = str(_e1)[:200]
                    else:
                        raise
                results["energy"] = energy
                freqs = psi4.core.variable("frequencies")
                if freqs is not None:
                    results["frequencies"] = freqs.to_array().tolist()
                results["success"] = True

            elif task_type == 'ts':
                report(30, "搜索过渡态...")
                energy, wfn = psi4.optimize('ts', molecule=mol, return_wfn=True)
                results["energy"] = energy
                results["success"] = True

            elif task_type == 'excited':
                report(30, "计算激发态...")
                psi4.set_options({'tdscf_excitations': 5})
                energy, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
                results["energy"] = energy
                results["success"] = True

            elif task_type == 'sapt':
                report(30, "计算 SAPT...")
                psi4.set_options({'sapt_symmetry': 'c1'})
                energy = psi4.sapt_energy(method, molecule=mol)
                results["energy"] = energy
                results["success"] = True
                try:
                    wfn = psi4.core.get_wavefunction()
                except Exception:
                    wfn = None

            elif task_type == 'thermo':
                report(30, "进行几何优化...")
                try:
                    opt_energy, opt_wfn = psi4.optimize(method, molecule=mol, return_wfn=True)
                except Exception as _e1:
                    if _rollback_pcm_if_needed():
                        opt_energy, opt_wfn = psi4.optimize(method, molecule=mol, return_wfn=True)
                        results["pcm_rolled_back"] = True
                        results["solvent_rollback_reason"] = str(_e1)[:200]
                    else:
                        raise
                results["energy"] = opt_energy
                opt_mol = opt_wfn.molecule()
                results["optimized_xyz"] = opt_mol.save_string_xyz()
                report(60, "计算频率（优化后结构）...")
                try:
                    freq_energy, freq_wfn = psi4.frequency(method, molecule=opt_mol, return_wfn=True)
                except Exception as _e2:
                    # 频率步若因 PCM 失败，降级为气相重算（optimize 步已成功则保留）
                    if _rollback_pcm_if_needed():
                        freq_energy, freq_wfn = psi4.frequency(method, molecule=opt_mol, return_wfn=True)
                        results.setdefault("pcm_rolled_back", True)
                    else:
                        raise
                thermo = psi4.core.variable("thermodynamics")
                if thermo is not None:
                    results["thermo"] = thermo.to_array().tolist()
                else:
                    # 科学红线 S-05：频率/热化学未取得热力学量，必须显式标记，
                    # 绝不能静默成功（用户会以为拿到了自由能，实则只有电子能）。
                    results.setdefault("thermo_fallback", []).append("thermo")
                    logger.error(
                        "热化学：thermo 任务未取得 thermodynamics 量（频率/热化学可能失败），"
                        "该点自由能不可靠、仅作占位。"
                    )
                results["success"] = True
                wfn = opt_wfn

            else:
                results["error"] = f"未知任务类型: {task_type}"

            # 高级扩展：用户自定义 post hook
            if results["success"] and extra_post_hook is not None:
                try:
                    _hook_ret = extra_post_hook(wfn, mol, method)
                    if isinstance(_hook_ret, dict):
                        results.setdefault("hook", {}).update(_hook_ret)
                except Exception as _hook_err:
                    logger.warning("extra_post_hook 执行失败：%s", _hook_err)
                    results["hook_error"] = str(_hook_err)

            # P1 波函数属性
            if results["success"] and wfn is not None:
                try:
                    props: dict[str, Any] = {}
                    try:
                        na_list = wfn.nalpha()
                        eps_a = wfn.epsilon_a()
                        if eps_a is not None:
                            eps_a = eps_a.to_array()
                        if eps_a is not None and len(eps_a) > 0:
                            n_a = int(na_list)
                            homo_i = max(0, min(n_a - 1, len(eps_a) - 1))
                            lumo_i = min(homo_i + 1, len(eps_a) - 1)
                            hartree_to_ev = 27.21139664
                            homo_ev = float(eps_a[homo_i]) * hartree_to_ev
                            lumo_ev = float(eps_a[lumo_i]) * hartree_to_ev
                            props["homo_ev"] = homo_ev
                            props["lumo_ev"] = lumo_ev
                            props["gap_ev"] = lumo_ev - homo_ev
                    except Exception as _e_hl:
                        logger.debug("取 HOMO/LUMO 失败: %s", _e_hl)

                    try:
                        psi4.oeprop(wfn, "MULLIKEN_CHARGES", "LOWDIN_CHARGES", "DIPOLE")
                        try:
                            mu_x = float(psi4.core.variable("DIPOLE X"))
                            mu_y = float(psi4.core.variable("DIPOLE Y"))
                            mu_z = float(psi4.core.variable("DIPOLE Z"))
                            mu_tot = (mu_x ** 2 + mu_y ** 2 + mu_z ** 2) ** 0.5
                            props["dipole"] = {"x_D": mu_x, "y_D": mu_y, "z_D": mu_z, "total_D": mu_tot}
                        except Exception as _e_d:
                            logger.debug("取偶极矩失败: %s", _e_d)
                    except Exception as _e_prop:
                        logger.debug("oeprop 属性计算失败: %s", _e_prop)

                    if props:
                        results["properties"] = props
                except Exception as _e_p1:
                    logger.debug("P1 波函数属性提取整体失败: %s", _e_p1)

            # P3 IR 光谱
            ir_png: str | None = None
            ir_csv: str | None = None
            if results["success"] and results.get("frequencies") and output_prefix:
                try:
                    ir_csv = output_prefix + "_ir_spectrum.csv"
                    ir_png = output_prefix + "_ir_spectrum.png"
                    freqs = list(results["frequencies"])
                    intensities: list[float] = []
                    try:
                        ir_arr = psi4.core.variable("IR INTENSITIES")
                        if ir_arr is not None and hasattr(ir_arr, "to_array"):
                            intensities = [float(x) for x in ir_arr.to_array().tolist()]
                    except Exception:
                        intensities = []
                    n = len(freqs)
                    if len(intensities) != n:
                        intensities = [1.0 for _ in freqs]
                    with open(ir_csv, "w", encoding="utf-8", newline="") as _f:
                        _wr = csv.writer(_f)
                        _wr.writerow(["wavenumber_cm-1", "intensity_km/mol"])
                        for fv, iv in zip(freqs, intensities):
                            _wr.writerow([fv, iv])
                    _plot_ir(freqs, intensities, ir_png)
                    results["ir_csv"] = ir_csv
                    results["ir_png"] = ir_png
                    results["output_files"].extend([ir_csv, ir_png])
                except Exception as _e_p3:
                    logger.debug("P3 IR 光谱生成失败: %s", _e_p3)

            # P2 cubeprop
            cube_files: list[str] = []
            if results["success"] and wfn is not None and output_prefix:
                try:
                    cube_out_dir = Path(output_prefix).parent / (Path(output_prefix).name + "_cubes")
                    cube_out_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        old_cwd = Path.cwd()
                        os.chdir(cube_out_dir)
                    except OSError:
                        old_cwd = None
                    try:
                        psi4.set_options({
                            'CUBEPROP_TASKS': ['DENSITY', 'FRONTIER_ORBITALS'],
                            'CUBIC_GRID_SPACING': 0.25,
                        })
                        psi4.cubeprop(wfn)
                    finally:
                        if old_cwd is not None:
                            try:
                                os.chdir(old_cwd)
                            except OSError:
                                pass
                    for p in cube_out_dir.iterdir():
                        if p.suffix.lower() == ".cube":
                            cube_files.append(str(p))
                    results["cube_dir"] = str(cube_out_dir)
                    results["cube_files"] = cube_files
                    results["output_files"].extend(cube_files)
                except Exception as _e_p2:
                    logger.debug("P2 cubeprop 失败: %s", _e_p2)

            # 保存结果
            if results["success"] and output_prefix:
                report(80, "保存结果文件...")
                if wfn is None:
                    try:
                        wfn = psi4.core.get_wavefunction()
                    except Exception:
                        wfn = None

                if wfn is not None:
                    fchk_file = output_prefix + ".fchk"
                    psi4.fchk(wfn, fchk_file)
                    results["fchk_file"] = fchk_file
                    results["output_files"].append(fchk_file)

                if results.get("optimized_xyz"):
                    xyz_file = output_prefix + "_opt.xyz"
                    with open(xyz_file, 'w') as f:
                        f.write(results["optimized_xyz"])
                    results["output_files"].append(xyz_file)

                summary_file = output_prefix + "_summary.json"
                summary_data = {
                    "input_file": input_file,
                    "task_type": task_type,
                    "method": method,
                    "basis": basis,
                    "preset": preset_name,
                    "energy": results["energy"],
                    "success": results["success"],
                }
                with open(summary_file, 'w', encoding='utf-8') as f:
                    json.dump(summary_data, f, indent=2)
                results["output_files"].append(summary_file)

            # 复制结果到原目录
            if use_temp and temp_dir:
                report(95, "复制结果到原目录...")
                os.makedirs(original_output_dir, exist_ok=True)
                for src_path in list(results["output_files"]):
                    if os.path.exists(src_path):
                        dst_path = os.path.join(original_output_dir, os.path.basename(src_path))
                        shutil.copy2(src_path, dst_path)
                        if src_path == results.get("log_file"):
                            results["log_file"] = dst_path
                        elif src_path == results.get("fchk_file"):
                            results["fchk_file"] = dst_path
                if log_file and os.path.exists(log_file) and log_file not in results["output_files"]:
                    dst_log = os.path.join(original_output_dir, os.path.basename(log_file))
                    shutil.copy2(log_file, dst_log)
                    results["log_file"] = dst_log

            report(100, "任务完成")

        except Exception as e:
            results["error"] = str(e)
            import traceback
            logger.error("PSI4 任务执行异常: %s", e, exc_info=True)
            traceback.print_exc()

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            try:
                psi4.core.clean()
            except Exception:
                pass
            # 审计 UX2：停止进度嗅探线程并恢复 PSI4 输出目标（避免污染 worker 复用）
            try:
                if '_progress_stop' in locals() and _progress_stop is not None:
                    _progress_stop.set()
                    _progress_thread.join(timeout=2)
            except Exception:
                pass
            try:
                if '_progress_out' in locals() and _progress_out:
                    psi4.core.set_output_file("")
            except Exception:
                pass

    finally:
        _finalize()

    return results


# ================================================================
# 可取消的 PSI4 任务运行器（F03 队列「取消 / 超时终止」的硬地基）
# ================================================================
def _psi4_runner_script_path() -> Path:
    """返回子进程运行器脚本的绝对路径。"""
    return Path(__file__).resolve().parents[0] / "_subprocess_runner.py"


def _terminate_process_tree(proc: "subprocess.Popen", grace_period: float = 2.0) -> None:
    """
    跨平台杀掉整个进程树（PSI4 会派生 OpenMP/MPI 子进程，只杀父进程没用）。

    优化（审计建议）：先发「优雅退出」信号，等待 grace_period 秒让进程树自我清理
    （flush 输出、删除半成品 .fchk 等），仍未退出再强制杀，最后兜底 proc.kill()。
      - Windows: taskkill /T（不带 /F，温和终止）→ 等待 → taskkill /T /F（强制）
      - POSIX:   killpg(SIGTERM) → 等待 → killpg(SIGKILL)
    """
    pid = proc.pid
    if proc.poll() is not None:
        return  # 已经退出，无需处理
    # ---- 1) 优雅退出 ----
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass
    # ---- 2) 等待优雅退出 ----
    deadline = time.time() + grace_period
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    # ---- 3) 强制杀 ----
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            # 审计 4.3 修复：补充用 psutil 递归杀脱离进程组的后代（若可用），
            # 覆盖 PSI4 自行 setsid 导致其后代不在同一进程组、killpg 杀不到的场景。
            try:
                import psutil
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    # ---- 4) 兜底 ----
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def _read_new_progress(progress_path: str, state: dict, progress_callback) -> None:
    """读取 progress 文件中新增的 JSON 行并转发给 progress_callback。"""
    if progress_callback is None or not os.path.exists(progress_path):
        return
    try:
        with open(progress_path, "r", encoding="utf-8") as pf:
            content = pf.read()
    except Exception:
        return
    if not content:
        return
    parts = content.split("\n")
    # 末尾若没有换行，说明最后一行可能还没写完，跳过它避免重复/半行解析
    n_complete = len(parts) - 1
    last_n = state.get("last_n", 0)
    if n_complete <= last_n:
        return
    for i in range(last_n, n_complete):
        line = parts[i].strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            progress_callback(obj.get("p", 0), obj.get("m", ""))
        except Exception:
            pass
    state["last_n"] = n_complete


def _run_psi4_subprocess(
    input_file, task_type, method, basis, output_dir, preset_name,
    solvent, d3, charge, multiplicity, memory, *,
    progress_callback=None, cancel_check=None, timeout=None, poll_interval=0.2,
    **kwargs
) -> Dict:
    """
    在独立子进程里运行 run_psi4_task，主进程轮询 cancel_check / timeout，
    一旦触发就杀掉整个进程树实现强制取消。结果从临时 JSON 读回。
    """
    work_root = Path(__file__).resolve().parents[2]  # .../chem/psi4/core.py -> 工作区根
    runner = work_root / "chem" / "psi4" / "_subprocess_runner.py"
    if not runner.exists():
        raise RuntimeError(f"子进程运行器不存在: {runner}")

    tmp = tempfile.mkdtemp(prefix="psi4_sub_")
    cmd_path = os.path.join(tmp, "cmd.json")
    result_path = os.path.join(tmp, "result.json")
    progress_path = os.path.join(tmp, "progress.jsonl")

    # 拼出要传给子进程的关键字参数（剔除不可序列化的回调）
    cmd = dict(
        input_file=input_file,
        task_type=task_type,
        method=method,
        basis=basis,
        output_dir=output_dir,
        preset_name=preset_name,
        solvent=solvent,
        d3=d3,
        charge=charge,
        multiplicity=multiplicity,
        memory=memory,
    )
    for k, v in kwargs.items():
        if k in ("_progress_callback", "_extra_post_hook"):
            continue
        cmd[k] = v

    with open(cmd_path, "w", encoding="utf-8") as f:
        json.dump(cmd, f, ensure_ascii=False, default=str)

    env = os.environ.copy()
    pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(work_root) + (os.pathsep + pypath if pypath else "")

    # 用 `-m` 方式启动，避免把脚本所在目录（chem/psi4，里面有个 utils.py）
    # 塞进 sys.path[0] 而遮蔽顶层的 utils 包。cwd 设为工作区根保证包可导入。
    args = [sys.executable, "-m", "chem.psi4._subprocess_runner",
            cmd_path, result_path, progress_path]
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    proc = subprocess.Popen(
        args, cwd=str(work_root), env=env, creationflags=creationflags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    progress_state = {"last_n": 0}
    cancelled = False
    started = time.time()
    try:
        while proc.poll() is None:
            _read_new_progress(progress_path, progress_state, progress_callback)
            if cancel_check is not None and cancel_check():
                cancelled = True
                _terminate_process_tree(proc)
                break
            if timeout is not None and (time.time() - started) > timeout:
                cancelled = True
                _terminate_process_tree(proc)
                break
            time.sleep(poll_interval)
        # 收尾：把残余进度转发完
        _read_new_progress(progress_path, progress_state, progress_callback)
    finally:
        if proc.poll() is None:
            _terminate_process_tree(proc)
        try:
            proc.wait(timeout=3)
        except Exception:
            pass

    if cancelled:
        return {"success": False, "cancelled": True, "error": "任务已被取消（超时或用户取消）"}

    if not os.path.exists(result_path):
        return {"success": False, "error": "子进程未产出结果（可能崩溃或被强杀）"}

    try:
        with open(result_path, "r", encoding="utf-8") as rf:
            result = json.load(rf)
    except Exception as e:
        return {"success": False, "error": f"读取子进程结果失败: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return result


# ================================================================
# 持久热 worker（消除每次计算重导 psi4 的 ~10-15s 开销）
# ================================================================
_worker_proc = None
_worker_lock = threading.Lock()


def _ensure_worker() -> "subprocess.Popen":
    """懒启动 / 复用常驻 worker；worker 进程在启动时一次性导入 psi4。"""
    global _worker_proc
    if _worker_proc is not None and _worker_proc.poll() is None:
        return _worker_proc
    _worker_proc = None  # 之前的可能已死（被取消强杀或崩溃），需要重启
    work_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(work_root) + (os.pathsep + pypath if pypath else "")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform.startswith("win") else 0
    _worker_proc = subprocess.Popen(
        [sys.executable, "-m", "chem.psi4._worker"],
        cwd=str(work_root), env=env, creationflags=creationflags,
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )
    return _worker_proc


def _kill_worker() -> None:
    """杀掉常驻 worker 进程树（用于取消 / 退出清理）。"""
    global _worker_proc
    proc = _worker_proc
    _worker_proc = None
    if proc is None:
        return
    _terminate_process_tree(proc)
    try:
        proc.wait(timeout=3)
    except Exception:
        pass


def _shutdown_worker() -> None:
    """优雅关闭常驻 worker：写 SHUTDOWN 命令让其自行退出，避免孤儿进程。

    若 worker 已僵死 / stdin 已关闭导致优雅退出失败，则回退到强杀 _kill_worker()。
    """
    global _worker_proc
    proc = _worker_proc
    if proc is None:
        return
    try:
        if proc.poll() is None and proc.stdin is not None:
            proc.stdin.write('{"command": "SHUTDOWN"}\n')
            proc.stdin.flush()
        proc.wait(timeout=3)
        _worker_proc = None
    except Exception:
        _kill_worker()


def _run_psi4_worker(
    input_file, task_type, method, basis, output_dir, preset_name,
    solvent, d3, charge, multiplicity, memory, *,
    progress_callback=None, cancel_check=None, timeout=None, poll_interval=0.2,
    **kwargs
) -> Dict:
    work_root = Path(__file__).resolve().parents[2]
    tmp = tempfile.mkdtemp(prefix="psi4_wk_")
    result_path = os.path.join(tmp, "result.json")
    progress_path = os.path.join(tmp, "progress.jsonl")

    cmd = dict(
        input_file=input_file, task_type=task_type, method=method, basis=basis,
        output_dir=output_dir, preset_name=preset_name, solvent=solvent, d3=d3,
        charge=charge, multiplicity=multiplicity, memory=memory,
        result_path=result_path, progress_path=progress_path,
    )
    for k, v in kwargs.items():
        if k in ("_progress_callback", "_extra_post_hook"):
            continue
        cmd[k] = v

    with _worker_lock:
        try:
            proc = _ensure_worker()
            try:
                proc.stdin.write(json.dumps(cmd, ensure_ascii=False, default=str) + "\n")
                proc.stdin.flush()
            except Exception as _w_err:
                _kill_worker()
                raise RuntimeError(f"向 worker 写命令失败: {_w_err}")

            started = time.time()
            progress_state = {"last_n": 0}
            cancelled = False
            try:
                while True:
                    if os.path.exists(result_path):
                        break
                    _read_new_progress(progress_path, progress_state, progress_callback)
                    if cancel_check is not None and cancel_check():
                        cancelled = True
                        _kill_worker()
                        break
                    if timeout is not None and (time.time() - started) > timeout:
                        cancelled = True
                        _kill_worker()
                        break
                    if proc.poll() is not None:
                        # worker 在写出结果前就退出了（如 psi4 崩溃）
                        raise RuntimeError("worker 进程在计算完成前意外退出")
                    time.sleep(poll_interval)
                _read_new_progress(progress_path, progress_state, progress_callback)
            finally:
                pass
        finally:
            pass

    if cancelled:
        return {"success": False, "cancelled": True, "error": "任务已被取消（超时或用户取消）"}

    try:
        with open(result_path, "r", encoding="utf-8") as rf:
            result = json.load(rf)
    except Exception as e:
        raise RuntimeError(f"读取 worker 结果失败: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return result


def run_psi4_task_cancellable(
    input_file: str,
    task_type: str = 'energy',
    method: str = 'b3lyp',
    basis: str = '6-31g*',
    output_dir: Optional[str] = None,
    preset_name: Optional[str] = None,
    solvent: Optional[str] = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = '4 GB',
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
    timeout: Optional[float] = None,
    poll_interval: float = 0.2,
    **kwargs
) -> Dict:
    """
    可取消版本的 run_psi4_task。三级回退保证健壮：
      1) 常驻热 worker（psi4 仅导入一次，开销最低，支持取消/超时强杀）；
      2) 退化为每次启动的子进程（_subprocess_runner，仍支持取消，但每次重导 psi4）；
      3) 退化为同步直接调用 run_psi4_task（不可取消，但功能不丢）。

    参数：
      cancel_check: 无参 callable，返回 True 时取消。通常传 app.task_manager.is_cancelled
      timeout:      可选的最大运行秒数，超时即取消
      **kwargs:     透传 extra_options 等给底层 run_psi4_task
    """
    progress_callback = kwargs.get("_progress_callback")
    try:
        return _run_psi4_worker(
            input_file, task_type, method, basis, output_dir, preset_name,
            solvent, d3, charge, multiplicity, memory,
            progress_callback=progress_callback,
            cancel_check=cancel_check, timeout=timeout,
            poll_interval=poll_interval, **kwargs)
    except Exception as _w_err:
        logger.warning("PSI4 热 worker 失败，回退到子进程模式：%s", _w_err)
        try:
            return _run_psi4_subprocess(
                input_file, task_type, method, basis, output_dir, preset_name,
                solvent, d3, charge, multiplicity, memory,
                progress_callback=progress_callback,
                cancel_check=cancel_check, timeout=timeout,
                poll_interval=poll_interval, **kwargs)
        except Exception as _sp_err:
            logger.warning("PSI4 子进程模式也失败，回退到同步执行：%s", _sp_err)
            return run_psi4_task(
                input_file, task_type, method, basis, output_dir, preset_name,
                solvent, d3, charge, multiplicity, memory, **kwargs)


# 解释器退出时优雅关闭常驻 worker（失败回退强杀），避免残留进程
atexit.register(_shutdown_worker)