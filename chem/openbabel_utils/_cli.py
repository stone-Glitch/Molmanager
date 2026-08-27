import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from utils.constants import (
    OB_DEFAULT_TIMEOUT_SEC,
    OB_PROPLIST_TIMEOUT_SEC,
    OB_VERSION_TIMEOUT_SEC,
)
from utils.logger import default_logger as logger

# ======================== 导入与版本兼容 ========================
from ._common import _DEFAULT_BASE_DIR, _OBABEL_CLI_LOCK


def set_manual_obabel_path(path: str | None) -> None:
    """设置用户手动指定的 obabel 可执行文件路径。传入 None 或 "" 会清除手动设置并回退到自动查找。"""
    global _MANUAL_OBABEL_PATH, _OBABEL_CLI_EXE
    v = (path or "").strip() if path else ""
    # 与 _resolve_obabel_cli 共用同一把锁，保证对两个全局变量的读写原子（审计 3.2）
    with _OBABEL_CLI_LOCK:
        if not v:
            _MANUAL_OBABEL_PATH = None
        else:
            _MANUAL_OBABEL_PATH = v
        # 手动路径改了，必须让下一次调用重新解析（清掉已缓存的 _OBABEL_CLI_EXE）
        _OBABEL_CLI_EXE = None



def get_manual_obabel_path() -> str | None:
    return _MANUAL_OBABEL_PATH



def _load_manual_from_config() -> str | None:
    """在第一次解析 CLI 时，从配置懒加载用户手动路径（避免 openbabel_utils → config 循环 import）。"""
    try:
        from utils.config import load_config
        cfg = load_config()
        v = str(cfg.get("obabel_path", "") or "").strip()
        return v or None
    except Exception as _e:
        logger.debug("从配置加载 obabel_path 失败，忽略: %s", _e)
        return None


# ======================== 子进程包装（安全、跨平台） ========================

