"""backup 子系统 mixin（由原 core/model.py 拆分而来）。"""
from typing import Dict, List, Optional

from ._common import *  # noqa: F401,F403


class BackupMixin:
    def configure_backup(self, backup_cfg: Optional[Dict] = None) -> None:
        """
        用 config["backup"] 配置备份行为。controller 初始化时调用一次即可。

        永不抛异常：配置读不出来就用默认值（启用 / 每类保留 10 份）。
        """
        cfg = backup_cfg if isinstance(backup_cfg, dict) else {}
        try:
            self._backup_enabled = bool(cfg.get("enabled", True))
        except Exception:
            self._backup_enabled = True
        try:
            self._backup_keep = int(cfg.get("keep_per_type", 10) or 10)
        except (TypeError, ValueError):
            self._backup_keep = 10
        try:
            self._backup_max_mb = int(cfg.get("max_file_mb", 64) or 64)
        except (TypeError, ValueError):
            self._backup_max_mb = 64
        mgr = getattr(self, "_backup_manager", None)
        if mgr is not None:
            try:
                mgr.configure(
                    enabled=self._backup_enabled,
                    keep_per_type=self._backup_keep,
                    max_file_bytes=self._backup_max_mb * 1024 * 1024,
                )
            except Exception as exc:
                logger.debug("更新备份配置失败（非致命）: %s", exc)

    @property
    def backup_manager(self):
        """
        懒加载的 BackupManager，绑定当前工作目录下的 ``.backup``。

        工作目录变化后会自动重建（快照跟着数据走）。任何构造失败都返回 None，
        调用方按「拿不到就跳过备份」处理，绝不阻断主流程。
        """
        try:
            from utils.backup_manager import BackupManager
        except Exception as exc:  # pragma: no cover
            logger.warning("⚠️ 备份模块不可用（已跳过备份）: %s", exc)
            return None
        try:
            root = get_backup_dir(self.work_dir, create=False)
        except Exception as exc:
            logger.warning("⚠️ 解析备份目录失败（已跳过备份）: %s", exc)
            return None
        mgr = getattr(self, "_backup_manager", None)
        if mgr is not None and getattr(mgr, "backup_root", None) == root:
            return mgr
        try:
            mgr = BackupManager(
                root,
                keep_per_type=getattr(self, "_backup_keep", 10),
                enabled=getattr(self, "_backup_enabled", True),
                max_file_bytes=getattr(self, "_backup_max_mb", 64) * 1024 * 1024,
                logger=logger,
            )
        except Exception as exc:
            logger.warning("⚠️ 备份管理器初始化失败（已跳过备份）: %s", exc)
            return None
        self._backup_manager = mgr
        return mgr

    def create_backup_snapshot(self, trigger: str, files, description: str = ""):
        """
        统一的快照入口。**永不抛异常**（架构 §6.4），失败返回 None。

        参数:
            trigger: 'mapping' / 'export' / 'config'
            files:   待备份的文件路径集合（不存在的会被静默跳过）
        """
        try:
            mgr = self.backup_manager
            if mgr is None:
                return None
            return mgr.create_snapshot(trigger, files, description)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ 创建快照失败（不影响主操作）: %s", exc)
            return None

    def get_mapping_artifacts(self) -> List[Path]:
        """
        返回「映射表产物集合」——F17 快照按产物集合而非单文件备份（架构 §7 风险表）。

        C9：映射表历史上是**读 TSV / 写 JSON** 双格式，两者都要纳入快照，
        否则回滚后会出现 JSON 已还原、TSV 仍是新版的撕裂状态。
        """
        out: List[Path] = []
        seen: set = set()

        def _add(p) -> None:
            if p is None:
                return
            try:
                path = Path(p)
                key = os.path.normcase(os.fspath(path))
            except (TypeError, ValueError, OSError):
                return
            if key in seen:
                return
            seen.add(key)
            out.append(path)

        _add(self.default_mapping_path())
        _add(getattr(self, "mapping_source_path", None))
        return out

    def default_mapping_path(self) -> Path:
        """映射表 JSON 的默认落盘位置（与 mapping_dialog 历史行为一致）。"""
        return Path(self.work_dir) / "分子命名映射.json"
