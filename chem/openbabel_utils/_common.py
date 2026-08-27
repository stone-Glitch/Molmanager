"""

Open Babel 工具模块 - 封装常用分子操作

支持格式转换、SMILES生成、力场优化、描述符计算、分子叠加等

所有函数返回统一格式：{'success': bool, 'message': str, 'data': any}

其中 'data' 包含具体结果（如描述符字典、文件路径等）。

"""


import os
import threading

from utils.cache import LRUCache
from utils.constants import (
    COMMON_INPUT_FORMATS,
)


# ======================== 导入与版本兼容 ========================

try:
    # 新版 OpenBabel (>=3.0) 推荐使用 openbabel 模块
    import openbabel as ob
    import openbabel.pybel as pybel
    PYBEL_AVAILABLE = True
except ImportError:
    try:
        # 旧版使用 pybel 顶层模块
        import pybel
        PYBEL_AVAILABLE = True
    except ImportError:
        PYBEL_AVAILABLE = False

# ======================== 缓存（性能优化 + 线程安全） ========================
# 原实现为各自手写 OrderedDict + move_to_end + popitem 的重复 LRU；
# 现统一到 utils.cache.LRUCache（线程安全、容量上限、原子淘汰）。

_DESC_CACHE_MAX = 128

desc_cache: "LRUCache" = LRUCache(maxsize=_DESC_CACHE_MAX)

#: 仅对不超过该大小的文件做整文件内容哈希，作为缓存键的额外维度（审计 P-2）；
#: 超过则退回 (mtime, size) 仅键，避免读取巨文件拖累性能（P-3 关注大文件场景）。

_CONTENT_HASH_MAX_BYTES = 2 * 1024 * 1024

_MOL_READ_CACHE_MAX = 256
#: 单个文件超过此大小（默认 50MB）时不进缓存，避免一个巨量 SDF 撑爆内存
#: （审计建议：SDF 可能含成千上万个分子，整表缓存代价过高）。
# 审计 5.1：原硬编码 50MB 无配置项；改为可通过环境变量 MM_MOL_READ_CACHE_MAX_BYTES
# （单位字节）调整，便于用户按机器内存自定义上限，例如设为 200MB：
#   set MM_MOL_READ_CACHE_MAX_BYTES=209715200

_MOL_READ_CACHE_MAX_BYTES = int(
    os.environ.get("MM_MOL_READ_CACHE_MAX_BYTES", 50 * 1024 * 1024)
)
#: 单文件含分子数超过此值的（典型为上千分子的巨量 SDF）不进读取缓存，仅跳过缓存、正常返回，
#: 避免整表 pybel 分子对象撑爆内存（审计 P-3）。

_MOL_READ_CACHE_MAX_MOLECULES = 200

mol_read_cache: "LRUCache" = LRUCache(maxsize=_MOL_READ_CACHE_MAX)

_OBABEL_CLI_LOCK = threading.Lock()  # 保护 _OBABEL_CLI_EXE 单例初始化

# ======================== 问题三：用户可手动指定 obabel 可执行文件路径 ========================
# 默认空串 = 自动解析（PATH + shutil.which）。
# 用户通过「环境设置 / OpenBabel 路径设置」对话框写入 config["obabel_path"]，
# 这里在 _resolve_obabel_cli 的最开头优先尝试。

_MANUAL_OBABEL_PATH: str | None = None

# OpenBabel 安装建议 / 故障诊断建议（纯文本，不含外部 URL 点击，避免安全弹窗）

OB_INSTALL_GUIDE: str = (
    "━━━━━━━━━━━━  OpenBabel 安装/故障排查指引  ━━━━━━━━━━━━\n"
    "【推荐方式】\n"
    "  1) conda 安装（最稳，CLI + Python 接口都会配好）：\n"
    "       conda install -c conda-forge openbabel\n"
    "  2) Windows 官网安装包：https://github.com/openbabel/openbabel/releases\n"
    "     安装后把 C:\\Program Files\\OpenBabel-3.1.1 加入系统 PATH，再重启程序。\n"
    "  3) pip 安装（成功率较低，只推荐纯 Python 场景）：\n"
    "       pip install openbabel-wheel   # 或 pip install openbabel\n"
    "\n"
    "【本程序提供的修复入口】\n"
    "  • 菜单「帮助 → 环境诊断」可一键检查依赖状态\n"
    "  • 状态栏右下角的「OB 指示灯」（绿/红色圆点）点击可查看详情或手动指定路径\n"
    "  • 直接点击「手动选择 obabel 路径」，浏览选中 obabel.exe（Linux/mac 为 obabel）即可\n"
    "\n"
    "【常见失败原因】\n"
    "  • obabel 没加入系统 PATH，导致自动查找失败\n"
    "  • 旧版 Windows 安装包只配了 GUI，没勾选「Add to PATH」\n"
    "  • conda 环境没激活，导致程序只能看到基础 Python\n"
    "  • 杀毒软件把 obabel.exe 当成可疑程序隔离（恢复白名单即可）\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
)

_OBABEL_CLI_EXE: str | None = None

_DEFAULT_BASE_DIR = None

_COMMON_IN_FORMATS = COMMON_INPUT_FORMATS
