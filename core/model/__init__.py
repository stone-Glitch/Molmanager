"""MolManager 核心模型：原 core/model.py 拆分为子系统 mixin，组合保持 MolManagerModel 接口不变。"""
from ._common import *  # noqa: F401,F403
from ._common import _is_windows_junction  # noqa: F401  # 向后兼容旧别名
from ._base import MolManagerModelBase
from ._backup import BackupMixin
from ._mapping import MappingMixin
from ._scan import ScanMixin
from ._fileops import FileOpsMixin
from ._history import HistoryMixin
from ._chem import ChemMixin


class MolManagerModel(
    MolManagerModelBase,
    BackupMixin,
    MappingMixin,
    ScanMixin,
    FileOpsMixin,
    HistoryMixin,
    ChemMixin,
):
    """组合后的核心模型。所有原 MolManagerModel 的公开方法均通过 mixin 继承而来。"""
    pass
