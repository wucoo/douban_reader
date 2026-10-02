"""图形界面（tkinter，标准库，零新增依赖）。

说明
----
* 本包刻意**不在导入时**加载 :mod:`tkinter`（用 PEP 562 按需加载），
  这样在没有图形环境的机器上 ``import douban_reader.gui`` 不会炸，
  真正的启动入口是 ``gui/__main__.py``（带错误兜底）。
* 启动方式：双击 ``run_gui.cmd``，或 ``python -m douban_reader.gui``。

界面分成两层：

* :mod:`douban_reader.gui.controller` —— 表单 ↔ 配置、离线计划、后台任务、事件队列（无 tkinter，可单测）；
* :mod:`douban_reader.gui.app`        —— tkinter 控件与线程间通信。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from .app import GuiApp, main

__all__ = ["GuiApp", "main"]


def __getattr__(name: str) -> Any:
    """PEP 562 惰性属性：用到 ``GuiApp`` / ``main`` 时才 import tkinter 相关实现。"""
    if name in __all__:
        from . import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
