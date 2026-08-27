#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F17 自动备份 —— 快照管理器（T09 / Phase 1）
─────────────────────────────────────────
在「映射表保存 / 导出产物覆写 / 配置写入」等关键操作**之前**，把即将被覆盖的
文件复制一份到 ``<work_dir>/.backup/<时间戳>_<触发类型>/``，并写一份 meta.json
描述快照内容，用户可在「备份管理」对话框中预览与回滚。

目录结构（架构 §3.3）::

    .backup/
      ├── 20260806_143022_mapping/
      │     ├── meta.json          # SnapshotMeta 序列化
      │     └── 分子命名映射.json    # 实际文件副本
      └── 20260806_150811_export/

🔴 铁律（架构 §6.4）：
    **任何失败都只 logger.warning，绝不 raise 到主流程。**
    备份是附加保险，不是前置条件；备份挂了用户的保存操作也必须照常完成。

其他约束：
  - **无 Tk 依赖**，可脱离 GUI 单测（架构 §6.6）；
  - 不 import requests / chem.psi4；
  - 原子写复用 utils/config.save_config 的成熟范式（tmp → chmod → os.replace，C19），
    通过 utils.path_utils.chmod_quiet 复用，不重复造轮子。

⚠️ 与 core/model.scan_files 的协同：``.backup`` 必须在 scan_files 的排除名单里
（T08），否则备份副本会混进文件列表，被「整理 / 重命名 / 删除」误伤 —— 安全网
反而变成事故源。见 model.PROTECTED_DIR_NAMES。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from utils.logger import default_logger as _default_logger
from utils.path_utils import chmod_quiet, enforce_no_symlink_target
from utils.version import __version__ as APP_VERSION


# ---------------------------------------------------------------- 常量

#: 备份根目录名。⚠️ 改动此值必须同步 core/model.PROTECTED_DIR_NAMES。
BACKUP_DIR_NAME: str = ".backup"

#: 快照元数据文件名
META_FILENAME: str = "meta.json"

#: 本期支持的触发类型（架构 Q2 决策：PSI4 计算输出延后到 Phase 2）
TRIGGER_MAPPING: str = "mapping"
TRIGGER_EXPORT: str = "export"
TRIGGER_CONFIG: str = "config"
TRIGGER_PRERESTORE: str = "prerestore"

KNOWN_TRIGGERS: tuple[str, ...] = (
    TRIGGER_MAPPING, TRIGGER_EXPORT, TRIGGER_CONFIG, TRIGGER_PRERESTORE,
)

TRIGGER_LABELS: dict[str, str] = {
    TRIGGER_MAPPING: "映射表",
    TRIGGER_EXPORT: "导出产物",
    TRIGGER_CONFIG: "应用配置",
    TRIGGER_PRERESTORE: "回滚前自动快照",
}

#: 每种触发类型默认保留份数
DEFAULT_KEEP_PER_TYPE: int = 10

#: 单个文件的备份体积上限（默认 64 MB）。超过则跳过该文件并记 warning，
#: 避免误把 PSI4 的 GB 级输出拷进 .backup（Q2 明确本期不覆盖计算输出）。
DEFAULT_MAX_FILE_BYTES: int = 64 * 1024 * 1024

_SAFE_TRIGGER_RE = re.compile(r"[^0-9A-Za-z_\-]+")
_SNAPSHOT_ID_RE = re.compile(r"^\d{8}_\d{6}(?:_\d+)?_[0-9A-Za-z_\-]+$")

_TS_FMT = "%Y%m%d_%H%M%S"


# ---------------------------------------------------------------- 工具函数

def format_size(num_bytes: Any) -> str:
    """把字节数格式化为人类可读字符串（对话框展示用）。永不抛异常。"""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    if n < 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def sanitize_trigger(trigger: Any) -> str:
    """把触发类型规范化为可安全用作目录名后缀的短标识。"""
    try:
        text = str(trigger or "").strip().lower()
    except Exception:
        text = ""
    text = _SAFE_TRIGGER_RE.sub("_", text).strip("_")
    return text or "misc"