def _resolve_obabel_cli() -> str:
    """
    安全解析 obabel 命令行可执行文件的绝对路径，
    避免相对名 + PATH 搜索导致的本地可执行文件劫持（B607/CWE-426）。

    ===== 放宽策略（问题三修复） =====
      - 顺序：① 手动路径（用户显式指定）② PATH 中的 obabel ③ 常见安装目录兜底
      - 只拒绝 tempdir / 当前工作目录 下的真实可执行（因为它们是典型的劫持目录）
      - 不再一概拒绝用户家目录：conda 安装到 ~/anaconda3/bin、~/miniconda3/bin、
        ~/AppData/Local/Continuum/anacondaX 等都是用户常用合法路径；另外「手动路径」
        因为是用户明确点选，视为显式信任，不再做目录黑白名单。
      - 解析结果缓存，加锁保护单例初始化。
    """
    import shutil as _shutil
    import tempfile as _tempfile
    global _OBABEL_CLI_EXE, _MANUAL_OBABEL_PATH

    def _candidate_locations() -> list[str]:
        """兜底：常见安装位置（只在 shutil.which 找不到时试，避免误劫持）。"""
        home = Path.home()
        out: list[str] = []
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\OpenBabel-3.1.1\obabel.exe",
                r"C:\Program Files\OpenBabel-3.0.0\obabel.exe",
                r"C:\Program Files (x86)\OpenBabel-3.1.1\obabel.exe",
                str(home / "anaconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "miniconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "Miniconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "Anaconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "AppData" / "Local" / "Programs" / "OpenBabel" / "obabel.exe"),
            ]
            out.extend(candidates)
        else:
            out.extend([
                "/usr/bin/obabel",
                "/usr/local/bin/obabel",
                "/opt/homebrew/bin/obabel",              # macOS Apple Silicon
                "/usr/local/homebrew/bin/obabel",        # macOS Intel
                str(home / "anaconda3" / "bin" / "obabel"),
                str(home / "miniconda3" / "bin" / "obabel"),
                str(home / "miniforge3" / "bin" / "obabel"),
                str(home / "mambaforge" / "bin" / "obabel"),
                str(home / "bin" / "obabel"),
            ])
        return out

    def _safe_real(p: Path, *, user_explicit: bool) -> Path:
        """
        解析真实路径 + 安全检查。
        user_explicit=True 时：只做「存在性 + 是文件」检查，不再限制目录（视为用户显式信任）。
        user_explicit=False 时：拒绝 tempdir 和 cwd（家目录放行）。
        """
        try:
            # 审计 4.1：断开映射的网络驱动器（如 Z:）在 resolve(strict=True) 会抛 OSError（设备未就绪）；
            # 改用 absolute()（不访问磁盘、绝不抛异常）拿到基础路径，再尝试 resolve(strict=False)。
            # strict=False 不会因文件不存在而抛错；仅极少数 Windows 版本对坏链接抛 OSError，
            # 此时退回 absolute() 结果并交由下方 exists() 判定——避免「本地 PATH 上的 obabel 因某个
            # 坏候选路径而整体不可用」的连坐问题。
            abs_p = p.absolute()
        except Exception:
            abs_p = Path(os.fspath(p))
        try:
            real = abs_p.resolve(strict=False)
        except OSError:
            real = abs_p
        if not real.exists() or not real.is_file():
            raise RuntimeError(f"obabel 路径不存在或不可读: {p}")
        if not real.is_file():
            raise RuntimeError(f"obabel 路径不是文件: {real}")
        if user_explicit:
            # 用户显式选择的路径：信任他的选择，但仍然校验可执行（后面 _run_obabel 调用时会得到真实报错）
            logger.info("使用用户指定的 OpenBabel CLI: %s", real)
            return real
        # 自动路径的安全检查：只拒绝 临时目录 和 当前工作目录
        unsafe_roots: list[Path] = []
        for _cand in (
            _tempfile.gettempdir(),
            os.getcwd(),
        ):
            try:
                unsafe_roots.append(Path(_cand).resolve(strict=False))
            except Exception as _e:
                logger.debug("obabel 安全路径检查：跳过不可解析目录 %r: %s", _cand, _e)
        for root in unsafe_roots:
            try:
                real.relative_to(root)
                raise RuntimeError(
                    f"出于安全考虑，拒绝执行在可写目录下的 obabel 真实路径: {real}（父目录={root}），"
                    "请把它移动到系统路径，或通过「菜单→帮助→环境诊断」手动选择该路径以显式信任。"
                )
            except ValueError:
                # ValueError = 不在 root 下，是期望的安全结果
                logger.debug("obabel 路径安全检查通过：%s 不在 %s 下", real, root)
        return real

    # 双检锁（DCL）避免频繁抢锁
    if _OBABEL_CLI_EXE is not None:
        return _OBABEL_CLI_EXE
    with _OBABEL_CLI_LOCK:
        if _OBABEL_CLI_EXE is not None:
            return _OBABEL_CLI_EXE

        # Step 1：懒加载用户手动配置路径（第一次调用才读 config）
        if _MANUAL_OBABEL_PATH is None:
            cfg_v = _load_manual_from_config()
            if cfg_v:
                _MANUAL_OBABEL_PATH = cfg_v

        # Step 2：手动路径优先
        if _MANUAL_OBABEL_PATH:
            real_path = _safe_real(Path(_MANUAL_OBABEL_PATH), user_explicit=True)
            _OBABEL_CLI_EXE = str(real_path)
            return _OBABEL_CLI_EXE

        # Step 3：shutil.which（PATH 解析）
        resolved = _shutil.which("obabel")
        real_path: Path | None = None
        if resolved:
            try:
                real_path = _safe_real(Path(resolved), user_explicit=False)
            except RuntimeError as _e:
                logger.warning("自动解析的 obabel 路径不安全/不可用：%s，继续尝试常见目录", _e)
                real_path = None

        # Step 4：常见安装目录兜底
        if real_path is None:
            last_err: Exception | None = None
            for cand in _candidate_locations():
                if not os.path.isfile(cand):
                    continue
                try:
                    real_path = _safe_real(Path(cand), user_explicit=False)
                    break
                except RuntimeError as _e:
                    last_err = _e
                    logger.debug("obabel 兜底候选不可用 %s: %s", cand, _e)
            if real_path is None and last_err is not None:
                # 把兜底里最严重的错误也带出去，方便诊断
                raise RuntimeError(
                    "未找到可用的 obabel（OpenBabel 命令行），请安装或手动指定路径。\n"
                    f"最近一次失败原因：{last_err}"
                )

        if real_path is None:
            raise RuntimeError(
                "未在 PATH 和常见安装目录中找到 obabel（OpenBabel 命令行），请安装后重试。\n"
                "也可以通过「菜单 → 帮助 → 环境诊断 → 手动选择 obabel 路径」指定。"
            )
        _OBABEL_CLI_EXE = str(real_path)
        return _OBABEL_CLI_EXE



