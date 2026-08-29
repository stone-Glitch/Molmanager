#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路径工具模块 - 集中管理所有路径安全、目录解析、跨平台兼容等公共函数

重构说明：
  本模块从以下文件中提取了重复代码：
  - config.py / logger.py: _app_data_dir(), _chmod_quiet()
  - main.py / model.py: _is_windows_junction()
  - model.py: enforce_no_symlink_target(), resolve_secure_output_path_external()
  - psi4_compute.py / reaction_animation.py: _secure_output_path(), _default_base_dir_from_input()

所有函数保持原有行为不变，仅做命名空间统一。
"""
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Union

PathLike = Union[str, os.PathLike]


# ==================== Windows 长路径（>260）扩展长度前缀 ====================

def win_longpath(p: PathLike) -> str:
    """
    将 Windows 绝对路径转换为 ``\\\\?\\`` 扩展长度（extended-length）格式，
    规避 Win32 传统 MAX_PATH=260 字符限制；非 Windows 或相对路径原样返回。

    适用边界（重要）：
      - 仅用于「最终交给 open()/os 文件操作」的字符串参数。
      - 不要用于仍需 Path 语义（.relative_to / .parent / 拼接）的中间路径，
        否则 ``\\\\?\\`` 前缀会让这些操作失效。
      - ``\\\\?\\`` 只是 Win32 API 前缀，文件系统里的真实路径仍是原样，
        因此后续用不带前缀的 Path 做 os.replace/mkdir 等不受影响。

    处理要点：
      - UNC ``\\\\server\\share\\...`` → ``\\\\?\\UNC\\server\\share\\...``
      - 本地盘符 ``C:\\foo``          → ``\\\\?\\C:\\foo``
      - 已是 ``\\\\?\\`` 前缀则幂等返回；相对路径 / 非 Windows 原样返回。
    """
    if sys.platform != "win32":
        return os.fspath(p)
    s = os.fspath(p)
    if not isinstance(s, str):
        s = str(s)
    if not os.path.isabs(s):
        return s
    s = s.replace("/", "\\")
    if s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


# ==================== 目录权限 ====================

def chmod_quiet(p: Path, mode: int) -> None:
    """静默设置文件/目录权限，失败不报错（Windows 下某些路径会拒绝）。"""
    try:
        if hasattr(os, 'chmod'):
            os.chmod(p, mode)
    except OSError:
        # Windows 对某些路径可能拒绝 chmod，静默跳过（ACL 仍有效）
        pass


# ==================== 应用数据目录 ====================

def get_app_data_dir() -> Path:
    """
    获取应用数据目录（跨平台）。
    - Windows: %APPDATA%/MolManager 或 ~/.mol_manager
    - macOS/Linux: ~/.mol_manager
    目录不存在时自动创建，并设置为仅当前用户可访问（0o700）。
    """
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA')
        if base:
            d = Path(base) / "MolManager"
        else:
            d = Path.home() / ".mol_manager"
    else:
        d = Path.home() / ".mol_manager"
    d.mkdir(parents=True, exist_ok=True)
    # CWE-732 修复：仅当前用户可读取/进入该目录，防止同机其他用户嗅探
    chmod_quiet(d, 0o700)
    return d


# ==================== 备份目录（F17 / T07）====================

#: 备份根目录名。
#: 🔴 改动此常量必须同步以下两处，否则备份文件会被当作普通文件误伤：
#:    1. utils/backup_manager.BACKUP_DIR_NAME
#:    2. core/model.PROTECTED_DIR_NAMES（scan_files / 整理 / 删除的排除名单）
BACKUP_DIR_NAME = ".backup"


def get_backup_dir(work_dir: PathLike | None = None, *, create: bool = True) -> Path:
    """
    获取备份根目录（F17 快照的存放位置）。

    参数:
        work_dir: 工作目录。给定时返回 ``<work_dir>/.backup``（快照跟着数据走，
                  换工作目录后互不干扰）；为 None 时回落到应用数据目录下的
                  ``.backup``，保证「还没选工作目录」时也有地方落盘。
        create:   是否自动创建目录（默认 True，并设为仅当前用户可访问 0o700）

    返回:
        备份根目录的 Path。**本函数不抛异常**：创建失败时仍返回路径对象，
        由调用方（BackupManager）按「备份失败只 WARNING」的契约处理。
    """
    if work_dir is not None:
        try:
            base = Path(work_dir)
        except (TypeError, ValueError):
            base = get_app_data_dir()
    else:
        base = get_app_data_dir()
    d = base / BACKUP_DIR_NAME
    if create:
        try:
            d.mkdir(parents=True, exist_ok=True)
            chmod_quiet(d, 0o700)
        except OSError:
            # 静默：备份目录建不出来不能阻断主流程（架构 §6.4）
            pass
    return d


# ==================== Windows Junction 检测 ====================

def is_windows_junction(path: PathLike, *, raise_on_junction: bool = False) -> bool:
    """
    检测路径是否为 Windows NTFS Junction / ReparsePoint。
    非 Windows 平台直接返回 False。

    参数:
        path: 要检测的路径
        raise_on_junction: 为 True 时，检测到 junction 会抛出 ValueError 而非返回 True

    返回:
        True = 是 junction / reparse point；False = 不是或非 Windows 平台
    """
    if os.name != "nt":
        return False
    try:
        p = Path(path)
        if not p.exists():
            return False
        try:
            st = os.lstat(p)
        except OSError:
            return False
        FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
        # ⚠️ BUG-1 修复：stat.S_IFMT 是「函数」(stat.S_IFMT(mode))，不是位掩码。
        # 旧代码误写成 `st.st_mode & stat.S_IFMT` 会抛 TypeError，
        # 被下方裸 except 静默吞掉 → 函数恒返回 False，整条防护链全线失效。
        # 这里改用函数调用形式；st_file_attributes 用 getattr 兜底
        # （非 Windows / 部分文件系统无此属性时为 0，即「非 reparse point」）。
        attrs = getattr(st, "st_file_attributes", 0)
        if stat.S_IFMT(st.st_mode) == stat.S_IFDIR and (attrs & FILE_ATTRIBUTE_REPARSE_POINT):
            if raise_on_junction:
                raise ValueError(
                    f"检测到 Windows Junction / ReparsePoint 目录，拒绝跟随操作: {os.fspath(p)!r}"
                )
            return True
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 安全降级必须留痕：检测失败绝不能再「静默吞掉」，否则会像 BUG-1 那样
        # 长期潜伏、在用户数据上酿成真实损坏。此处降级为 False（保守：不误判为
        # junction，交给 is_symlink / 上层 commonpath 等其余防线兜底）。
        from utils.logger import default_logger as _logger
        _logger.warning(
            "is_windows_junction 检测异常，降级为 False（保守非 junction）: %r (%s: %s)",
            os.fspath(path), type(exc).__name__, exc,
        )
        return False
    return False


# ==================== 符号链接 / Junction 安全检查 ====================

def enforce_no_symlink_target(
    path: PathLike,
    *,
    allow_nonexistent: bool = True,
    _level: str = "leaf",
) -> None:
    """
    安全检查：确保路径（及其**每一层祖先**）都不是符号链接或 Windows Junction。

    BUG-1 修复关键：必须逐级检查，不能只检查「叶子 + 直接父目录」。
    复现路径 ``wk/jn/.backup`` 中 junction 出现在**中间层** ``jn``，
    若只查叶子 ``benzene.mol`` 与其父 ``snap``，会完全漏掉 ``jn``，
    导致防护链失效、真实改写 ``.backup``（架构文档 C11 最大风险项）。

    实现要点：用 ``absolute()``（**不跟随** symlink/junction）拿到字面路径，
    再逐级拼接、对每一层已存在的目录调用 ``is_symlink()`` / ``is_windows_junction()``。
    ⚠️ 绝不能用 ``resolve()``：它会把 junction 中间层「折叠」成真实目标，
    使 ``jn`` 从 parts 中消失、无法被检出。

    参数:
        path: 要检查的路径
        allow_nonexistent: 最终目标不存在时是否静默通过（仅对最终目标生效；
                           已存在的祖先层仍会被检查，以拦下位于中间层的 junction/symlink）
        _level: 仅作调用层级标记，无安全语义

    抛出:
        ValueError: 任一层级检测到 symlink / junction 时
    """
    # 不调用 resolve()：resolve 会跟随 junction / symlink 把中间层「折叠」掉，
    # 导致 junction 这一级从 parts 中消失、无法被检出（BUG-1 复现路径 wk/jn/.backup 的 jn）。
    # 这里只用 absolute()（不跟随符号链接）拿到字面路径，再逐级拼接检查每一层。
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / p
    except (OSError, ValueError):
        return
    parts = p.parts
    if not parts:
        return
    cur = Path(parts[0])
    for seg in parts[1:]:
        cur = cur / seg
        try:
            exists = cur.exists()
        except OSError:
            exists = False
        if not exists:
            # 该层不存在：继续向下拼（可能是尚未创建的重命名目标），
            # 但其上层若存在 junction/symlink 已在上一轮被拦下。
            continue
        try:
            if cur.is_symlink():
                raise ValueError(f"检测到符号链接（symlink），拒绝操作: {os.fspath(cur)!r}")
        except OSError as exc:
            raise ValueError(f"无法判定是否为符号链接: {os.fspath(cur)!r} ({exc})") from exc
        if os.name == "nt":
            # 内部 raise_on_junction=True：命中即抛 ValueError（绝不静默）。
            is_windows_junction(cur, raise_on_junction=True)



# ==================== 安全输出路径解析 ====================

def resolve_secure_output_path(
    requested_path,
    *,
    base_dir,
    is_dir: bool = False,
    default_name=None,
    allow_outside: bool = False,
    create_parent: bool = False,
) -> Path:
    """
    安全解析输出路径（路径遍历防护 + symlink/junction 检测）。

    安全特性:
      - 禁止路径中包含 '..' 段
      - 默认限制输出在 base_dir 范围内（allow_outside=False）
      - 路径链上的每一级都检查 symlink / junction
      - 解析后真实路径仍需在 base_dir 内（防 symlink 穿透）

    参数:
        requested_path: 用户请求的输出路径（相对或绝对）
        base_dir: 允许的根目录（必须已存在）
        is_dir: 输出目标是否为目录（影响父目录创建逻辑）
        default_name: requested_path 为空时使用的默认名称
        allow_outside: 是否允许输出到 base_dir 之外
        create_parent: 是否自动创建父目录

    返回:
        规范化后的 Path 对象

    抛出:
        ValueError: 路径非法 / 越界 / 含 symlink 等
    """
    if not base_dir:
        raise ValueError("base_dir 不能为空")
    base_p = Path(base_dir)
    if not base_p.is_dir():
        raise ValueError(f"base_dir 必须是已存在的目录: {os.fspath(base_p)!r}")
    base_real = base_p.resolve(strict=True)

    # --- 规范化输入路径 ---
    raw = ""
    if requested_path is None:
        raw = ""
    elif isinstance(requested_path, bytes):
        raw = requested_path.decode("utf-8", "replace")
    else:
        raw = os.fspath(requested_path)
    raw = raw.strip() if isinstance(raw, str) else ""
    if not raw and default_name:
        raw = str(default_name)
    if not raw:
        raise ValueError("输出路径为空且未提供 default_name")

    # --- 禁止 '..' 段 ---
    raw_slashed = raw.replace("\\", "/")
    raw_segs = [s for s in raw_slashed.split("/") if s != ""]
    if any(seg == ".." for seg in raw_segs):
        raise ValueError(f"输出路径禁止包含 '..' 段: {raw!r}")

    # --- 拼出绝对路径 ---
    p = Path(raw)
    if not p.is_absolute():
        p = base_real / p

    # --- commonpath 范围检查 ---
    norm_abs = os.path.normpath(os.fspath(p))
    base_norm = os.path.normpath(os.fspath(base_real))
    if not allow_outside:
        # 🔴 显式预检盘符：Windows 上 os.path.commonpath 跨盘符会抛原生英文
        # ValueError("Paths don't have the same drive")，对用户极不友好且难定位。
        # 先用 splitdrive 取出盘符（含 UNC 的 \\server\share）比对，不同则给出明确
        # 中文错误并附两个路径与盘符，让用户/排错者一眼看到根因。
        d_abs, _ = os.path.splitdrive(norm_abs)
        d_base, _ = os.path.splitdrive(base_norm)
        if os.path.normcase(d_abs) != os.path.normcase(d_base):
            raise ValueError(
                f"输出路径与允许根目录不在同一盘符：\n"
                f"  请求路径：{norm_abs!r}（盘符 {d_abs or '(相对)'})\n"
                f"  允许根：  {base_norm!r}（盘符 {d_base or '(相对)'})\n"
                f"常见原因：工作目录在 D:/ 盘，但输出路径（或其父目录）被解析到了 C:/ "
                f"或其它盘符（如路径中含指向其它盘的 symlink/junction，或调用方传了"
                f"其它盘的 base_dir）。"
            )
        try:
            # 规范化到真实路径：展开 Windows 8.3 短名（如 LVDOUZ~1 → lvdouzhijia82）、
            # 统一大小写与分隔符，避免同一目录因短名/长名不一致被 commonpath 误判为「越界」。
            # 例：本机 TEMP 为 C:\Users\LVDOUZ~1，而 base_dir 经 resolve() 展开为长名，
            # 若不统一规范化会错误拒绝工作目录内合法路径。
            norm_abs_real = os.path.realpath(norm_abs)
            base_norm_real = os.path.realpath(base_norm)
            common = os.path.commonpath([base_norm_real, norm_abs_real])
            if os.path.normcase(common) != os.path.normcase(base_norm_real):
                raise ValueError(
                    f"输出路径越出允许范围（commonpath 判定）：请求 {norm_abs!r}，允许根 {base_norm!r}"
                )
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"输出路径规范化失败: {raw!r}") from exc
    cand = Path(norm_abs)

    # --- 路径链 symlink/junction 逐段检查 ---
    def _walk_chain(target: Path, base: Path) -> None:
        # 🔴 BUG-5：绝不能用 resolve()（会把 junction 中间层折叠掉），必须 absolute()，
        # 与 enforce_no_symlink_target 一致。
        try:
            rel = target.absolute().relative_to(base.absolute())
            parts_a = list(rel.parts)
        except (OSError, ValueError):
            parts_a = list(target.parts)
        cur = base
        for part in parts_a:
            cur = cur / part
            if not cur.exists():
                continue
            enforce_no_symlink_target(cur, allow_nonexistent=True, _level="chain")
        # 🔴 BUG-5：叶子（输出文件本身）即使尚不存在也要检查其祖先链。
        enforce_no_symlink_target(target, allow_nonexistent=True, _level="leaf")

    try:
        _walk_chain(cand, base_real)
    except ValueError as exc:
        raise ValueError(f"输出路径链中存在符号链接 / Junction，拒绝写入: {raw!r} ({exc})") from exc

    # --- 解析后真实路径范围检查（防 symlink 穿透）---
    if not allow_outside:
        try:
            if cand.exists() or cand.parent.exists():
                resolved = cand.resolve(strict=False)
            else:
                resolved = cand
            resolved.relative_to(base_real)
        except (OSError, ValueError) as exc:
            raise ValueError(f"解析后真实路径超出允许范围（含 symlink 穿透）: {raw!r}") from exc

    # --- 自动创建父目录 ---
    if create_parent:
        parent = cand if is_dir else cand.parent
        try:
            if not allow_outside:
                # 同样展开 8.3 短名（如 LVDOUZ~1 → lvdouzhijia82）后再做 relative_to 校验，
                # 避免短名/长名不一致被误判为「不在允许根目录内」。
                parent_resolved = Path(os.path.realpath(os.fspath(parent)))
                base_resolved = Path(os.path.realpath(os.fspath(base_real)))
                _ = parent_resolved.relative_to(base_resolved)
            parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法为输出路径创建父目录: {os.fspath(cand)!r} ({exc})") from exc

    return cand


# ==================== 安全输入文件解析 ====================

def resolve_secure_input_file(
    path: PathLike,
    *,
    base_dir: PathLike | None = None,
    allow_outside: bool = True,
) -> Path:
    """
    安全解析「被读取的输入文件」路径。

    与 resolve_secure_output_path（限制**写入**落点）不同，输入文件由用户/调用方
    显式选择，可能位于任意目录，因此默认 allow_outside=True（不做来源白名单），
    仅保证最终解析到的是一个**真实存在的普通文件**（拒绝目录 / 设备 / 不存在路径），
    防止读到非预期位置或把目录当文件读（审计：reaction_animation 输入越权读取）。

    若确实需要限制来源（例如内部生成的临时文件），传 base_dir 且 allow_outside=False
    即可开启白名单校验。

    参数:
        path: 输入文件路径
        base_dir: 允许的来源根目录（allow_outside=False 时生效）
        allow_outside: 是否允许文件位于 base_dir 之外（默认 True）

    返回:
        解析后的真实文件路径（Path）

    抛出:
        ValueError: 文件不存在 / 不是普通文件 / 越出 base_dir 范围
    """
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / p
    except (OSError, ValueError) as exc:
        raise ValueError(f"非法的输入文件路径: {os.fspath(path)!r} ({exc})") from exc
    # realpath 不要求路径存在；先拿到真实路径再判存在性/类型，
    # 避免 resolve(strict=True) 在部分 Windows 路径上误抛 OSError。
    real = Path(os.path.realpath(os.fspath(p)))
    if not real.exists():
        raise ValueError(f"输入文件不存在: {os.fspath(real)!r}")
    if not real.is_file():
        raise ValueError(
            f"输入路径不是普通文件（可能是目录或设备），拒绝读取: {os.fspath(real)!r}"
        )
    if base_dir is not None and not allow_outside:
        try:
            base_real = Path(base_dir).resolve(strict=True)
            real.relative_to(base_real)
        except (OSError, ValueError) as exc:
            raise ValueError(f"输入文件越出允许来源范围: {os.fspath(real)!r}") from exc
    return real


# ==================== 默认 base_dir 推断 ====================

def default_base_dir_from_input(
    *inputs: PathLike | None,
    fallback: PathLike | None = None,
) -> Path:
    """
    从输入文件/目录推断默认的 base_dir。
    优先级：第一个存在的输入的父目录 → fallback → cwd → tempdir。

    目的：避免用户随便输相对路径时跑到 cwd 下。
    """
    for inp in inputs:
        if inp is None:
            continue
        try:
            p = Path(inp)
            if p.is_dir():
                return p.resolve()
            if p.parent.is_dir():
                return p.parent.resolve()
        except Exception:
            continue
    if fallback is not None:
        try:
            pf = Path(fallback)
            if pf.is_dir():
                return pf.resolve()
            if pf.parent.is_dir():
                return pf.parent.resolve()
        except Exception:
            pass
    try:
        cwd = Path.cwd()
        if cwd.is_dir():
            return cwd.resolve()
    except Exception:
        pass
    return Path(tempfile.gettempdir()).resolve()


# ==================== 便捷封装 ====================

def secure_output_path(
    requested_path,
    *,
    is_dir: bool = False,
    default_name=None,
    base_dir=None,
    allow_outside: bool = False,
    create_parent: bool = True,
) -> Path:
    """
    resolve_secure_output_path 的便捷封装：
    - base_dir 为 None 时自动推断（cwd → tempdir）
    - 默认 create_parent=True（更常用）
    """
    if base_dir is None:
        try:
            cwd = Path.cwd()
            if cwd.is_dir():
                base_dir = cwd
            else:
                raise RuntimeError
        except Exception:
            base_dir = Path(tempfile.gettempdir())
    return resolve_secure_output_path(
        requested_path,
        base_dir=base_dir,
        is_dir=is_dir,
        default_name=default_name,
        allow_outside=allow_outside,
        create_parent=create_parent,
    )


# ==================== 过期临时目录清理 ====================

def cleanup_stale_tempdirs(max_age_seconds: int = 3 * 24 * 3600) -> int:
    """
    清理系统临时目录中过期（> max_age_seconds）的 psi4_temp_* 目录，返回删除数量。
    安全加固（修复 CWE-59 符号链接跟随 / CWE-367 TOCTOU / 审计 1.2 Windows junction）：
      - 仅在系统临时目录内匹配，不触碰 cwd。
      - 先用 resolve(strict=True) 拿真实路径，再 relative_to 校验仍在临时根目录内。
      - 拒绝所有 is_symlink / Windows junction 路径。
      - age 检查与 rmtree 作用于同一已 resolve 的 Path 对象，缩小竞争窗口。
    """
    from utils.logger import default_logger as logger
    removed = 0
    roots: set = set()
    for envvar in ('TMPDIR', 'TEMP', 'TMP'):
        v = os.environ.get(envvar)
        if v:
            try:
                roots.add(Path(v).resolve())
            except OSError:
                continue
    try:
        roots.add(Path(tempfile.gettempdir()).resolve())
    except OSError:
        pass
    roots = {r for r in roots if r and r.is_dir()}
    if not roots:
        return 0
    now = time.time()
    seen: set = set()
    for root in roots:
        try:
            candidates = list(root.glob("psi4_temp_*"))
        except OSError:
            continue
        for d in candidates:
            try:
                if is_windows_junction(d):
                    continue
                real = d.resolve(strict=True)
                if d.is_symlink() or real.is_symlink():
                    continue
                if is_windows_junction(real):
                    continue
                if real in seen:
                    continue
                seen.add(real)
                try:
                    real.relative_to(root)
                except ValueError:
                    continue
                if not real.is_dir():
                    continue
                if not real.name.startswith("psi4_temp_"):
                    continue
                try:
                    st = real.stat(follow_symlinks=False)
                except OSError:
                    continue
                if now - st.st_mtime >= max_age_seconds:
                    try:
                        shutil.rmtree(real, ignore_errors=True)
                        removed += 1
                    except OSError:
                        pass
            except Exception:
                continue
    if removed:
        logger.info("清理过期临时目录 %d 个（> %.1f 天）", removed, max_age_seconds / 86400.0)
    return removed


# ==================== 统一临时目录管理 ====================
# 全项目所有「散落各处的 mkdtemp」都应改走这里：创建即注册，由 atexit 统一兜底清理，
# 防止进程异常退出（崩溃 / 强杀）时残留临时目录。手动清理后调用 unregister_temp_dir
# 可移除登记（不调用也无害——cleanup_all_temp_dirs 对已不存在的目录自动跳过）。
import atexit as _atexit
import threading as _threading

_TEMP_DIRS: list[Path] = []
_TEMP_DIRS_LOCK = _threading.Lock()


def make_temp_dir(prefix: str = "molmanager_") -> str:
    """创建临时目录并注册到统一清理清单。返回与 ``tempfile.mkdtemp`` 相同的 str 路径。"""
    p = tempfile.mkdtemp(prefix=prefix)
    register_temp_dir(p)
    return p


def register_temp_dir(p) -> None:
    """把临时目录注册到统一清理清单（幂等，重复注册只保留一份）。"""
    if not p:
        return
    try:
        pp = Path(p)
    except Exception:
        return
    with _TEMP_DIRS_LOCK:
        if pp not in _TEMP_DIRS:
            _TEMP_DIRS.append(pp)


def unregister_temp_dir(p) -> None:
    """手动清理后把登记项移除（幂等）。"""
    if not p:
        return
    try:
        pp = Path(p)
    except Exception:
        return
    with _TEMP_DIRS_LOCK:
        try:
            _TEMP_DIRS.remove(pp)
        except ValueError:
            pass


def cleanup_all_temp_dirs() -> int:
    """立即清理所有已登记且仍存在的临时目录，返回删除数量。"""
    with _TEMP_DIRS_LOCK:
        all_dirs = list(_TEMP_DIRS)
        _TEMP_DIRS.clear()
    removed = 0
    for d in all_dirs:
        try:
            if d.exists():
                shutil.rmtree(str(d), ignore_errors=True)
                removed += 1
        except Exception:
            pass
    return removed


_atexit.register(cleanup_all_temp_dirs)
