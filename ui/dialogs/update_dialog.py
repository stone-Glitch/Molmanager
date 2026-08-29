#!/usr/bin/env python3
"""
F18 新版本提示对话框（T15 / Phase 1 批次二）
──────────────────────────────────────────
两个入口，反馈强度**刻意不同**：

  1. 启动静默检查（`core/view.MainView` 的 after(2000)）
     —— 只有「确实有新版本」才弹窗；无更新 / 断网 / 未配置源时**完全无声**。
  2. 菜单「⚙️ 设置 → 🔄 检查更新」手动检查
     —— 无论结果如何都必须给明确反馈，否则用户会以为按钮坏了：
        · 有更新   → 本对话框
        · 无更新   → "已是最新版本" 提示框
        · 检查失败 → 说明原因（未配置源 / 缺 requests / 网络不可达）

界面结构：
    ┌ 标题：🎉 发现新版本
    ├ 版本对比：当前 1.0.0  →  最新 1.2.0（发布于 …）
    ├ 更新说明：只读 ScrolledText（Markdown 原文直出，不做渲染）
    └ 按钮：[🌐 前往下载]  [⏰ 稍后提醒]  [🚫 跳过此版本]

契约：
  - 所有对话框函数都吞异常，绝不把错误冒泡到 Tk 事件循环；
  - 「跳过此版本」写入 `update.skipped_version`（经 utils/updater 落盘）；
  - 「前往下载」用系统默认浏览器打开，打不开时降级为"复制链接"提示。
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from utils import updater
from utils import version as ver
from utils.dialog_geom import fit_dialog_geometry
from utils.logger import default_logger as logger

#: 按钮返回值
ACTION_DOWNLOAD = "download"
ACTION_LATER = "later"
ACTION_SKIP = "skip"


def _log(app: Any, msg: str, level: str = "info") -> None:
    """统一日志出口：优先走主界面日志面板，失败退回 logger（与 backup_dialog 一致）。"""
    try:
        helpers = getattr(app, "helpers", None)
        if helpers is not None and hasattr(helpers, "on_log"):
            helpers.on_log(msg, level)
            return
    except Exception:
        pass
    try:
        getattr(logger, "warning" if level in ("warning", "error") else "info")(msg)
    except Exception:
        pass


def _config_of(app: Any) -> dict | None:
    """取主窗口的 config_data（拿不到时返回 None，updater 会自行 load_config）。"""
    cfg = getattr(app, "config_data", None)
    return cfg if isinstance(cfg, dict) else None


# ============================================================================
# 主对话框：发现新版本
# ============================================================================


def show_update_dialog(app: Any, info: Any, *, manual: bool = False) -> str:
    """
    显示「发现新版本」对话框，返回用户选择的动作常量。

    参数:
        app:    主窗口（Toplevel 的 parent）
        info:   `updater.UpdateInfo`
        manual: 是否由用户手动触发（仅影响文案，不影响按钮）

    返回:
        ACTION_DOWNLOAD / ACTION_LATER / ACTION_SKIP。
        构建失败时返回 ACTION_LATER（等价于"什么也没做"）。
    """
    try:
        return _build_update_dialog(app, info, manual=manual)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ 打开更新提示对话框失败: %s", exc)
        return ACTION_LATER


def _build_update_dialog(app: Any, info: Any, *, manual: bool) -> str:
    if info is None:
        return ACTION_LATER

    remote_version = str(getattr(info, "version", "") or "")
    local_version = str(getattr(info, "current_version", "") or ver.get_version())
    changelog = str(getattr(info, "changelog", "") or "")
    download_url = str(getattr(info, "download_url", "") or "")
    published_at = str(getattr(info, "published_at", "") or "")
    title_text = str(getattr(info, "title", "") or "")
    prerelease = bool(getattr(info, "prerelease", False))

    result = {"action": ACTION_LATER}

    dialog = tk.Toplevel(app)
    dialog.title("🎉 发现新版本")
    dialog.geometry(fit_dialog_geometry(dialog, 640, 520, min_w=520, min_h=400))
    dialog.resizable(True, True)
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass

    # ---------------------------------------------------------- 顶部：版本对比
    header = ttk.Frame(dialog, padding=(14, 12, 14, 6))
    header.pack(fill=tk.X)

    ttk.Label(
        header,
        text="🎉 发现新版本" + ("（预发布）" if prerelease else ""),
        font=("Microsoft YaHei UI", 13, "bold"),
    ).pack(anchor=tk.W)

    ttk.Label(
        header,
        text=f"当前版本：{local_version}      →      最新版本：{remote_version}",
        font=("Microsoft YaHei UI", 11),
    ).pack(anchor=tk.W, pady=(6, 0))

    meta_bits = []
    if title_text and title_text != remote_version:
        meta_bits.append(title_text)
    if published_at:
        meta_bits.append(f"发布于 {published_at[:10]}")
    if meta_bits:
        ttk.Label(header, text="　".join(meta_bits), foreground="#5A6785").pack(anchor=tk.W, pady=(2, 0))

    # ---------------------------------------------------------- 中部：更新说明
    body = ttk.LabelFrame(dialog, text="📋 更新说明", padding=6)
    body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 6))

    text_widget = scrolledtext.ScrolledText(body, wrap=tk.WORD, height=12, relief=tk.FLAT, borderwidth=0)
    text_widget.pack(fill=tk.BOTH, expand=True)
    text_widget.insert(
        tk.END,
        changelog.strip() if changelog.strip() else "（本次发布未提供更新说明）",
    )
    # 只读但仍可选中复制：用 state=disabled 而不是 takefocus=0
    text_widget.configure(state=tk.DISABLED)

    # ---------------------------------------------------------- 底部：按钮行
    footer = ttk.Frame(dialog, padding=(14, 4, 14, 12))
    footer.pack(fill=tk.X)

    hint = ttk.Label(
        footer,
        text="提示：「跳过此版本」后，启动时不再提示该版本；手动检查仍会显示。",
        foreground="#5A6785",
    )
    hint.pack(side=tk.LEFT, anchor=tk.W)

    def _close(action: str) -> None:
        result["action"] = action
        try:
            dialog.grab_release()
        except Exception:
            pass
        try:
            dialog.destroy()
        except Exception:
            pass

    def _on_download() -> None:
        opened = False
        if download_url:
            try:
                opened = bool(webbrowser.open(download_url))
            except Exception as exc:  # noqa: BLE001
                logger.debug("打开下载页失败: %s", exc)
                opened = False
        if opened:
            _log(app, f"🌐 已在浏览器中打开下载页：{download_url}", "info")
        else:
            # 降级：给出可复制的地址，别让用户卡在"点了没反应"
            try:
                messagebox.showinfo(
                    "请手动打开下载地址",
                    f"无法自动唤起浏览器，请手动复制以下地址：\n\n{download_url or '（本次发布未提供下载地址）'}",
                    parent=dialog,
                )
            except Exception:
                pass
            if download_url:
                _log(app, f"ℹ️ 请手动打开下载地址：{download_url}", "info")
        _close(ACTION_DOWNLOAD)

    def _on_later() -> None:
        _log(app, f"⏰ 已稍后提醒：新版本 {remote_version}", "info")
        _close(ACTION_LATER)

    def _on_skip() -> None:
        ok = False
        try:
            ok = updater.mark_version_skipped(_config_of(app), remote_version)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 写入跳过版本失败: %s", exc)
        if ok:
            _log(app, f"🚫 已跳过版本 {remote_version}，启动时不再提示", "info")
        else:
            _log(app, f"⚠️ 跳过版本 {remote_version} 的设置未能保存（本次仍不再提示）", "warning")
        _close(ACTION_SKIP)

    ttk.Button(footer, text="🚫 跳过此版本", command=_on_skip, width=14).pack(side=tk.RIGHT, padx=(6, 0))
    ttk.Button(footer, text="⏰ 稍后提醒", command=_on_later, width=12).pack(side=tk.RIGHT, padx=(6, 0))
    ttk.Button(footer, text="🌐 前往下载", command=_on_download, width=12).pack(side=tk.RIGHT, padx=(6, 0))

    dialog.protocol("WM_DELETE_WINDOW", lambda: _close(ACTION_LATER))
    dialog.bind("<Escape>", lambda _e: _close(ACTION_LATER))

    try:
        app.wait_window(dialog)
    except Exception:
        pass
    return result["action"]


# ============================================================================
# 手动检查的其余两种反馈
# ============================================================================


def show_no_update_dialog(app: Any, current_version: str | None = None) -> None:
    """手动检查且**已是最新**时的反馈。静默检查绝不调用此函数。"""
    version_text = str(current_version or ver.get_version())
    try:
        messagebox.showinfo(
            "检查更新",
            f"✅ 当前已是最新版本。\n\n版本号：{version_text}",
            parent=app,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("显示「已是最新版本」提示失败: %s", exc)


def show_check_failed_dialog(app: Any, reason: str = "") -> None:
    """
    手动检查**失败**时的反馈（未配置更新源 / 缺 requests / 网络不可达）。

    静默检查绝不调用此函数 —— 架构 §9 明确要求"断网状态下启动无任何弹窗"。
    """
    detail = (reason or "").strip()
    if not detail:
        detail = (
            "未能连接到更新服务器。\n\n"
            "可能原因：\n"
            "  · 当前处于离线状态或网络受限\n"
            "  · 更新服务器暂时不可用 / 请求被限流\n"
            "  · 系统代理设置导致请求被拦截\n\n"
            "这不影响任何本地功能，稍后再试即可。"
        )
    try:
        messagebox.showinfo("检查更新", detail, parent=app)
    except Exception as exc:  # noqa: BLE001
        logger.debug("显示更新检查失败提示失败: %s", exc)


# ============================================================================
# 统一分发入口 —— view / controller 只需调这一个
# ============================================================================


def notify_update_result(app: Any, info: Any, *, manual: bool = False) -> str:
    """
    根据检查结果给出恰当的反馈，返回用户动作（无更新时返回 ACTION_LATER）。

    这是 **F18 唯一的 UI 分发点**：静默 / 手动两条路径的差异全部收敛在这里，
    调用方（view 的 after(2000) 回调、菜单项回调）不需要各写一遍 if/else。
    """
    try:
        if info is not None:
            return show_update_dialog(app, info, manual=manual)
        if not manual:
            return ACTION_LATER  # 🔴 静默路径：无更新 = 完全无声
        reason = ""
        try:
            reason = updater.describe_unavailable_reason(_config_of(app))
        except Exception as exc:  # noqa: BLE001
            logger.debug("获取更新诊断说明失败: %s", exc)
        if reason:
            show_check_failed_dialog(app, reason)
        else:
            show_no_update_dialog(app, ver.get_version())
        return ACTION_LATER
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ 处理更新检查结果失败: %s", exc)
        return ACTION_LATER


__all__ = [
    "ACTION_DOWNLOAD",
    "ACTION_LATER",
    "ACTION_SKIP",
    "show_update_dialog",
    "show_no_update_dialog",
    "show_check_failed_dialog",
    "notify_update_result",
]
