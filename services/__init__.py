"""Service 层：把业务逻辑从 UI/对话框中抽离，框架无关（不依赖 tkinter/ui）。

设计范式仿 ``chem.quantum_reaction.runner``：回调驱动、无 UI 依赖。
所有业务 Service 继承 :class:`services.base.ServiceBase`，通过**可注入的调度器**
提交后台任务：
  * 桌面路径 → 默认 :class:`services.base.TkinterScheduler`（复用共享
    ``task_manager`` + ``app.after(0)`` 回主线程）；
  * Web 路径 → ``api.dispatcher.WebDispatcher``（复用 ``api.jobs.JobManager``，
    按 job_id 取消 + WebSocket 事件推送）。

进度/日志统一以 WebSocket 友好的 dict 事件表达，UI 层只负责渲染。
"""

from .advanced_tools_service import AdvancedToolsService
from .analytics_service import AnalyticsService
from .base import (
    EVENT_ERROR,
    EVENT_LOG,
    EVENT_STAGE,
    Scheduler,
    ServiceBase,
    TkinterScheduler,
)
from .openbabel_service import OpenBabelService
from .psi4_scan_service import Psi4ScanService
from .quantum_reaction_service import QuantumReactionService
from .reaction_service import ReactionService
from .sync_service import SyncService

__all__ = [
    "EVENT_ERROR",
    "EVENT_LOG",
    "EVENT_STAGE",
    "AdvancedToolsService",
    "AnalyticsService",
    "OpenBabelService",
    "Psi4ScanService",
    "QuantumReactionService",
    "ReactionService",
    "Scheduler",
    "ServiceBase",
    "SyncService",
    "TkinterScheduler",
]
