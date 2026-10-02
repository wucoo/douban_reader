"""``python -m douban_reader.gui`` 入口（带错误兜底）。

用 ``pythonw.exe`` 启动时没有控制台，启动失败会表现成"双击了但什么都没发生"。
所以这里把异常写进 ``logs/gui-error-*.log``，并尽量弹一个提示框。
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _report(exc: BaseException) -> Path | None:
    """把异常写进日志文件，并尽力弹框告知用户。"""
    from ..settings import PROJECT_ROOT

    log_path: Path | None = None
    try:
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"gui-error-{datetime.now():%Y%m%d-%H%M%S}.log"
        log_path.write_text(
            "".join(traceback.format_exception(exc)), encoding="utf-8", newline="\n"
        )
    except OSError:
        log_path = None

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "豆瓣阅读抓取器 · 启动失败",
            f"{type(exc).__name__}: {exc}\n\n详情见：{log_path}",
        )
        root.destroy()
    except Exception:  # noqa: BLE001 - 连 tkinter 都不可用时只能靠日志文件
        pass
    return log_path


def run() -> int:
    """启动界面；任何异常都转成可读的提示与日志。"""
    try:
        from .app import main
    except Exception as exc:  # noqa: BLE001 - 例如缺少 tkinter
        _report(exc)
        return 1
    try:
        return main()
    except Exception as exc:  # noqa: BLE001
        _report(exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
