"""MolManager 模型基类：初始化 + 通用工具方法（由原 core/model.py 拆分而来）。"""

from ._common import *  # noqa: F401,F403


class MolManagerModelBase:
    def __init__(self, work_dir="output"):
        self._lock = threading.RLock()
        self.work_dir = Path(work_dir)
        self.mapping = {}
        self._reverse_mapping = {}
        self.history = []
        self.redo_stack: list = []
        # D-06：启动时恢复历史（在 work_dir 确定后调用；失败静默回退为空）
        try:
            self._load_history()
        except Exception:
            self.history = []
            self.redo_stack = []
        self.log_callback = None
        self._suppress_history = False
        # 历史汇聚容器：非 None 时，_add_history 不直接入栈，而是把 file_pairs
        # 汇聚到这里，由发起方（如 fix_all）在结束时合并成「一条」可撤销历史。
        # 取代旧的 _suppress_history 硬抑制——后者会让子步骤历史彻底丢失。
        self._history_sink: list | None = None
        self._scan_cache: tuple[int, tuple, int, list] | None = None
        self._scan_cache_revision: int = 0
        # ---- F17 自动备份（T09/T10）----
        # 映射表的「来源文件」（通常是 TSV），load_mapping_file 时记录，
        # 保存映射时一并纳入快照，避免 JSON/TSV 双格式只备份一半（C9）。
        self.mapping_source_path: Path | None = None
        self._backup_manager = None
        self._backup_enabled: bool = True
        self._backup_keep: int = 10
        self._backup_max_mb: int = 64

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    @work_dir.setter
    def work_dir(self, value):
        p = Path(value)
        try:
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("自动创建工作目录失败（将继续运行，但扫描可能失败）: %s", e)
        try:
            self._work_dir_resolved = p.resolve()
        except OSError:
            self._work_dir_resolved = p
        self._work_dir = self._work_dir_resolved
        # 同步 ob_utils 默认可信根：所有写出操作默认以工作目录为允许根，
        # 避免「工作目录 ≠ 程序启动目录」时被路径护栏误拒。
        try:
            ob_utils.set_default_base_dir(str(self._work_dir_resolved))
        except Exception:
            pass
        # 审计加分项：切换工作目录时同步清空 ob_utils 的描述符/分子读取缓存，
        # 释放内存并避免跨目录残留 stale 缓存（缓存键虽含完整路径不会误命中，清缓存仍属良好卫生）。
        try:
            ob_utils.clear_caches()
        except Exception:
            pass

    def set_log_callback(self, callback):
        self.log_callback = callback

    def _log(self, msg, level="info"):
        if self.log_callback:
            self.log_callback(msg, level)
        else:
            getattr(logger, level, logger.info)(msg)

    def set_mapping(self, mapping_dict):
        with self._lock:
            self.mapping = mapping_dict
            self._reverse_mapping = {v: k for k, v in mapping_dict.items()}
            self.invalidate_scan_cache()

    def invalidate_scan_cache(self):
        with self._lock:
            self._scan_cache_revision += 1
            self._scan_cache = None

    @staticmethod
    def _touches_protected(path) -> bool:
        """
        路径字符串里是否出现受保护目录段（.trash_backup / .backup）。

        纯字符串判断，不碰文件系统 —— 即使路径不存在 / 无权限也能挡住，
        作为 resolve 检查之前的第一道快速防线。
        """
        if path is None:
            return False
        try:
            text = os.fspath(path)
        except (TypeError, ValueError):
            try:
                text = str(path)
            except Exception:
                return False
        parts = text.replace("\\", "/").split("/")
        return any(seg in PROTECTED_DIR_NAMES for seg in parts)

    def _is_inside_protected(self, resolved_path: Path) -> bool:
        """
        已 resolve 的绝对路径是否位于工作目录下某个受保护目录之内（含目录本身）。

        用于 resolve 之后的第二道防线（挡住相对路径 / 大小写 / 8.3 短名等绕过）。
        """
        try:
            base = self._work_dir_resolved
        except AttributeError:
            return False
        for name in PROTECTED_DIR_NAMES:
            guard = (base / name).resolve(strict=False)
            try:
                if resolved_path == guard:
                    return True
                resolved_path.relative_to(guard)
                return True
            except (ValueError, OSError):
                continue
        return False

    def is_protected(self, path) -> bool:
        """
        对外的统一判定：给定路径是否属于「不可被整理/重命名/删除」的受保护内容。

        UI 与 controller 应优先调用本方法，而不是各自硬编码目录名。
        """
        if self._touches_protected(path):
            return True
        try:
            return self._is_inside_protected(Path(path).resolve(strict=False))
        except (OSError, TypeError, ValueError):
            return False

    HISTORY_MAX_ENTRIES = 500  # 防止 history.json 无限膨胀
