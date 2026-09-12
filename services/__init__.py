"""Service 层：把业务逻辑从 UI/对话框中抽离，框架无关（不依赖 tkinter/ui）。

设计范式仿 ``chem.quantum_reaction.runner``：回调驱动、无 UI 依赖。
所有业务 Service 继承 :class:`services.base.ServiceBase`，通过注入的
``task_manager`` 提交后台任务；``on_done``/``on_error`` 由 ``run_async``
自动 ``after(0)`` 回主线程，UI 层只负责提供回调与渲染。
"""

from .advanced_tools_service import AdvancedToolsService
from .base import ServiceBase
from .quantum_reaction_service import QuantumReactionService
from .reaction_service import ReactionService

__all__ = [
    "AdvancedToolsService",
    "QuantumReactionService",
    "ReactionService",
    "ServiceBase",
]
