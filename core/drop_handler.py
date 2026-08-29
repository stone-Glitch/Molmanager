#!/usr/bin/env python3
"""
拖放路径路由（T17 / F06 · Phase 1 批次二）
──────────────────────────────────────────
职责（架构 §3.2）：把「一堆用户丢过来的路径」变成「一份可以安全导入的文件清单」。

    路径分类（文件 / 目录）
      → 目录递归展开（深度受限、跳过受保护目录）
      → 扩展名白名单过滤（读 config 的 dnd.extensions）
      → 去重（按真实路径归一化）
      → 拒绝受保护目录 / 符号链接 / 越权路径
      → 返回结构化结果 DropResult(accepted, rejected)

🔴 为什么要独立成模块（架构 §2.1 注）：
    **正常拖放路径与 tkdnd 缺失时的"菜单导入"降级路径共用同一套逻辑。**
    两条入口如果各写一份校验，迟早会出现"拖进去被拦住、从菜单进去却漏过"的
    安全不一致。所有校验只此一份。

约束（架构 §6）：
  - 本模块**无 Tk 依赖**（连 import tkinter 都没有），可脱离 GUI 单测；
  - 本模块**不 import requests**、**不 import chem.psi4**；
  - 本模块**只读不写**：不复制、不移动任何文件（落盘交给 model.import_external_files）。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 常量

#: 默认扩展名白名单（与 utils/config.DEFAULT_CONFIG["dnd"]["extensions"] 保持一致）。
DEFAULT_EXTENSIONS: tuple[str, ...] = (
    ".xyz",
    ".mol",
    ".sdf",
    ".pdb",
    ".cif",
    ".log",
    ".out",
)

#: 🔴 受保护目录名单。必须与 core/model.PROTECTED_DIR_NAMES 一致。
#:   这里**刻意不 import core.model**：drop_handler 要保持零依赖可单测，
#:   而 model 会连带拉起 openbabel / psi4 等重量级模块。
#:   一致性由下方的 `assert_protected_names_in_sync()` 与全局审查共同保证。
PROTECTED_DIR_NAMES: frozenset[str] = frozenset({".trash_backup", ".backup", ".preview"})

#: 单次拖放接受的最大文件数（防止误拖 C:\ 根目录把界面拖死）。
DEFAULT_MAX_FILES: int = 2000

#: 目录递归的最大深度（相对于被拖入的目录本身）。
DEFAULT_MAX_DEPTH: int = 8

# ---- 拒绝原因（对用户可见的中文短语，直接用于日志与汇总）----
REASON_NOT_FOUND = "路径不存在"
REASON_PROTECTED = "位于受保护的备份目录内"
REASON_SYMLINK = "是符号链接 / Junction"
REASON_EXTENSION = "扩展名不在白名单内"
REASON_DUPLICATE = "重复路径"
REASON_EMPTY_DIR = "目录内没有符合条件的文件"
REASON_LIMIT = "超出单次导入数量上限"
REASON_UNREADABLE = "无法访问"
REASON_ALREADY_HERE = "已在工作目录中"
REASON_INVALID = "无效路径"


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class RejectedItem:
    """一条被拒绝的路径 + 原因。`detail` 给出可选的补充说明。"""

    path: str = ""
    reason: str = REASON_INVALID
    detail: str = ""

    def describe(self) -> str:
        """ "<文件名>（原因）" 形式的一行说明。"""
        name = os.path.basename(self.path.rstrip("\\/")) or self.path
        text = f"{name}（{self.reason}"
        if self.detail:
            text += f"：{self.detail}"
        return text + "）"


@dataclass
class DropResult:
    """一次拖放/导入的结构化结果。"""

    accepted: list[Path] = field(default_factory=list)
    rejected: list[RejectedItem] = field(default_factory=list)
    scanned_dirs: int = 0
    truncated: bool = False  # 是否因为触达 max_files 上限而截断

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def has_accepted(self) -> bool:
        return bool(self.accepted)

    def accepted_names(self) -> list[str]:
        return [p.name for p in self.accepted]

    def rejected_by_reason(self) -> dict[str, list[RejectedItem]]:
        """按原因分组，便于日志里"同类合并"输出，而不是刷 200 行。"""
        grouped: dict[str, list[RejectedItem]] = {}
        for item in self.rejected:
            grouped.setdefault(item.reason, []).append(item)
        return grouped

    def summary(self) -> str:
        """一行摘要，供日志面板 / 确认框标题使用。"""
        parts = [f"可导入 {self.accepted_count} 个"]
        if self.rejected_count:
            parts.append(f"已忽略 {self.rejected_count} 个")
        if self.truncated:
            parts.append("（已达数量上限，列表被截断）")
        return "，".join(parts)

    def rejection_lines(self, limit: int = 6) -> list[str]:
        """
        生成"原因 → 数量 + 示例"的紧凑说明行，最多 `limit` 行。

        用于日志与确认框：一次拖 500 个不合规文件时，用户需要的是
        "480 个扩展名不在白名单内（如 a.txt、b.docx…）"，而不是 480 行刷屏。
        """
        lines: list[str] = []
        for reason, items in self.rejected_by_reason().items():
            samples = [os.path.basename(i.path.rstrip("\\/")) or i.path for i in items[:3]]
            sample_text = "、".join(s for s in samples if s)
            line = f"{reason}：{len(items)} 个"
            if sample_text:
                line += f"（如 {sample_text}{'…' if len(items) > 3 else ''}）"
            lines.append(line)
            if len(lines) >= max(1, limit):
                break
        return lines


# ---------------------------------------------------------------- 工具函数


def normalize_extensions(extensions: Any) -> tuple[str, ...]:
    """
    规范化扩展名白名单：统一小写、统一补前导点、去空去重。

        [".XYZ", "mol", "", None, ".xyz"]  ->  (".xyz", ".mol")
        None                                ->  DEFAULT_EXTENSIONS
        []                                  ->  ()  # 空元组 = 不做扩展名限制

    永不抛异常。
    """
    if extensions is None:
        return DEFAULT_EXTENSIONS
    if isinstance(extensions, str):
        # 允许 ".xyz,.mol" 这种逗号分隔的写法
        extensions = list(extensions.replace(";", ",").split(","))
    try:
        items = list(extensions)
    except TypeError:
        return DEFAULT_EXTENSIONS
    out: list[str] = []
    seen: set = set()
    for raw in items:
        if raw is None:
            continue
        try:
            text = str(raw).strip().lower()
        except Exception:
            continue
        if not text:
            continue
        if not text.startswith("."):
            text = "." + text
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def parse_drop_data(data: Any) -> list[str]:
    """
    解析 tkdnd 的 `<<Drop>>` 事件数据串。

    tkdnd 的格式：路径以空格分隔；**含空格的路径用花括号包裹**，例如::

        {C:/Users/张三/我的 文件/a.xyz} C:/tmp/b.mol {D:/x y/z.pdb}

    本函数把它拆成 `["C:/Users/张三/我的 文件/a.xyz", "C:/tmp/b.mol", "D:/x y/z.pdb"]`。

    容错：
      - 传入 list/tuple 时原样转成字符串列表（菜单导入路径复用本函数）；
      - 没有花括号但整串本身就是个存在的路径时，按单路径处理
        （某些 tkdnd 版本对单个含空格路径不加花括号）；
      - 永不抛异常，解析不出来就返回空列表。
    """
    if data is None:
        return []
    if isinstance(data, (list, tuple, set)):
        out: list[str] = []
        for item in data:
            try:
                text = os.fspath(item) if hasattr(item, "__fspath__") else str(item)
            except Exception:
                continue
            text = text.strip().strip("{}").strip()
            if text:
                out.append(text)
        return out

    try:
        text = str(data)
    except Exception:
        return []
    text = text.strip()
    if not text:
        return []

    # 审计 4.2 修复：优先用 tkinterdnd2 的 splitlist 解析（对含空格/花括号的路径最稳健，
    # 避免手写状态机把文件名内的花括号误当分隔符）。失败时回退到原有手写逻辑。
    try:
        from tkinterdnd2 import TkinterDnD

        parts = TkinterDnD.splitlist(text)
        if parts:
            if isinstance(parts, str):
                parts = [parts]
            out = [os.fspath(p).strip().strip("{}").strip() for p in parts if p]
            if out:
                return out
    except Exception:
        pass

    if "{" not in text:
        # 无花括号：优先按"整串就是一个路径"判断（处理含空格的单路径）
        try:
            if os.path.exists(text):
                return [text]
        except (OSError, ValueError):
            pass
        return [seg for seg in text.split() if seg]

    tokens: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "{":
            end = text.find("}", i + 1)
            if end < 0:
                # 花括号未闭合：把剩余部分整体当成一个路径，别丢数据
                token = text[i + 1 :].strip()
                if token:
                    tokens.append(token)
                break
            token = text[i + 1 : end].strip()
            if token:
                tokens.append(token)
            i = end + 1
            continue
        start = i
        while i < length and not text[i].isspace():
            i += 1
        token = text[start:i].strip()
        if token:
            tokens.append(token)
    return tokens


def _norm_key(path: Path) -> str:
    """去重键：优先用真实路径（消解 8.3 短名 / 大小写差异），失败退回绝对路径。"""
    try:
        return os.path.normcase(os.path.realpath(os.fspath(path)))
    except (OSError, ValueError, TypeError):
        try:
            return os.path.normcase(os.path.abspath(os.fspath(path)))
        except Exception:
            return os.path.normcase(str(path))


def is_protected_path(path: Any) -> bool:
    """
    路径的任一层是否落在受保护目录（`.backup` / `.trash_backup`）内。

    纯字符串判断，不碰文件系统 —— 路径不存在 / 无权限时也能挡住。
    """
    if path is None:
        return False
    try:
        text = os.fspath(path) if hasattr(path, "__fspath__") else str(path)
    except (TypeError, ValueError):
        return False
    segments = text.replace("\\", "/").split("/")
    return any(seg in PROTECTED_DIR_NAMES for seg in segments)


def assert_protected_names_in_sync(other: Iterable[str]) -> bool:
    """
    自检：本模块的受保护目录名单是否与 `core.model.PROTECTED_DIR_NAMES` 一致。

    供全局一致性审查 / 单测调用，运行期不强制执行（避免为了一个断言把
    model 的重依赖拉进纯逻辑模块）。
    """
    try:
        return frozenset(other) == PROTECTED_DIR_NAMES
    except TypeError:
        return False


# ---------------------------------------------------------------- 主类


class DropHandler:
    """
    拖放路径路由器。**无状态复用**：每次拖放建一个实例即可（构造开销可忽略）。

    典型用法::

        handler = DropHandler.from_config(app.config_data, work_dir=model.work_dir)
        result = handler.process_drop_data(event.data)     # 拖放入口
        result = handler.process(paths_from_filedialog)    # 菜单降级入口
        if result.has_accepted():
            model.import_external_files(result.accepted)
    """

    def __init__(
        self,
        *,
        extensions: Any = None,
        work_dir: Any = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        recursive: bool = True,
        follow_symlinks: bool = False,
    ) -> None:
        """
        参数:
            extensions:      扩展名白名单；None=用默认；空列表=不限制。
            work_dir:        当前工作目录。用于识别"已在工作目录根下"的无意义导入。
            max_files:       单次接受的最大文件数（含目录展开后的总数）。
            max_depth:       目录递归深度上限。
            recursive:       是否递归展开目录；False 时只取目录第一层。
            follow_symlinks: 是否跟随符号链接。**默认 False**，与项目既有安全策略一致。
        """
        self.extensions: tuple[str, ...] = normalize_extensions(extensions)
        try:
            self.max_files: int = max(1, int(max_files))
        except (TypeError, ValueError):
            self.max_files = DEFAULT_MAX_FILES
        try:
            self.max_depth: int = max(0, int(max_depth))
        except (TypeError, ValueError):
            self.max_depth = DEFAULT_MAX_DEPTH
        self.recursive: bool = bool(recursive)
        self.follow_symlinks: bool = bool(follow_symlinks)

        self.work_dir: Path | None = None
        self._work_dir_key: str = ""
        if work_dir is not None:
            try:
                self.work_dir = Path(work_dir)
                self._work_dir_key = _norm_key(self.work_dir)
            except (TypeError, ValueError):
                self.work_dir = None
                self._work_dir_key = ""

    # ------------------------------------------------------------ 构造

    @classmethod
    def from_config(cls, config: Any, *, work_dir: Any = None, **overrides: Any) -> DropHandler:
        """
        从整份 config（或直接是 `config["dnd"]`）构造。

        读不到配置时静默回落到默认值 —— 拖放是易用性功能，不能因为配置
        缺一个键就罢工。
        """
        node: dict[str, Any] = {}
        if isinstance(config, dict):
            candidate = config.get("dnd")
            if isinstance(candidate, dict):
                node = candidate
            elif "extensions" in config or "enabled" in config:
                node = config
        kwargs: dict[str, Any] = {
            "extensions": node.get("extensions", None),
            "work_dir": work_dir,
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    @staticmethod
    def is_enabled(config: Any) -> bool:
        """读 `config["dnd"]["enabled"]`，缺失时默认 True。"""
        if not isinstance(config, dict):
            return True
        node = config.get("dnd")
        if not isinstance(node, dict):
            return True
        try:
            return bool(node.get("enabled", True))
        except Exception:
            return True

    # ------------------------------------------------------------ 判定

    def is_allowed_ext(self, path: Any) -> bool:
        """扩展名是否在白名单内。白名单为空元组时一律放行。"""
        if not self.extensions:
            return True
        try:
            suffix = os.path.splitext(os.fspath(path))[1].lower()
        except (TypeError, ValueError):
            return False
        return suffix in self.extensions

    def is_in_work_dir_root(self, path: Path) -> bool:
        """
        文件是否**恰好**位于工作目录根下。

        这种情况导入是纯粹的无意义副本（会被改名成 xxx_1.mol），直接拒绝。
        注意：工作目录**子目录**里的文件不算，用户可能就是想把它提到根下。
        """
        if not self._work_dir_key:
            return False
        try:
            return _norm_key(path.parent) == self._work_dir_key
        except Exception:
            return False

    # ------------------------------------------------------------ 主流程

    def process_drop_data(self, data: Any) -> DropResult:
        """拖放入口：先解析 tkdnd 数据串，再走 `process()`。"""
        return self.process(parse_drop_data(data))

    def process(self, paths: Any) -> DropResult:
        """
        路径清单 → 结构化结果。**永不抛异常**（任何单条路径的异常都转成 rejected）。

        参数:
            paths: 路径序列（str / Path 均可），也接受 tkdnd 原始数据串。

        返回:
            `DropResult`。`accepted` 中的每一项都保证是"存在的、非符号链接的、
            扩展名合规的、不在受保护目录内的"真实文件绝对路径。
        """
        result = DropResult()
        if paths is None:
            return result
        if isinstance(paths, str):
            candidates: Sequence[Any] = parse_drop_data(paths)
        elif isinstance(paths, (list, tuple, set)):
            candidates = list(paths)
        else:
            candidates = [paths]

        seen: set = set()
        for raw in candidates:
            if result.accepted_count >= self.max_files:
                result.truncated = True
                result.rejected.append(RejectedItem(str(raw), REASON_LIMIT, f"最多 {self.max_files} 个"))
                continue
            self._process_one(raw, result, seen)
        return result

    # ------------------------------------------------------------ 内部

    def _process_one(self, raw: Any, result: DropResult, seen: set) -> None:
        """处理单个输入路径（可能是文件，也可能是目录）。"""
        path = self._coerce_path(raw)
        if path is None:
            result.rejected.append(RejectedItem(str(raw), REASON_INVALID))
            return
        text = os.fspath(path)

        # 第一道：字符串级受保护目录判断（不碰文件系统，永远有效）
        if is_protected_path(path):
            result.rejected.append(RejectedItem(text, REASON_PROTECTED))
            return

        try:
            exists = path.exists()
        except OSError as exc:
            result.rejected.append(RejectedItem(text, REASON_UNREADABLE, str(exc)))
            return
        if not exists:
            result.rejected.append(RejectedItem(text, REASON_NOT_FOUND))
            return

        # 第二道：符号链接 / Junction 一律拒绝（与项目既有安全策略一致）
        if not self.follow_symlinks and self._is_link(path):
            result.rejected.append(RejectedItem(text, REASON_SYMLINK))
            return

        try:
            is_dir = path.is_dir()
        except OSError as exc:
            result.rejected.append(RejectedItem(text, REASON_UNREADABLE, str(exc)))
            return

        if is_dir:
            self._expand_dir(path, result, seen)
        else:
            self._accept_file(path, result, seen)

    def _coerce_path(self, raw: Any) -> Path | None:
        """把任意输入转成 Path（去掉引号 / 花括号 / file:// 前缀），失败返回 None。"""
        if raw is None:
            return None
        try:
            text = os.fspath(raw) if hasattr(raw, "__fspath__") else str(raw)
        except (TypeError, ValueError):
            return None
        text = text.strip().strip("{}").strip().strip('"').strip("'").strip()
        if not text:
            return None
        if text.lower().startswith("file:///"):
            # tkdnd 在部分平台会给出 file:// URI
            try:
                from urllib.parse import unquote, urlparse

                parsed = urlparse(text)
                local = unquote(parsed.path)
                # Windows: "/C:/x/y" -> "C:/x/y"
                if os.name == "nt" and len(local) > 2 and local[0] == "/" and local[2] == ":":
                    local = local[1:]
                text = local
            except Exception:
                return None
        try:
            return Path(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_link(path: Path) -> bool:
        """是否符号链接或 Windows Junction / ReparsePoint。判断失败按"是"处理（保守）。"""
        try:
            if path.is_symlink():
                return True
        except OSError:
            return True
        if os.name != "nt":
            return False
        try:
            st = os.lstat(path)
        except OSError:
            return True
        try:
            FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
            attrs = getattr(st, "st_file_attributes", 0)
            return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        except Exception:
            return False

    def _accept_file(self, path: Path, result: DropResult, seen: set) -> None:
        """把一个**文件**纳入结果（或给出拒绝原因）。"""
        text = os.fspath(path)
        if result.accepted_count >= self.max_files:
            result.truncated = True
            result.rejected.append(RejectedItem(text, REASON_LIMIT, f"最多 {self.max_files} 个"))
            return
        if not self.is_allowed_ext(path):
            result.rejected.append(RejectedItem(text, REASON_EXTENSION, os.path.splitext(text)[1] or "无扩展名"))
            return
        key = _norm_key(path)
        if key in seen:
            result.rejected.append(RejectedItem(text, REASON_DUPLICATE))
            return
        if self.is_in_work_dir_root(path):
            result.rejected.append(RejectedItem(text, REASON_ALREADY_HERE))
            return
        try:
            resolved = path.resolve(strict=False)
        except (OSError, ValueError):
            resolved = path
        # resolve 之后再查一次受保护目录（挡住相对路径 / 短名等绕过手法）
        if is_protected_path(resolved):
            result.rejected.append(RejectedItem(text, REASON_PROTECTED))
            return
        seen.add(key)
        result.accepted.append(resolved)

    def _expand_dir(self, root: Path, result: DropResult, seen: set) -> None:
        """
        递归展开目录，收集符合白名单的文件。

        - 跳过受保护目录整棵子树（`.backup` / `.trash_backup`）；
        - 不跟随符号链接目录（防递归死循环 / 越界）；
        - 深度超过 `max_depth` 的层级不再深入；
        - 无权限的子目录静默跳过，不让整次拖放失败；
        - 目录里一个合规文件都没有时，给出 `REASON_EMPTY_DIR` 让用户知道为什么没反应。
        """
        before = result.accepted_count
        stack: list[tuple[str, int]] = [(os.fspath(root), 0)]
        result.scanned_dirs += 1
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    for entry in iterator:
                        if result.accepted_count >= self.max_files:
                            result.truncated = True
                            return
                        name = entry.name
                        if name in PROTECTED_DIR_NAMES:
                            continue  # 🔴 整棵受保护子树隐身
                        try:
                            entry_is_dir = entry.is_dir(follow_symlinks=self.follow_symlinks)
                        except OSError:
                            continue
                        if entry_is_dir:
                            if not self.recursive:
                                continue
                            if depth + 1 > self.max_depth:
                                continue
                            stack.append((entry.path, depth + 1))
                            result.scanned_dirs += 1
                            continue
                        try:
                            entry_is_file = entry.is_file(follow_symlinks=self.follow_symlinks)
                        except OSError:
                            continue
                        if not entry_is_file:
                            continue
                        if not self.is_allowed_ext(entry.path):
                            continue  # 目录展开时静默跳过，不给每个文件生成一条 rejected
                        try:
                            child = Path(entry.path)
                        except (TypeError, ValueError):
                            continue
                        if not self.follow_symlinks and self._is_link(child):
                            continue
                        self._accept_file(child, result, seen)
            except (PermissionError, OSError):
                continue
        if result.accepted_count == before:
            hint = "白名单：" + "、".join(self.extensions) if self.extensions else ""
            result.rejected.append(RejectedItem(os.fspath(root), REASON_EMPTY_DIR, hint))


__all__ = [
    "DEFAULT_EXTENSIONS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_FILES",
    "PROTECTED_DIR_NAMES",
    "REASON_ALREADY_HERE",
    "REASON_DUPLICATE",
    "REASON_EMPTY_DIR",
    "REASON_EXTENSION",
    "REASON_INVALID",
    "REASON_LIMIT",
    "REASON_NOT_FOUND",
    "REASON_PROTECTED",
    "REASON_SYMLINK",
    "REASON_UNREADABLE",
    "DropHandler",
    "DropResult",
    "RejectedItem",
    "assert_protected_names_in_sync",
    "is_protected_path",
    "normalize_extensions",
    "parse_drop_data",
]
