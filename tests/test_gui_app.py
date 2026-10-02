"""GUI 界面层烟囱测试（标记 ``gui``）：真实创建 Tk 窗口，但**隐藏**并立即销毁。

只验证"界面能搭起来、关键动作不炸"，不点任何会弹模态框或打开资源管理器的按钮。
没有图形环境时自动跳过。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from douban_reader.errors import ExitCode, NavigationError
from douban_reader.gui.app import GuiApp
from douban_reader.scraper import ScrapeResult
from douban_reader.settings import Settings

pytestmark = pytest.mark.gui


def _ensure_tcl_library() -> None:
    """显式指定 Tcl/Tk 库路径。

    某些环境下 Tcl 的库搜索会**偶发**失败（报 "Can't find a usable init.tcl"，
    但文件其实存在），于是界面测试会随机被跳过。应用本身不受影响
    （GUI 在同样环境下能正常启动），这里只是让测试结果稳定、可复现。
    """
    if os.environ.get("TCL_LIBRARY"):
        return
    base = Path(sys.base_prefix) / "tcl"
    for env_name, folder, marker in (
        ("TCL_LIBRARY", "tcl8.6", "init.tcl"),
        ("TK_LIBRARY", "tk8.6", "tk.tcl"),
    ):
        candidate = base / folder
        if (candidate / marker).is_file():
            os.environ[env_name] = str(candidate)


@pytest.fixture
def root() -> Any:
    tk = pytest.importorskip("tkinter", reason="没有 tkinter")
    _ensure_tcl_library()
    window = None
    last_error: Exception | None = None
    # Tcl 首次初始化偶发失败（冷启动/杀软扫描 DLL），重试一次即可稳定
    for _ in range(2):
        try:
            window = tk.Tk()
            break
        except tk.TclError as exc:
            last_error = exc
            time.sleep(0.3)
    if window is None:  # pragma: no cover - 无图形环境（如无 X11 的服务器）
        pytest.skip(f"没有可用的图形环境：{last_error}")
    window.withdraw()  # 别真的弹到用户屏幕上
    yield window
    window.destroy()


@pytest.fixture
def app(root: Any, tmp_path: Path) -> GuiApp:
    settings = Settings(
        ebook_id="1465780",
        start_page=1,
        end_page=7,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )
    instance = GuiApp(root, settings)
    instance.vars["open_when_done"].set(False)  # 免得测试真的打开资源管理器
    root.update_idletasks()
    return instance


def test_window_builds_and_loads_form(app: GuiApp, root: Any) -> None:
    assert root.title() == "抓取界面" or "抓取器" in root.title()
    assert app.vars["ebook_id"].get() == "1465780"
    assert app.vars["start_page"].get() == "1"
    assert app.vars["end_page"].get() == "7"
    # 初始化时应当已经算过一次计划并写进状态栏
    assert "待抓 7 页" in app.status_var.get()
    assert "输出目录" in app.detail_var.get()


def test_form_changes_refresh_plan(app: GuiApp) -> None:
    app.vars["end_page"].set("3")
    app.refresh_plan(log_it=False)
    assert "待抓 3 页" in app.status_var.get()


def test_invalid_form_shows_status_instead_of_crashing(app: GuiApp) -> None:
    app.vars["start_page"].set("abc")
    app.refresh_plan(log_it=False)
    assert "参数有问题" in app.status_var.get()
    assert app._collect_settings(show_error=False) is None


def test_buttons_toggle_while_running(app: GuiApp) -> None:
    app._set_running(True)
    assert str(app.btn_start.cget("state")) == "disabled"
    assert str(app.btn_stop.cget("state")) == "normal"

    app._set_running(False)
    assert str(app.btn_start.cget("state")) == "normal"
    assert str(app.btn_stop.cget("state")) == "disabled"


def test_log_and_progress_updates(app: GuiApp) -> None:
    app._append_log("普通一行", "INFO")
    app._append_log("出错了", "ERROR")
    text = app.log_text.get("1.0", "end")
    assert "普通一行" in text and "出错了" in text

    from douban_reader.gui.controller import TaskEvent

    app._update_progress(TaskEvent(kind="progress", done=3, total=7))
    assert "3 / 7" in app.status_var.get()
    assert float(app.progress.cget("value")) == 3.0


def test_handle_done_success_reports_summary(app: GuiApp) -> None:
    """成功结束时：状态栏、日志、按钮都要复位。"""
    from douban_reader.gui.controller import TaskEvent

    result = ScrapeResult(
        root_dir=Path(app.base_settings.book_dir()),
        ebook_id="1465780",
        book_title="示例书名",
        pages_fetched=7,
        cached_pages=7,
        intentional_pages=7,
    )
    app._handle_done(TaskEvent(kind="done", result=result, done=7, total=7))

    assert "完成" in app.status_var.get()
    assert str(app.btn_start.cget("state")) == "normal"
    assert "本次正式抓取  7 页" in app.log_text.get("1.0", "end")


def test_handle_done_partial_and_error(app: GuiApp) -> None:
    from douban_reader.gui.controller import TaskEvent

    partial = ScrapeResult(
        root_dir=Path(app.base_settings.book_dir()),
        ebook_id="1465780",
        book_title="书",
        incomplete_pages=[5, 6],
    )
    app._handle_done(TaskEvent(kind="done", result=partial))
    assert "部分完成" in app.status_var.get()

    failed = ScrapeResult(
        root_dir=Path(app.base_settings.book_dir()),
        ebook_id="1465780",
        book_title="书",
        error="NavigationError: 翻页卡住",
        error_exc=NavigationError("翻页卡住"),
    )
    app._handle_done(TaskEvent(kind="done", result=failed))
    assert "失败" in app.status_var.get()
    assert failed.exit_code == ExitCode.NAVIGATION

    # 只有错误文本、没有异常对象时也绝不能算成功
    text_only = ScrapeResult(
        root_dir=Path(app.base_settings.book_dir()),
        ebook_id="1465780",
        book_title="书",
        error="说不清的错",
    )
    assert text_only.exit_code == ExitCode.UNEXPECTED
