#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U-06 简易/专家模式切换（纯逻辑层）

MolManager 已在 config 中提供 ``ui_mode``（"simple" / "advanced"）。
本模块把「某功能在哪种模式下可见」集中成一张声明式登记表，
供 UI 在渲染前统一裁决，避免散落的 if/else。

纯逻辑、无 tkinter 依赖，可在沙箱单测。
"""
from typing import Dict, Iterable, List


# 仅专家(advanced)模式可见的功能；simple 模式默认隐藏。
# 未列出的功能在两种模式下都可见。
ADVANCED_ONLY: frozenset = frozenset({
    "psi4_expert_options",   # PSI4 高级参数（内存/网格/D3/自定义关键词）
    "rule_engine",           # E-03 智能规则引擎
    "hpc_script_generator",  # E-09 HPC 作业脚本生成
    "cli_batch_mode",        # E-08 CLI 无头批处理
    "project_pack",          # E-05 项目打包/导出
    "metadata_columns",      # E-01 动态元数据列
    "log_parser",            # E-07 多程序日志解析
    "file_association",      # E-06 反向追溯
    "concurrency_settings",  # 队列/描述符并发度下拉
    "backup_snapshots",      # 自动备份快照管理
})


def feature_visible(ui_mode: str, feature: str) -> bool:
    """
    判断某功能在给定 UI 模式下是否可见。

    - ui_mode 不是 "advanced" 时，ADVANCED_ONLY 内的功能一律 False。
    - 其余功能两种模式都 True。
    """
    if feature in ADVANCED_ONLY and ui_mode != "advanced":
        return False
    return True


def resolve_features(ui_mode: str, features: Iterable[str]) -> Dict[str, bool]:
    """批量裁决一组功能的可见性，返回 {feature: bool}。"""
    return {f: feature_visible(ui_mode, f) for f in features}


def visible_features(ui_mode: str, features: Iterable[str]) -> List[str]:
    """返回在给定模式下可见的功能名列表（稳定顺序）。"""
    return [f for f in features if feature_visible(ui_mode, f)]


def is_advanced_mode(ui_mode: str) -> bool:
    return ui_mode == "advanced"


__all__ = ["ADVANCED_ONLY", "feature_visible", "resolve_features",
           "visible_features", "is_advanced_mode"]
