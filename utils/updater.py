#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在线更新检查（T14 / F18 · Phase 1 批次二）
──────────────────────────────────────────
核心契约（架构 §3.4「永不打扰」）：

    check_update() **永不抛异常**。
    以下情况一律静默返回 None，不写 WARNING、不弹窗、不阻塞：
      · repo 未配置（空串）—— 且**一个网络包都不发**
      · auto_check=False 且非强制检查
      · requests 未安装
      · 断网 / DNS 失败 / 连接超时 / 读取超时 / 代理不可达
      · HTTP 403（GitHub API 限流）/ 404（仓库不存在）/ 5xx
      · 响应体不是 JSON / JSON 结构不符合预期
      · 远端版本 <= 本地版本
      · 远端版本已被用户「跳过此版本」

更新源支持两种形态（决策 Q1，见 utils/config.DEFAULT_UPDATE_REPO）：
  1. "owner/repo"  → 自动拼 https://api.github.com/repos/{owner}/{repo}/releases/latest
  2. 完整 http(s) URL → 原样请求，期望返回 GitHub Releases 兼容的 JSON

约束（架构 §6）：
  - 本模块**无 Tk 依赖**，可脱离 GUI 单测；
  - 本模块**不 import requests**，所有网络请求经 utils/net.py（§6.3 网络唯一入口）；
  - 版本比较统一走 utils/version.py（packaging 缺失时自动降级为元组比较）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from utils import net
from utils import version as ver
from utils.logger import default_logger as logger


# ---------------------------------------------------------------- 常量

#: GitHub Releases API 模板（"owner/repo" 简写形式会拼成这个地址）。
GITHUB_LATEST_TEMPLATE: str = "https://api.github.com/repos/{owner}/{repo}/releases/latest"

#: GitHub API 推荐的 Accept 头（锁定 v3 响应结构，避免将来默认版本变更导致解析失败）。
GITHUB_ACCEPT: str = "application/vnd.github+json"

#: "owner/repo" 简写形式的合法字符集。
_REPO_SHORTHAND_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/"
                                r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

#: changelog 展示上限（字符）。超长的 release note 截断，避免撑爆对话框。
MAX_CHANGELOG_CHARS: int = 8000

#: 更新检查用的读取超时可以比默认更短——它是"锦上添花"，不值得让用户等。
CHECK_TIMEOUT = (net.CONNECT_TIMEOUT, net.READ_TIMEOUT)


# ---------------------------------------------------------------- 数据结构

@dataclass
class UpdateInfo:
    """一次成功的更新检查结果。所有字段都保证是 str/bool，不会是 None。"""

    version: str = ""                 # 远端版本号（已规范化，如 "1.2.0"）
    current_version: str = ""         # 本地版本号
    raw_tag: str = ""                 # 远端原始 tag（如 "v1.2.0"）
    title: str = ""                   # release 标题
    changelog: str = ""               # 更新说明正文
    download_url: str = ""            # 下载 / release 页面 URL
    published_at: str = ""            # 发布时间（原样字符串）
    prerelease: bool = False          # 是否预发布版本
    source_url: str = ""              # 本次请求的 API 地址（排查问题用）
    assets: list[dict[str, str]] = field(default_factory=list)  # 可选的资产列表

    def summary_line(self) -> str:
        """一行摘要，供日志 / 状态栏使用。"""
        return f"发现新版本 {self.version}（当前 {self.current_version}）"

    def has_changelog(self) -> bool:
        return bool(self.changelog.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "current_version": self.current_version,
            "raw_tag": self.raw_tag,
            "title": self.title,
            "changelog": self.changelog,
            "download_url": self.download_url,
            "published_at": self.published_at,
            "prerelease": self.prerelease,
            "source_url": self.source_url,
        }


# ---------------------------------------------------------------- 配置读写

