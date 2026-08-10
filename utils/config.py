#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块

重构说明：
  - 移除重复的 _app_data_dir() / _chmod_quiet()，改用 path_utils 中的统一实现
  - 保持所有外部接口不变
"""
import json
import os
from pathlib import Path

from utils.logger import default_logger as logger
from utils.path_utils import get_app_data_dir, chmod_quiet

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

DEFAULT_CONFIG = {
    "work_dir": "output",
    "mapping_file": "",
    "ext_filter": ".mol,.xyz,.fchk,.out,.inp",
    "window_geometry": "1000x750",
    "psi4_config": {
        "last_method": "b3lyp",
        "last_basis": "6-31g*",
        "last_task": "energy"
    },
    "preview_before_operation": True,
    "recent_work_dirs": [],
    # === 一、字太小：可配置字体基线（pt，默认14pt，立刻见效）===
    # 范围：10 ~ 20。用户可以直接在 mol_manager_config.json 里改。
    "font_size": 14,
    # （可选）强制与系统 DPI 一致地放大（缩放 font_size）。True=跟随DPI，False=按 pt 绝对值
    "font_follow_dpi": True,
    # === 三、OpenBabel 识别失败：用户可手动指定 obabel 可执行文件路径（绝对路径）===
    # 空串 = 自动查找（PATH / shutil.which / 常见安装位置）
    "obabel_path": "",
    # === 易用性改进新增字段 ===
    "ui_mode": "simple",                # simple / advanced
    "recent_files": [],                 # 最近使用的文件路径列表（最多10个）
    "preset_auto_load": "",             # 自动加载的预设名（空表示不自动加载）
    # first_run 由 wizard.py 管理
    "first_run": True,

    # === 任务队列并发度（Phase 5 增强）：常驻 worker 池的并行线程数，下拉实时调整 ===
    "queue_concurrency": 2,

    # ==================== Phase 1 新增配置组（架构 §5）====================
    # ⚠️ 全部通过 _deep_merge 向后兼容：老配置文件缺这些键时自动补默认值。

    # --- F15 日志过滤 ---
    # level: 见 utils/log_filter.LEVEL_ORDER（ALL/DEBUG/INFO/SUCCESS/WARNING/ERROR/CRITICAL）
    # ⚠️ 命名避让：这里是「日志面板」过滤，与文件列表过滤（ext_filter / filter_keyword_var）
    #    完全无关，UI 侧变量一律用 log_filter_* 前缀（架构 C8）。
    "log_filter": {
        "level": "INFO",
        "keyword": "",
    },

    # --- F17 自动备份 ---
    # enabled:        总开关，关掉后 BackupManager.create_snapshot 直接返回 None
    # keep_per_type:  每种触发类型保留的快照份数，超出自动清理
    # types:          启用快照的触发类型（Q2 决策：本期不含 PSI4 计算输出）
    # max_file_mb:    单文件备份体积上限（MB），超限跳过并记 WARNING
    "backup": {
        "enabled": True,
        "keep_per_type": 10,
        "types": ["mapping", "export", "config"],
        "max_file_mb": 64,
    },

    # --- F18 在线更新检查（T13-T16 已实现）---
    # auto_check:      启动 2s 后静默检查一次（用户明确要求默认开启）。
    #                  ⚠️ 开着也不会打扰用户：repo 为空时 check_update() 直接返回
    #                  None，一个网络包都不发；断网 / 超时 / 限流一律静默失败。
    # repo:            更新源，见文件顶部 DEFAULT_UPDATE_REPO 的填法说明。
    # skipped_version: 用户点过「跳过此版本」的版本号，该版本不再提示。
    "update": {
        "auto_check": True,
        "repo": DEFAULT_UPDATE_REPO,
        "skipped_version": "",
    },

    # --- F06 拖放导入（T17-T18 已实现）---
    # enabled:    总开关。关掉后拖入 / 菜单导入都会被 controller 直接拒绝。
    # extensions: 扩展名白名单，不在名单内的文件会被 drop_handler 拒绝并给出原因。
    #             留空列表 = 不做扩展名限制（放行所有文件）。
    "dnd": {
        "enabled": True,
        "extensions": [".xyz", ".mol", ".sdf", ".pdb", ".cif", ".log", ".out"],
    },
}

MAX_RECENT_DIRS = 10


def _deep_merge(target: dict, defaults: dict) -> dict:
    """递归合并 defaults 到 target（target 中的键优先），返回 target。"""
    for key, def_val in defaults.items():
        cur = target.get(key)
        if isinstance(def_val, dict):
            if not isinstance(cur, dict):
                target[key] = def_val.copy()
            else:
                _deep_merge(cur, def_val)
        elif cur is None:
            target[key] = def_val
    return target


def load_config():
    try:
        if CONFIG_FILE.exists():
            # 先补一次权限（兼容旧版本创建的 0o644 文件）
            chmod_quiet(CONFIG_FILE, 0o600)
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if not isinstance(config, dict):
                    logger.warning("配置文件格式不是字典，使用默认配置")
                    return DEFAULT_CONFIG.copy()
                return _deep_merge(config, DEFAULT_CONFIG)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("加载配置文件失败，使用默认配置: %s", e)
    return DEFAULT_CONFIG.copy()


def save_config(config):
    tmp_path: Path | None = None
    try:
        # 写入方式采用「先写临时文件→重命名」+ chmod 0o600，
        # 同时避免 (a) 写一半崩溃导致配置损坏 (b) 创建后未立即 chmod 被其他用户读取
        tmp_path = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
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
    "load_config",
    "save_config",
]