def _run_obabel(args: list[str], timeout: int | None = OB_DEFAULT_TIMEOUT_SEC,
                check: bool = False) -> subprocess.CompletedProcess:
    """
    安全执行 obabel 命令，自动处理 Windows 控制台窗口隐藏。
    args[0] 应为 "obabel"（相对名占位），此函数会替换为已解析的绝对路径。

    H-2 修复：timeout 默认从 constants 取 OB_DEFAULT_TIMEOUT_SEC（60s），不再是 None；
    避免异常 obabel 死锁卡住整个后台任务。大分子场景可显式传 OB_LARGE_TIMEOUT_SEC 或更大值。
    若需要无限制（极少场景），显式传 timeout=None。

    【审计 2.1 修复：命令参数防选项注入】
    所有调用方给出的「位置参数」（输入文件、输出文件、SMILES 串等）：
      a) 若该参数是「文件路径」，则一律转换为绝对路径；
      b) 若该参数可能是 "-" 开头的文件名，在前面补 "./"（Unix）或保留绝对路径。
    同时，本函数对每个 token 做最基本的 NUL/CR/LF 剔除，避免 subprocess 层报错或潜在的
    选项拼接风险。
    """
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs = {
            'startupinfo': startupinfo,
            'creationflags': subprocess.CREATE_NO_WINDOW
        }
    else:
        kwargs = {}

    if not args:
        raise ValueError("_run_obabel 调用缺少命令参数")
    exe = _resolve_obabel_cli()
    real_args: list[str] = [exe]

    def _hygiene(token: str) -> str:
        """剔除控制字符与 shell 元字符，防止命令行层被注入或截断。"""
        if token is None:
            return ""
        if not isinstance(token, str):
            token = str(token)
        # 剔除控制字符（NUL/CR/LF/TAB/换页/垂直制表）
        cleaned = "".join(ch for ch in token if ch not in "\x00\r\n\t\f\v")
        # 纵深防御：我们生成的参数不应含 shell 元字符，若出现则剔除并告警
        # （防止未来有外部可控参数拼接进来时产生选项注入 / 命令分隔，审计 S-1）
        _SHELL_META = set(";|&$`()<>\\'\"")
        if any(ch in _SHELL_META for ch in cleaned):
            logger.warning("openbabel 参数含潜在危险字符，已剔除: %r", token)
            cleaned = "".join(ch for ch in cleaned if ch not in _SHELL_META)
        return cleaned

    def _sanitize_file_path_arg(token: str) -> str:
        """把文件路径变成绝对路径（若存在），并做 hygiene。绝对路径天然不会被当作选项。"""
        t = _hygiene(token)
        if not t:
            return t
        p = Path(t)
        try:
            if p.exists() or p.parent.exists():
                # 走绝对路径：避免 "-foo.xyz" 被 obabel 当选项
                return os.path.abspath(os.fspath(p))
        except OSError:
            pass
        if not p.is_absolute():
            # 相对路径以 "-" 开头：用 "./" 前缀（或 Windows 下 ".\\"）防被 obabel 当选项
            if p.name.startswith("-"):
                return os.path.join(".", os.fspath(p))
        return os.path.abspath(os.fspath(p)) if not t.startswith("-") else os.path.join(".", t)

    rest: list[str] = []
    if args[0] in ("obabel", "obabel.exe", str(Path("obabel"))):
        rest = list(args[1:])
    else:
        rest = list(args)

    # 规则：
    #   - 如果 token 形如 "-O"、"-m" 这种单字母开关，或 "--..." 长选项，原样保留；
    #   - 如果 token 前面紧跟的开关是 "-O" / "-xi" / "-xo" / "--align" / "-a" / "-s" / "-v" / "-O"
    #     等已知要求文件 / 字符串值的开关，就做对应清洗（文件或纯字符串 hygiene）。
    #   - 其余自由位置参数视为输入文件 → _sanitize_file_path_arg。
    FILE_SWITCHES = {"-O", "-xi", "-xo", "-xr", "-xc", "--align", "-p"}
    VALUE_SWITCHES_EXPECT_FILE = FILE_SWITCHES

    i = 0
    n = len(rest)
    while i < n:
        tok = _hygiene(rest[i])
        if not tok:
            i += 1
            continue
        if tok == "--":
            # 显式「之后全是位置参数」：之后全部视为输入文件
            real_args.append("--")
            i += 1
            while i < n:
                real_args.append(_sanitize_file_path_arg(rest[i]))
                i += 1
            break
        if tok in VALUE_SWITCHES_EXPECT_FILE and i + 1 < n:
            real_args.append(tok)
            real_args.append(_sanitize_file_path_arg(rest[i + 1]))
            i += 2
            continue
        if tok.startswith("-"):
            # 其他开关（可能无值）：直接加，值用 hygiene
            real_args.append(tok)
            # 处理 紧接的值（如果不是另一个开关，就做 hygiene）
            if i + 1 < n and not rest[i + 1].startswith("-"):
                real_args.append(_hygiene(rest[i + 1]))
                i += 2
            else:
                i += 1
            continue
        # 自由位置参数 → 输入文件
        real_args.append(_sanitize_file_path_arg(tok))
        i += 1

    return subprocess.run(
        real_args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        shell=False,
        **kwargs
    )