def trigger_label(trigger: Any) -> str:
    """返回触发类型的中文标签，未知类型回落为原字符串。"""
    key = sanitize_trigger(trigger)
    return TRIGGER_LABELS.get(key, key)


def _atomic_write_json(path: Path, payload: Any) -> bool:
    """
    原子写 JSON：tmp → chmod 0o600 → os.replace（复用 config.save_config 范式，C19）。

    返回 True/False，不抛异常。
    """
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        chmod_quiet(tmp_path, 0o600)
        if hasattr(os, "replace"):
            os.replace(tmp_path, path)
        else:  # pragma: no cover - 现代 Python 都有 os.replace
            tmp_path.rename(path)
        chmod_quiet(path, 0o600)
        tmp_path = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------- 数据模型

@dataclass
class SnapshotMeta:
    """一次快照的元数据（对应快照目录下的 meta.json）。"""

    snapshot_id: str = ""
    timestamp: str = ""                       # ISO 8601，秒精度
    trigger: str = TRIGGER_MAPPING
    description: str = ""
    app_version: str = APP_VERSION
    files: list[dict[str, Any]] = field(default_factory=list)
    total_size: int = 0

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> SnapshotMeta | None:
        """从 dict 还原；结构不合法时返回 None（不抛）。"""
        if not isinstance(data, dict):
            return None
        try:
            raw_files = data.get("files")
            files: list[dict[str, Any]] = []
            if isinstance(raw_files, list):
                for item in raw_files:
                    if isinstance(item, dict):
                        files.append(dict(item))
            return cls(
                snapshot_id=str(data.get("snapshot_id", "") or ""),
                timestamp=str(data.get("timestamp", "") or ""),
                trigger=sanitize_trigger(data.get("trigger", "")),
                description=str(data.get("description", "") or ""),
                app_version=str(data.get("app_version", "") or ""),
                files=files,
                total_size=int(data.get("total_size", 0) or 0),
            )
        except (TypeError, ValueError):
            return None

    # ---- 展示辅助 ----
    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def trigger_label(self) -> str:
        return trigger_label(self.trigger)

    @property
    def size_text(self) -> str:
        return format_size(self.total_size)

    def display_time(self) -> str:
        """把 ISO 时间戳转成 ``YYYY-MM-DD HH:MM:SS``；失败时回落为 snapshot_id。"""
        try:
            return datetime.fromisoformat(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return self.snapshot_id or "-"


# ---------------------------------------------------------------- 管理器

class BackupManager:
    """
    快照的创建 / 列举 / 预览 / 回滚 / 清理。

    线程安全性：本类不持有可变共享状态（除配置项），所有操作基于文件系统。
    并发调用 ``create_snapshot`` 时靠「秒级时间戳 + 冲突自增后缀」避免目录撞名。

    典型用法::

        mgr = BackupManager(work_dir / ".backup", keep_per_type=10)
        mgr.create_snapshot("mapping", [mapping_json, mapping_tsv], "保存映射表前")
    """

    def __init__(
        self,
        backup_root: Any,
        *,
        keep_per_type: int = DEFAULT_KEEP_PER_TYPE,
        enabled: bool = True,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        logger: Any = None,
    ) -> None:
        self._logger = logger if logger is not None else _default_logger
        try:
            self.backup_root = Path(backup_root)
        except (TypeError, ValueError):
            self.backup_root = Path(BACKUP_DIR_NAME)
        self.enabled = bool(enabled)
        self.keep_per_type = self._coerce_keep(keep_per_type)
        self.max_file_bytes = self._coerce_max_bytes(max_file_bytes)

    # ------------------------------------------------------------ 配置

    @staticmethod
    def _coerce_keep(value: Any) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return DEFAULT_KEEP_PER_TYPE
        return max(1, min(n, 500))

    @staticmethod
    def _coerce_max_bytes(value: Any) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return DEFAULT_MAX_FILE_BYTES
        return max(1024, n)

    def configure(
        self,
        *,
        enabled: bool | None = None,
        keep_per_type: int | None = None,
        max_file_bytes: int | None = None,
    ) -> None:
        """运行期更新配置（用户在设置里改了 backup.* 时调用）。"""
        if enabled is not None:
            self.enabled = bool(enabled)
        if keep_per_type is not None:
            self.keep_per_type = self._coerce_keep(keep_per_type)
        if max_file_bytes is not None:
            self.max_file_bytes = self._coerce_max_bytes(max_file_bytes)

    def _warn(self, msg: str, *args: Any) -> None:
        """统一的「只警告不阻断」出口。"""
        try:
            self._logger.warning(msg, *args)
        except Exception:
            pass

    def _debug(self, msg: str, *args: Any) -> None:
        try:
            self._logger.debug(msg, *args)
        except Exception:
            pass

    # ------------------------------------------------------------ 路径

    def ensure_root(self) -> Path | None:
        """确保备份根目录存在；失败返回 None（不抛）。"""
        try:
            self.backup_root.mkdir(parents=True, exist_ok=True)
            chmod_quiet(self.backup_root, 0o700)
            return self.backup_root
        except OSError as exc:
            self._warn("⚠️ 备份目录创建失败（已跳过备份，不影响主操作）: %s", exc)
            return None

    def get_snapshot_dir(self, snapshot_id: Any) -> Path | None:
        """
        由 snapshot_id 解析出快照目录，并校验它确实位于 backup_root 之内。

        非法 id / 越界路径一律返回 None，防止 ``../`` 穿越删到别处。
        """
        try:
            sid = str(snapshot_id or "").strip()
        except Exception:
            return None
        if not sid or not _SNAPSHOT_ID_RE.match(sid):
            return None
        candidate = self.backup_root / sid
        try:
            root_real = self.backup_root.resolve(strict=False)
            cand_real = candidate.resolve(strict=False)
            cand_real.relative_to(root_real)
        except (OSError, ValueError):
            return None
        return candidate

    def _allocate_snapshot_dir(self, trigger: str) -> tuple[str, Path] | None:
        """生成不冲突的快照目录并创建之。返回 (snapshot_id, dir)；失败返回 None。"""
        root = self.ensure_root()
        if root is None:
            return None
        stamp = datetime.now().strftime(_TS_FMT)
        for attempt in range(0, 1000):
            sid = f"{stamp}_{trigger}" if attempt == 0 else f"{stamp}_{attempt}_{trigger}"
            target = root / sid
            try:
                target.mkdir(parents=False, exist_ok=False)
                chmod_quiet(target, 0o700)
                return sid, target
            except FileExistsError:
                continue
            except OSError as exc:
                self._warn("⚠️ 创建快照目录失败（已跳过备份）: %s", exc)
                return None
        self._warn("⚠️ 同一秒内快照目录冲突过多，已跳过本次备份")
        return None

    # ------------------------------------------------------------ 创建快照

    def create_snapshot(
        self,
        trigger: Any,
        files: Iterable[Any],
        description: str = "",
    ) -> SnapshotMeta | None:
        """
        为 ``files`` 里**已存在的普通文件**创建一份快照。

        参数:
            trigger: 触发类型（mapping / export / config / prerestore …）
            files: 待备份文件路径集合；不存在的路径会被静默跳过
                   （首次保存时目标文件本就不存在，这不是错误）
            description: 人类可读描述，展示在备份管理对话框里

        返回:
            成功返回 ``SnapshotMeta``；被禁用 / 无可备份内容 / 任何失败返回 ``None``。

        🔴 本方法**绝不抛异常**——调用方无需 try，直接忽略 None 即可继续主流程。
        """
        try:
            if not self.enabled:
                self._debug("备份功能已关闭，跳过 %s 快照", trigger)
                return None

            trig = sanitize_trigger(trigger)
            sources = self._collect_sources(files)
            if not sources:
                self._debug("没有可备份的现存文件，跳过 %s 快照", trig)
                return None

            allocated = self._allocate_snapshot_dir(trig)
            if allocated is None:
                return None
            snapshot_id, snap_dir = allocated

            entries: list[dict[str, Any]] = []
            total = 0
            used_names: set[str] = {META_FILENAME}
            for src in sources:
                entry = self._copy_one(src, snap_dir, used_names)
                if entry is None:
                    continue
                entries.append(entry)
                total += int(entry.get("size", 0) or 0)

            if not entries:
                # 一个文件都没拷成功：清掉空目录，别留垃圾
                self._remove_dir_quiet(snap_dir)
                self._warn("⚠️ %s 快照没有成功备份任何文件，已放弃", trig)
                return None

            meta = SnapshotMeta(
                snapshot_id=snapshot_id,
                timestamp=datetime.now().isoformat(timespec="seconds"),
                trigger=trig,
                description=str(description or "").strip(),
                app_version=APP_VERSION,
                files=entries,
                total_size=total,
            )
            if not _atomic_write_json(snap_dir / META_FILENAME, meta.to_dict()):
                self._remove_dir_quiet(snap_dir)
                self._warn("⚠️ 快照元数据写入失败，已回滚该快照目录")
                return None

            self._debug(
                "📦 已创建 %s 快照 %s（%d 个文件 / %s）",
                trigger_label(trig), snapshot_id, len(entries), format_size(total),
            )
            self.prune(trig)
            return meta
        except Exception as exc:  # noqa: BLE001 —— 契约：绝不把异常抛给主流程
            self._warn("⚠️ 创建备份快照失败（不影响主操作）: %s", exc)
            return None

    def _collect_sources(self, files: Iterable[Any]) -> list[Path]:
        """把入参规整为「去重后的、真实存在的普通文件」列表。"""
        out: list[Path] = []
        seen: set[str] = set()
        try:
            iterator = list(files or [])
        except TypeError:
            return out
        for item in iterator:
            if item is None:
                continue
            try:
                p = Path(item)
            except (TypeError, ValueError):
                continue
            try:
                if p.is_symlink():
                    # 与项目「拒绝 symlink」策略一致：不跟随链接备份
                    self._debug("跳过符号链接，不纳入快照: %s", p)
                    continue
                if not p.is_file():
                    continue
                key = os.path.normcase(os.fspath(p.resolve(strict=False)))
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    def _copy_one(
        self,
        src: Path,
        snap_dir: Path,
        used_names: set[str],
    ) -> dict[str, Any] | None:
        """把单个文件拷进快照目录，返回该文件的 meta 条目；失败返回 None。"""
        try:
            size = src.stat().st_size
        except OSError as exc:
            self._warn("⚠️ 无法读取待备份文件信息，跳过 %s: %s", src.name, exc)
            return None
        if size > self.max_file_bytes:
            self._warn(
                "⚠️ 文件超过单文件备份上限（%s > %s），跳过 %s",
                format_size(size), format_size(self.max_file_bytes), src.name,
            )
            return None

        stored_name = self._unique_name(src.name, used_names)
        dst = snap_dir / stored_name
        try:
            shutil.copy2(os.fspath(src), os.fspath(dst))
            chmod_quiet(dst, 0o600)
        except (OSError, shutil.Error) as exc:
            self._warn("⚠️ 备份文件复制失败，跳过 %s: %s", src.name, exc)
            return None

        try:
            mtime = src.stat().st_mtime
        except OSError:
            mtime = 0.0
        try:
            orig = os.fspath(src.resolve(strict=False))
        except OSError:
            orig = os.fspath(src)
        return {
            "orig_path": orig,
            "orig_name": src.name,
            "stored_name": stored_name,
            "size": int(size),
            "mtime": float(mtime),
        }

    @staticmethod
    def _unique_name(name: str, used: set[str]) -> str:
        """在快照目录内生成不重名的存储文件名。"""
        base = os.path.basename(str(name) or "unnamed")
        base = base.replace(os.sep, "_").replace("/", "_") or "unnamed"
        if base not in used:
            used.add(base)
            return base
        stem, ext = os.path.splitext(base)
        for i in range(1, 10000):
            cand = f"{stem}_{i}{ext}"
            if cand not in used:
                used.add(cand)
                return cand
        used.add(base + ".dup")
        return base + ".dup"

    # ------------------------------------------------------------ 列举 / 预览

    def list_snapshots(self, trigger: Any = None) -> list[SnapshotMeta]:
        """
        列出所有快照，按时间倒序（最新在前）。

        ``trigger`` 非空时只返回该类型。目录缺 meta.json 或 meta 损坏时跳过并记 debug。
        永不抛异常，失败返回空列表。
        """
        result: list[SnapshotMeta] = []
        try:
            if not self.backup_root.is_dir():
                return result
            want = sanitize_trigger(trigger) if trigger else None
            for child in self.backup_root.iterdir():
                try:
                    if not child.is_dir() or child.is_symlink():
                        continue
                    if not _SNAPSHOT_ID_RE.match(child.name):
                        continue
                    meta = self._read_meta(child)
                    if meta is None:
                        continue
                    if want and meta.trigger != want:
                        continue
                    result.append(meta)
                except OSError:
                    continue
        except OSError as exc:
            self._warn("⚠️ 读取备份目录失败: %s", exc)
            return []
        result.sort(key=self._snapshot_sort_key, reverse=True)
        return result

    @staticmethod
    def _snapshot_sort_key(meta: SnapshotMeta) -> tuple[str, int]:
        """
        快照排序键：``(时间戳, 同秒序号)``。

        🔴 不能直接拿 snapshot_id 做字符串排序：同一秒内的第 2 份快照 ID 形如
        ``20260806_173426_1_mapping``，而第 1 份是 ``20260806_173426_mapping``，
        按字符串比较 ``'1' < 'm'``，倒序时反而把**旧的**排到前面。
        prune() 依赖「最新在前」来决定删谁，排反了会把刚存的新版本当成超额快照删掉。
        因此必须把同秒序号单独解析成整数参与比较。
        """
        sid = meta.snapshot_id or ""
        parts = sid.split("_")
        if len(parts) < 2:
            return (sid, 0)
        stamp = f"{parts[0]}_{parts[1]}"      # YYYYmmdd_HHMMSS
        seq = 0
        if len(parts) >= 3:
            try:
                seq = int(parts[2])           # 同秒冲突序号（无冲突时这里是 trigger，转换失败即 0）
            except ValueError:
                seq = 0
        return (stamp, seq)

    def _read_meta(self, snap_dir: Path) -> SnapshotMeta | None:
        meta_path = snap_dir / META_FILENAME
        try:
            if not meta_path.is_file():
                return None
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._debug("快照元数据不可读，已跳过 %s: %s", snap_dir.name, exc)
            return None
        meta = SnapshotMeta.from_dict(data)
        if meta is None:
            return None
        # 以目录名为准，防止 meta 里的 id 与实际目录不一致
        meta.snapshot_id = snap_dir.name
        return meta

    def get_snapshot(self, snapshot_id: Any) -> SnapshotMeta | None:
        """按 id 读取单个快照的元数据；不存在返回 None。"""
        snap_dir = self.get_snapshot_dir(snapshot_id)
        if snap_dir is None:
            return None
        try:
            if not snap_dir.is_dir():
                return None
        except OSError:
            return None
        return self._read_meta(snap_dir)

    def preview_snapshot(self, snapshot_id: Any) -> list[dict[str, Any]]:
        """
        返回快照内文件的展示信息列表：

            [{"orig_name", "orig_path", "size", "size_text", "exists_now", "stored_exists"}]

        ``exists_now`` 指原位置当前是否还有同名文件（回滚会覆盖它）。
        """
        meta = self.get_snapshot(snapshot_id)
        if meta is None:
            return []
        snap_dir = self.get_snapshot_dir(snapshot_id)
        rows: list[dict[str, Any]] = []
        for entry in meta.files:
            orig_path = str(entry.get("orig_path", "") or "")
            stored_name = str(entry.get("stored_name", "") or "")
            size = int(entry.get("size", 0) or 0)
            exists_now = False
            stored_exists = False
            try:
                exists_now = bool(orig_path) and Path(orig_path).is_file()
            except OSError:
                exists_now = False
            try:
                stored_exists = (
                    snap_dir is not None
                    and bool(stored_name)
                    and (snap_dir / stored_name).is_file()
                )
            except OSError:
                stored_exists = False
            rows.append({
                "orig_name": str(entry.get("orig_name", "") or stored_name),
                "orig_path": orig_path,
                "stored_name": stored_name,
                "size": size,
                "size_text": format_size(size),
                "exists_now": exists_now,
                "stored_exists": stored_exists,
            })
        return rows

    # ------------------------------------------------------------ 回滚

    def restore_snapshot(
        self,
        snapshot_id: Any,
        *,
        target_dir: Any = None,
        pre_snapshot: bool = True,
    ) -> tuple[int, list[str]]:
        """
        把快照内容还原回原位置（或 ``target_dir``）。

        参数:
            target_dir: 非 None 时，所有文件按原文件名还原到该目录（"另存为"语义）
            pre_snapshot: 覆盖现有文件前，先为它们建一份 ``prerestore`` 快照
                          （防止用户回滚错版本后无路可退）

        返回:
            ``(成功还原数, 错误信息列表)``。永不抛异常。
        """
        errors: list[str] = []
        try:
            meta = self.get_snapshot(snapshot_id)
            snap_dir = self.get_snapshot_dir(snapshot_id)
            if meta is None or snap_dir is None:
                return 0, [f"快照不存在或元数据损坏: {snapshot_id}"]

            dest_root: Path | None = None
            if target_dir is not None:
                try:
                    dr = Path(target_dir)
                    # 整链（含中间 junction/symlink）检查，防止 target_dir 指向别处被穿透写入
                    enforce_no_symlink_target(dr)
                    dest_root = dr
                    dest_root.mkdir(parents=True, exist_ok=True)
                except (ValueError, OSError, TypeError) as exc:
                    return 0, [f"目标目录不可用或含符号链接: {exc}"]

            # 1) 先给「即将被覆盖的现存文件」做一次保险快照
            if pre_snapshot:
                doomed: list[Path] = []
                for entry in meta.files:
                    dst = self._resolve_restore_target(entry, dest_root)
                    if dst is None:
                        continue
                    try:
                        if dst.is_file():
                            doomed.append(dst)
                    except OSError:
                        continue
                if doomed:
                    # 注意：prerestore 也走 create_snapshot，本身不会再触发回滚，无递归风险
                    self.create_snapshot(
                        TRIGGER_PRERESTORE, doomed,
                        f"回滚 {meta.snapshot_id} 前的自动保险快照",
                    )

            # 2) 逐个还原
            restored = 0
            for entry in meta.files:
                stored_name = str(entry.get("stored_name", "") or "")
                src = snap_dir / stored_name if stored_name else None
                dst = self._resolve_restore_target(entry, dest_root)
                name = str(entry.get("orig_name", "") or stored_name or "?")
                if src is None or dst is None:
                    errors.append(f"{name}: 快照条目信息不完整，已跳过")
                    continue
                try:
                    if not src.is_file():
                        errors.append(f"{name}: 快照内文件缺失，已跳过")
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # 逐祖先层检查 symlink/junction（含叶子），拦截中间目录穿透写入
                    try:
                        enforce_no_symlink_target(dst)
                    except ValueError as exc:
                        errors.append(f"{name}: 拒绝还原（{exc}）")
                        continue
                    if dst.exists() and dst.is_symlink():
                        errors.append(f"{name}: 目标是符号链接，拒绝覆盖")
                        continue
                    shutil.copy2(os.fspath(src), os.fspath(dst))
                    restored += 1
                except (OSError, shutil.Error) as exc:
                    errors.append(f"{name}: 还原失败（{exc}）")
            return restored, errors
        except Exception as exc:  # noqa: BLE001
            self._warn("⚠️ 回滚快照时发生未预期错误: %s", exc)
            return 0, [f"回滚失败: {exc}"]

    @staticmethod
    def _resolve_restore_target(entry: dict[str, Any], dest_root: Path | None) -> Path | None:
        """算出某条目应还原到哪个路径。"""
        try:
            if dest_root is not None:
                name = str(entry.get("orig_name", "") or entry.get("stored_name", "") or "")
                name = os.path.basename(name)
                return (dest_root / name) if name else None
            orig = str(entry.get("orig_path", "") or "")
            return Path(orig) if orig else None
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------ 清理

    def delete_snapshot(self, snapshot_id: Any) -> bool:
        """删除单个快照目录。返回是否删除成功；永不抛异常。"""
        snap_dir = self.get_snapshot_dir(snapshot_id)
        if snap_dir is None:
            self._warn("⚠️ 非法快照 id，拒绝删除: %s", snapshot_id)
            return False
        return self._remove_dir_quiet(snap_dir)

    def _remove_dir_quiet(self, path: Path) -> bool:
        """安全删除快照目录：必须位于 backup_root 之内，且不是符号链接。"""
        try:
            root_real = self.backup_root.resolve(strict=False)
            target_real = path.resolve(strict=False)
            if target_real == root_real:
                return False
            target_real.relative_to(root_real)
        except (OSError, ValueError):
            self._warn("⚠️ 拒绝删除备份根目录之外的路径: %s", path)
            return False
        try:
            if path.is_symlink():
                self._warn("⚠️ 拒绝删除符号链接: %s", path)
                return False
            if not path.exists():
                return True
            shutil.rmtree(path, ignore_errors=False)
            return True
        except OSError as exc:
            self._warn("⚠️ 删除快照目录失败: %s", exc)
            return False

    def prune(self, trigger: Any = None) -> int:
        """
        按保留策略清理超额快照：每种触发类型只保留最近 ``keep_per_type`` 份。

        ``trigger`` 为 None 时清理所有类型。返回实际删除的快照数；永不抛异常。
        """
        removed = 0
        try:
            triggers: list[str]
            if trigger:
                triggers = [sanitize_trigger(trigger)]
            else:
                triggers = sorted({m.trigger for m in self.list_snapshots()})
            for trig in triggers:
                snaps = self.list_snapshots(trig)  # 已按时间倒序
                for stale in snaps[self.keep_per_type:]:
                    if self.delete_snapshot(stale.snapshot_id):
                        removed += 1
            if removed:
                self._debug("🧹 已清理 %d 份超额快照（每类保留 %d 份）", removed, self.keep_per_type)
        except Exception as exc:  # noqa: BLE001
            self._warn("⚠️ 清理超额快照失败: %s", exc)
        return removed

    def total_size(self) -> int:
        """统计备份根目录总占用字节数（展示用）。失败返回 0。"""
        total = 0
        try:
            if not self.backup_root.is_dir():
                return 0
            for dirpath, _dirnames, filenames in os.walk(self.backup_root):
                for fn in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, fn))
                    except OSError:
                        continue
        except OSError:
            return total
        return total

    def clear_all(self) -> int:
        """删除所有快照（用户在对话框里点「清空全部备份」）。返回删除数量。"""
        removed = 0
        for meta in self.list_snapshots():
            if self.delete_snapshot(meta.snapshot_id):
                removed += 1
        return removed


__all__ = [
    "BACKUP_DIR_NAME",
    "META_FILENAME",
    "TRIGGER_MAPPING",
    "TRIGGER_EXPORT",
    "TRIGGER_CONFIG",
    "TRIGGER_PRERESTORE",
    "KNOWN_TRIGGERS",
    "TRIGGER_LABELS",
    "DEFAULT_KEEP_PER_TYPE",
    "DEFAULT_MAX_FILE_BYTES",
    "SnapshotMeta",
    "BackupManager",
    "format_size",
    "sanitize_trigger",
    "trigger_label",
]
