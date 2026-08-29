#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块

重构说明：
  - 移除重复的 _app_data_dir() / _chmod_quiet()，改用 path_utils 中的统一实现
  - 保持所有外部接口不变（load_config 仍返回 dict，save_config 仍接受 dict）
  - 2026-08-16：配置模型改用 pydantic 做类型校验，删除手写 _deep_merge；
    加载时用 ``model_validate`` 自动补默认值 + 校验类型，再 ``model_dump()`` 回 dict，
    因此对外的 dict 契约完全不变，仅新增「非法类型 → 回退默认并告警」的保护。
"""
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from utils.logger import default_logger as logger
from utils.path_utils import chmod_quiet, get_app_data_dir

APP_DATA_DIR = get_app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "mol_manager_config.json"

# ============================================================================
# F18 在线更新检查 —— 更新源地址（决策 Q1）
# ----------------------------------------------------------------------------
# 本项目不是 git 仓库（架构 C15），无法自动推导 release API 地址，因此把更新源
# 做成这个**具名常量**：将来拿到地址，只改这一行（或直接改用户配置文件里的
# "update" -> "repo"）即可生效，**无需改动任何代码逻辑**。
#
# 支持两种填法：
#   1. 简写形式 "owner/repo"      —— 例如 "acme-lab/mol-manager"
#      会自动拼成 https://api.github.com/repos/acme-lab/mol-manager/releases/latest
#   2. 完整 URL "https://…"        —— 例如自建服务返回的 JSON 接口，原样使用
#      期望返回 JSON，字段兼容 GitHub Releases：
#        tag_name / version、body / changelog、html_url / download_url
#
# 🔴 空串 = 未配置更新源。此时 updater.check_update() 会**直接返回 None**，
#    不发起任何网络请求、不打扰用户（架构 §3.4「永不打扰」）。
# ============================================================================
DEFAULT_UPDATE_REPO: str = ""


class _MolManagerConfigBase(BaseModel):
    """所有配置模型的公共基类：允许并保留未知字段（extra="allow"），
    与旧 _deep_merge 的「target 键优先、额外键原样保留」行为对齐，避免丢键。"""
    model_config = ConfigDict(extra="allow")


class Psi4ConfigModel(_MolManagerConfigBase):
    last_method: str = "b3lyp"
    last_basis: str = "6-31g*"
    last_task: str = "energy"


class LogFilterModel(_MolManagerConfigBase):
    # level 见 utils/log_filter.LEVEL_ORDER（ALL/DEBUG/INFO/SUCCESS/WARNING/ERROR/CRITICAL）
    level: str = "INFO"
    keyword: str = ""


class BackupModel(_MolManagerConfigBase):
    # enabled: 总开关；keep_per_type: 每种触发类型保留份数；max_file_mb: 单文件上限(MB)
    enabled: bool = True
    keep_per_type: int = 10
    types: list[str] = Field(default_factory=lambda: ["mapping", "export", "config"])
    max_file_mb: int = 64


class UpdateModel(_MolManagerConfigBase):
    # auto_check: 启动 2s 后静默检查；repo 为空时 check_update 直接返回 None（不发网络包）
    auto_check: bool = True
    repo: str = DEFAULT_UPDATE_REPO
    skipped_version: str = ""


class DndModel(_MolManagerConfigBase):
    # enabled: 拖放导入总开关；extensions: 扩展名白名单（留空 = 不限）
    enabled: bool = True
    extensions: list[str] = Field(
        default_factory=lambda: [".xyz", ".mol", ".sdf", ".pdb", ".cif", ".log", ".out"]
    )


class MolManagerConfigModel(_MolManagerConfigBase):
    """根配置模型：字段与旧 DEFAULT_CONFIG 一一对应，带类型 + 默认值。"""
    work_dir: str = "output"
    mapping_file: str = ""
    ext_filter: str = ".mol,.xyz,.fchk,.out,.inp"
    window_geometry: str = "1000x750"
    psi4_config: Psi4ConfigModel = Field(default_factory=Psi4ConfigModel)
    preview_before_operation: bool = True
    recent_work_dirs: list[str] = Field(default_factory=list)
    # 字体基线（pt，范围 10~20），用户可直接在 json 里改
    font_size: int = 14
    # True=跟随系统 DPI 缩放 font_size，False=按 pt 绝对值
    font_follow_dpi: bool = True
    # OpenBabel 可执行文件路径（空=自动查找）
    obabel_path: str = ""
    ui_mode: str = "simple"          # simple / advanced
    recent_files: list[str] = Field(default_factory=list)   # 最近文件（最多10个）
    preset_auto_load: str = ""       # 自动加载的预设名（空=不自动加载）
    first_run: bool = True           # 由 wizard.py 管理
    queue_concurrency: int = 2       # 任务队列并发度（常驻 worker 池并行线程数）
    descriptor_workers: int = 1      # 批量描述符并发 worker 数（默认1=顺序，零回归）
    log_filter: LogFilterModel = Field(default_factory=LogFilterModel)
    backup: BackupModel = Field(default_factory=BackupModel)
    update: UpdateModel = Field(default_factory=UpdateModel)
    dnd: DndModel = Field(default_factory=DndModel)


# 对外仍暴露 dict 形式的默认配置（drop_handler 等处以 DEFAULT_CONFIG["dnd"]["extensions"] 引用）。
# 由模型生成，保证与 MolManagerConfigModel 永不漂移。
DEFAULT_CONFIG: dict = MolManagerConfigModel().model_dump()

MAX_RECENT_DIRS = 10


def _default_dict() -> dict:
    """返回一份全新的默认配置 dict（深拷贝语义，避免调用方误改 DEFAULT_CONFIG）。"""
    return MolManagerConfigModel().model_dump()


def load_config():
    try:
        if CONFIG_FILE.exists():
            # 先补一次权限（兼容旧版本创建的 0o644 文件）
            chmod_quiet(CONFIG_FILE, 0o600)
            with open(CONFIG_FILE, encoding='utf-8') as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                logger.warning("配置文件格式不是字典，使用默认配置")
                return _default_dict()
            # pydantic 校验 + 自动补默认值（替代旧 _deep_merge）
            return MolManagerConfigModel.model_validate(raw).model_dump()
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("加载配置文件失败，使用默认配置: %s", e)
    except Exception as e:
        # pydantic ValidationError 等：内容非法 → 回退默认，避免启动崩溃
        logger.warning("配置文件校验失败，使用默认配置: %s", e)
    return _default_dict()


def save_config(config):
    tmp_path: Path | None = None
    try:
        # 写入方式采用「先写临时文件→重命名」+ chmod 0o600，
        # 同时避免 (a) 写一半崩溃导致配置损坏 (b) 创建后未立即 chmod 被其他用户读取
        data = config.model_dump() if isinstance(config, BaseModel) else config
        tmp_path = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 先 chmod 再原子替换，防止竞态
        chmod_quiet(tmp_path, 0o600)
        if hasattr(os, 'replace'):
            os.replace(tmp_path, CONFIG_FILE)
        else:
            tmp_path.rename(CONFIG_FILE)
        chmod_quiet(CONFIG_FILE, 0o600)
    except OSError as e:
        logger.warning("保存配置文件失败: %s", e)
    finally:
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "APP_DATA_DIR",
    "CONFIG_FILE",
    "DEFAULT_CONFIG",
    "DEFAULT_UPDATE_REPO",
    "MAX_RECENT_DIRS",
    "MolManagerConfigModel",
    "load_config",
    "save_config",
]