# 默认可信根目录（安全护栏 base_dir 的回退值）。
# 由 model 在设置工作目录时写入 = 工作目录，使所有 ob_utils 写出操作默认以工作目录为允许根，
# 避免「工作目录 ≠ 程序启动目录」时被路径护栏误拒
# （例：work_dir=D:\...\化学\output，而程序 cwd=D:\...\工作区，两者为兄弟目录）。
# 审计 5.2：此为进程级全局变量。本应用为单 MolManagerModel 实例 / 单进程架构，全局兜底足够；
# 若将来出现「同一进程多个 Model 指向不同工作目录」的场景，全局会被后者覆盖导致路径校验混乱——
# 届时调用方应显式传 base_dir（_secure_output_path 已支持），而非依赖此全局。当前架构下仅作便利兜底。

def set_default_base_dir(path=None) -> None:
    """设置 ob_utils 写出操作默认的可信根目录（base_dir 为 None 时的回退）。传 None 还原为 cwd 兜底。"""
    global _DEFAULT_BASE_DIR
    if path is None:
        _DEFAULT_BASE_DIR = None
        return
    try:
        p = Path(path)
        # 允许尚不存在的目录（调用方常先 mkdir 再写）
        _DEFAULT_BASE_DIR = str(p.resolve())
    except Exception:
        try:
            _DEFAULT_BASE_DIR = str(Path(path))
        except Exception:
            _DEFAULT_BASE_DIR = str(path)