def get_update_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    从整份 config 中取出 "update" 子字典（永远返回 dict，绝不返回 None）。

    传 None 时会自行 `load_config()`；缺键时回落到 DEFAULT_CONFIG 的默认值。
    """
    cfg: dict[str, Any]
    if isinstance(config, dict):
        cfg = config
    else:
        try:
            from utils.config import load_config
            cfg = load_config()
        except Exception as exc:  # noqa: BLE001
            logger.debug("更新检查读取配置失败（使用默认值）: %s", exc)
            cfg = {}
    node = cfg.get("update")
    if not isinstance(node, dict):
        node = {}
    try:
        from utils.config import DEFAULT_UPDATE_REPO
    except Exception:  # pragma: no cover
        DEFAULT_UPDATE_REPO = ""
    return {
        "auto_check": bool(node.get("auto_check", True)),
        "repo": str(node.get("repo", DEFAULT_UPDATE_REPO) or ""),
        "skipped_version": str(node.get("skipped_version", "") or ""),
    }


def _persist(config: dict[str, Any] | None, key: str, value: Any) -> bool:
    """
    把 update.<key> 写回 config 并落盘。返回是否成功。

    config 为 None 时先 load 再存（此路径少用；GUI 应该传 app.config_data，
    这样内存态与磁盘态才不会分叉）。任何失败只记 WARNING 并返回 False。
    """
    try:
        from utils.config import load_config, save_config
        cfg = config if isinstance(config, dict) else load_config()
        node = cfg.get("update")
        if not isinstance(node, dict):
            node = {}
            cfg["update"] = node
        node[key] = value
        save_config(cfg)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ 保存更新设置失败（update.%s）: %s", key, exc)
        return False


def mark_version_skipped(config: dict[str, Any] | None, version: Any) -> bool:
    """记录「跳过此版本」。之后的**静默检查**不再提示该版本（手动检查仍会提示）。"""
    normalized = ver.normalize_version(version)
    if not normalized:
        return False
    return _persist(config, "skipped_version", normalized)


def clear_skipped_version(config: dict[str, Any] | None = None) -> bool:
    """清除跳过标记（用户想重新收到该版本提示时使用）。"""
    return _persist(config, "skipped_version", "")


def set_auto_check(config: dict[str, Any] | None, enabled: bool) -> bool:
    """开 / 关启动时的静默更新检查。"""
    return _persist(config, "auto_check", bool(enabled))


def is_version_skipped(config: dict[str, Any] | None, version: Any) -> bool:
    """给定版本是否已被用户跳过。"""
    upd = get_update_config(config)
    skipped = ver.normalize_version(upd.get("skipped_version", ""))
    target = ver.normalize_version(version)
    return bool(skipped) and bool(target) and skipped == target


# ---------------------------------------------------------------- 更新源解析

def normalize_repo(raw: Any) -> str:
    """
    规范化 repo 配置值。

        "owner/repo"                                  -> "owner/repo"
        " OWNER/repo/ "                               -> "OWNER/repo"
        "https://github.com/owner/repo"               -> "owner/repo"
        "https://github.com/owner/repo/releases"      -> "owner/repo"
        "https://example.com/api/latest.json"         -> 原样返回（完整 URL 模式）
        ""/None/垃圾输入                                -> ""

    永不抛异常。
    """
    if raw is None:
        return ""
    try:
        text = str(raw).strip()
    except Exception:
        return ""
    if not text:
        return ""

    # 完整 URL：github.com 的网页地址转成简写，其余原样保留
    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        marker = "github.com/"
        idx = lowered.find(marker)
        if idx >= 0 and "api.github.com" not in lowered:
            tail = text[idx + len(marker):].strip("/")
            segs = [s for s in tail.split("/") if s]
            if len(segs) >= 2:
                candidate = f"{segs[0]}/{segs[1]}"
                if candidate.endswith(".git"):
                    candidate = candidate[:-4]
                if _REPO_SHORTHAND_RE.match(candidate):
                    return candidate
        return text  # 自建接口 / api.github.com 完整地址：原样使用

    # 简写形式
    text = text.strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if _REPO_SHORTHAND_RE.match(text):
        return text
    logger.debug("更新源配置无法识别（已忽略）: %r", text[:120])
    return ""


def build_release_url(repo: Any) -> str:
    """
    把规范化后的 repo 转成实际请求的 URL。无法转换时返回空串。

        "owner/repo"       -> "https://api.github.com/repos/owner/repo/releases/latest"
        "https://…"        -> 原样返回
        ""                 -> ""
    """
    normalized = normalize_repo(repo)
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return normalized
    try:
        owner, name = normalized.split("/", 1)
    except ValueError:
        return ""
    if not owner or not name:
        return ""
    return GITHUB_LATEST_TEMPLATE.format(owner=owner, repo=name)


# ---------------------------------------------------------------- 响应解析

def _first_str(data: dict[str, Any], *keys: str) -> str:
    """按顺序取第一个非空字符串字段，全空则返回 ""。"""
    for key in keys:
        try:
            value = data.get(key)
        except Exception:
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _extract_assets(data: dict[str, Any]) -> list[dict[str, str]]:
    """提取 release 的下载资产列表（GitHub 结构）；异常一律返回空列表。"""
    out: list[dict[str, str]] = []
    try:
        raw_assets = data.get("assets")
        if not isinstance(raw_assets, list):
            return out
        for item in raw_assets[:20]:
            if not isinstance(item, dict):
                continue
            name = _first_str(item, "name")
            url = _first_str(item, "browser_download_url", "url")
            if name and url:
                out.append({"name": name, "url": url})
    except Exception:  # noqa: BLE001
        return []
    return out


def parse_release_payload(data: Any, *, source_url: str = "") -> UpdateInfo | None:
    """
    把 release JSON 解析成 `UpdateInfo`。

    兼容 GitHub Releases 与自建接口：
        版本号   tag_name / version / name
        更新说明 body / changelog / notes / description
        下载地址 html_url / download_url / url

    草稿（draft=True）直接忽略。解析不出版本号时返回 None。**永不抛异常。**
    """
    try:
        # GitHub 的 /releases（复数）返回列表；容错地取第一个非草稿项
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and not bool(item.get("draft", False)):
                    data = item
                    break
            else:
                return None
        if not isinstance(data, dict):
            return None
        if bool(data.get("draft", False)):
            logger.debug("远端最新 release 是草稿，已忽略")
            return None

        raw_tag = _first_str(data, "tag_name", "version", "name")
        remote_version = ver.normalize_version(raw_tag)
        if not remote_version:
            logger.debug("远端响应中找不到可解析的版本号，已忽略")
            return None

        changelog = _first_str(data, "body", "changelog", "notes", "description")
        if len(changelog) > MAX_CHANGELOG_CHARS:
            changelog = changelog[:MAX_CHANGELOG_CHARS] + "\n\n…（更新说明过长，已截断）"

        return UpdateInfo(
            version=remote_version,
            current_version=ver.get_version(),
            raw_tag=raw_tag,
            title=_first_str(data, "name", "title"),
            changelog=changelog,
            download_url=_first_str(data, "html_url", "download_url", "url"),
            published_at=_first_str(data, "published_at", "created_at", "date"),
            prerelease=bool(data.get("prerelease", False)),
            source_url=source_url,
            assets=_extract_assets(data),
        )
    except Exception as exc:  # noqa: BLE001 - 契约：解析失败也只返回 None
        logger.debug("解析更新响应失败（已静默忽略）: %s", exc)
        return None


# ---------------------------------------------------------------- 主入口

def check_update(
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
    respect_skip: bool | None = None,
    timeout: Any = None,
) -> UpdateInfo | None:
    """
    检查是否有新版本。**契约：永不抛异常，失败一律返回 None。**

    参数:
        config:       整份配置字典（GUI 请传 `app.config_data`，保证内存/磁盘一致）。
                      为 None 时自行 load_config()。
        force:        True = 用户手动点「检查更新」。此时忽略 `auto_check` 开关，
                      并且默认**不**过滤已跳过的版本（用户主动问就该如实回答）。
        respect_skip: 显式覆盖「是否过滤已跳过版本」。默认 `not force`。
        timeout:      透传给 utils.net，None 表示用默认 (3s, 5s)。

    返回:
        有新版本 → `UpdateInfo`；其余所有情况 → `None`。
    """
    try:
        upd = get_update_config(config)

        # ---- 1) 开关：静默检查必须尊重 auto_check ----
        if not force and not upd.get("auto_check", True):
            logger.debug("自动更新检查已关闭，跳过")
            return None

        # ---- 2) 🔴 repo 为空 → 直接返回，不发起任何网络请求（决策 Q1）----
        url = build_release_url(upd.get("repo", ""))
        if not url:
            logger.debug("未配置更新源（update.repo 为空），跳过更新检查（未发起网络请求）")
            return None

        # ---- 3) requests 缺失 → 静默降级 ----
        if not net.is_available():
            logger.debug("requests 不可用，跳过更新检查")
            return None

        # ---- 4) 发请求（net 层已保证不抛）----
        data = net.get_json(
            url,
            headers={"Accept": GITHUB_ACCEPT},
            timeout=timeout if timeout is not None else CHECK_TIMEOUT,
        )
        if data is None:
            logger.debug("更新检查未取得有效响应（离线 / 超时 / 限流），静默放弃")
            return None

        info = parse_release_payload(data, source_url=url)
        if info is None:
            return None

        # ---- 5) 版本比较（packaging 缺失时自动降级为元组比较）----
        if not ver.is_newer(info.version, info.current_version):
            logger.debug("已是最新版本 %s（远端 %s）", info.current_version, info.version)
            return None

        # ---- 6) 「跳过此版本」----
        skip = (not force) if respect_skip is None else bool(respect_skip)
        if skip:
            skipped = ver.normalize_version(upd.get("skipped_version", ""))
            if skipped and skipped == ver.normalize_version(info.version):
                logger.debug("版本 %s 已被用户跳过，不再提示", info.version)
                return None

        logger.info("🔔 %s", info.summary_line())
        return info
    except Exception as exc:  # noqa: BLE001 - 最外层兜底，确保"永不抛"
        logger.debug("更新检查异常（已静默吞掉）: %s", exc)
        return None


def describe_unavailable_reason(config: dict[str, Any] | None = None) -> str:
    """
    给「手动检查更新」用的诊断说明：解释这次为什么没能真正联网检查。

    返回空串表示"配置正常，可以正常检查"。本函数不发起任何网络请求。
    """
    try:
        upd = get_update_config(config)
        if not build_release_url(upd.get("repo", "")):
            return (
                "尚未配置更新源。\n\n"
                "请在配置文件的 update.repo 中填写：\n"
                "  · 简写形式：owner/repo\n"
                "  · 或完整的 releases JSON 接口地址\n\n"
                "填好后无需重启即可在下次检查时生效。"
            )
        if not net.is_available():
            reason = net.unavailable_reason()
            return (
                "缺少网络组件 requests，无法联网检查更新。\n\n"
                "安装方式：pip install requests\n"
                + (f"\n详细原因：{reason}" if reason else "")
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("生成更新诊断说明失败: %s", exc)
    return ""


__all__ = [
    "GITHUB_LATEST_TEMPLATE",
    "MAX_CHANGELOG_CHARS",
    "UpdateInfo",
    "get_update_config",
    "normalize_repo",
    "build_release_url",
    "parse_release_payload",
    "check_update",
    "mark_version_skipped",
    "clear_skipped_version",
    "set_auto_check",
    "is_version_skipped",
    "describe_unavailable_reason",
]
