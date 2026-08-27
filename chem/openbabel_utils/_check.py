from pathlib import Path
import tempfile
from typing import Any

from utils.constants import (
    OB_PROPLIST_TIMEOUT_SEC,
    OB_VERSION_TIMEOUT_SEC,
)
from utils.logger import default_logger as logger

from ._cli import _load_manual_from_config, _resolve_obabel_cli, _run_obabel

# ======================== 导入与版本兼容 ========================
from ._common import _DEFAULT_BASE_DIR, _OBABEL_CLI_LOCK


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