# 输出路径安全校验：统一包装（审计 1.1 路径遍历修复）

def _secure_output_path(
    requested_path,
    *,
    base_dir=None,
    is_dir: bool = False,
    default_name=None,
    allow_outside: bool = False,
    create_parent: bool = False,
) -> Path:
    """
    对 obabel 输出文件 / 目录路径做安全解析。
    base_dir 若为 None：
      - 若 requested_path 是绝对路径，走 allow_outside 判定（默认仍不允许越出 None 语义
        上的 work_dir，但若调用方明确 allow_outside=True 则放行）；
      - 若为相对路径：以当前工作目录（tempfile.gettempdir fallback）为基准，
        但我们更建议调用方显式传 base_dir。
    """
    from utils.path_utils import resolve_secure_output_path

    if base_dir is None:
        base_dir = _DEFAULT_BASE_DIR
    if base_dir is None:
        # 兜底：优先当前 cwd；不存在时 fallback 到临时目录
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


# ======================== 环境检测 ========================

def check_openbabel() -> tuple[bool, str, dict[str, Any]]:
    """
    检测 Open Babel 是否可用，优先使用 pybel 接口，其次检测命令行。

    增强版（问题三修复）：返回更详细信息，包括版本、支持的格式数、接口类型、
    用户指定的路径、安装指引 install_guide 等。

    返回: (可用性, 消息, 详情字典)
    详情字典 keys:
      - interfaces_available: list[str]  ("pybel" / "cli")
      - pybel_version / cli_version: str 或 None
      - supported_format_count: int 或 None
      - warnings: list[str]
      - manual_path_used: bool
      - resolved_cli_path: str | None
      - install_guide: str (OB_INSTALL_GUIDE，UI 可直接展示)
      - diagnosis: str[] (按严重程度排的诊断建议)
    """
    global _MANUAL_OBABEL_PATH
    details: dict[str, Any] = {
        "interfaces_available": [],
        "pybel_version": None,
        "cli_version": None,
        "supported_format_count": None,
        "warnings": [],
        "manual_path_used": False,
        "resolved_cli_path": None,
        "install_guide": OB_INSTALL_GUIDE,
        "diagnosis": [],
    }
    warning_list: list[str] = details["warnings"]
    diagnosis_list: list[str] = details["diagnosis"]

    # 首次探测：懒加载手动路径（加锁保护全局状态，避免与 _resolve_obabel_cli / set_manual_obabel_path 竞争；
    # 锁块仅设置 _MANUAL_OBABEL_PATH，不含后续对 _resolve_obabel_cli 的调用，避免不可重入锁死锁，审计 3.2）
    with _OBABEL_CLI_LOCK:
        if _MANUAL_OBABEL_PATH is None:
            _MANUAL_OBABEL_PATH = _load_manual_from_config()
    if _MANUAL_OBABEL_PATH:
        details["manual_path_used"] = True

    # 1. 检测 pybel
    pybel_ok = False
    if PYBEL_AVAILABLE:
        try:
            mol = pybel.readstring("smi", "C")
            if mol:
                pybel_ok = True
                details["interfaces_available"].append("pybel")
                try:
                    details["pybel_version"] = getattr(ob, "__version__", None) or \
                                                getattr(pybel, "__version__", None) or \
                                                "unknown"
                except Exception as _ve:
                    logger.debug("pybel 版本探测失败: %s", _ve)
                try:
                    n_fmts = len(getattr(pybel, "informats", {})) + len(getattr(pybel, "outformats", {}))
                    details["supported_format_count"] = max(0, n_fmts)
                except Exception as _ce:
                    logger.debug("pybel 支持格式计数失败: %s", _ce)
            else:
                warning_list.append("pybel 导入但无法创建测试分子 CH4")
        except Exception as e:
            warning_list.append(f"pybel 接口异常: {e}")
    else:
        diagnosis_list.append("未检测到 Python 包 pybel/openbabel（仅影响部分高级功能），建议安装 conda 版 OpenBabel")

    # 2. 检测命令行
    cli_ok = False
    try:
        # 先拿到解析到的路径（失败也不能让整个 check 抛错，只记 warning）
        try:
            exe = _resolve_obabel_cli()
            details["resolved_cli_path"] = exe
        except Exception as _re:
            warning_list.append(f"obabel 命令行未找到或不可用: {_re}")
            exe = None
        if exe:
            result = _run_obabel(["obabel", "-V"], timeout=OB_VERSION_TIMEOUT_SEC)
            if result.returncode == 0 and result.stdout.strip():
                cli_ok = True
                details["interfaces_available"].append("cli")
                details["cli_version"] = result.stdout.strip()
            else:
                warning_list.append(f"obabel -V 返回码={result.returncode}, stderr={result.stderr[:200]}")
    except Exception as e:
        warning_list.append(f"无法运行 obabel 命令行: {e}")

    if not cli_ok:
        if details.get("manual_path_used"):
            diagnosis_list.append("已配置手动 obabel 路径，但命令行仍不可用，请检查路径是否指向正确的可执行文件（Windows 下应为 obabel.exe）")
        else:
            diagnosis_list.append("未找到 obabel 命令行：推荐执行 conda install -c conda-forge openbabel，或使用下方「手动选择路径」")
        diagnosis_list.append("点击状态栏右下角的红点（OB 指示灯）可查看完整诊断并一键进入手动路径设置")

    # 汇总
    available = pybel_ok or cli_ok
    if available:
        if pybel_ok and cli_ok:
            msg = f"pybel + CLI 双接口可用（pybel={details['pybel_version']}, cli={details['cli_version']}）"
        elif pybel_ok:
            msg = f"pybel 接口可用（版本={details['pybel_version']}）"
        else:
            msg = f"obabel 命令行可用（版本={details['cli_version']}）"
        if details["supported_format_count"]:
            msg += f"，支持约 {details['supported_format_count']} 种格式"
        if details["resolved_cli_path"]:
            msg += f"；CLI 路径={details['resolved_cli_path']}"
        if details["manual_path_used"]:
            msg += "（使用用户手动指定路径）"
        if warning_list:
            msg += f"（警告：{len(warning_list)} 条）"
    else:
        msg_parts = ["OpenBabel 不可用"]
        if warning_list:
            msg_parts.append("；".join(warning_list[:2]))
        msg_parts.append("点击下方「环境诊断」或状态栏红点查看完整解决指引")
        msg = "，".join(msg_parts)

    return available, msg, details



def check_openbabel_simple() -> tuple[bool, str]:
    """兼容旧调用方：只返回 (bool, str)，内部调用增强版。"""
    ok, msg, _ = check_openbabel()
    return ok, msg



def get_supported_formats() -> list[str]:
    """
    获取 Open Babel 支持的读写格式列表。
    返回格式名称列表（字符串）。
    """
    formats: set[str] = set()
    if PYBEL_AVAILABLE:
        try:
            formats.update(pybel.informats.keys())
            formats.update(pybel.outformats.keys())
        except AttributeError as _ae:
            logger.debug("pybel informats/outformats 属性缺失: %s", _ae)
    else:
        try:
            result = _run_obabel(["obabel", "-L", "formats"], timeout=OB_PROPLIST_TIMEOUT_SEC)
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = line.strip().split()
                    if parts:
                        formats.add(parts[0])
        except Exception as _e:
            logger.debug("obabel CLI 查询格式列表失败: %s", _e)
    return sorted(formats)


# ======================== 格式转换 ========================
# 集中化：从 constants 复用，避免多处重复硬编码
