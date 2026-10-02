"""GUI 无界面层的测试：表单↔配置、离线计划、事件队列、后台任务。

不需要图形环境，也不需要浏览器。
"""

from __future__ import annotations

import json
import logging
import queue
import tomllib
from pathlib import Path

import pytest

from douban_reader.errors import ConfigError
from douban_reader.gui.controller import (
    FormValues,
    PlanSummary,
    QueueLogHandler,
    TaskEvent,
    TaskRunner,
    build_plan,
    build_settings,
    latest_log_file,
)
from douban_reader.settings import Settings, settings_from_mapping


@pytest.fixture(autouse=True)
def restore_root_logger() -> object:
    """TaskRunner.start() 会重设根 logger，测试后恢复，免得污染其它测试。"""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def make_book(tmp_path: Path, *, pages: int = 3, intentional: int = 1) -> Path:
    """造一个带 pages/ 缓存的书稿目录。"""
    root = tmp_path / "book" / "测试书_1465780"
    pages_dir = root / "pages"
    pages_dir.mkdir(parents=True)
    for page_no in range(1, pages + 1):
        payload = {
            "page": page_no,
            "title": "章1",
            "paragraphs": [{"type": "text", "text": f"正文{page_no}"}],
            "intentional": page_no <= intentional,
        }
        (pages_dir / f"p{page_no}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    return root


# --------------------------------------------------------------------- 表单


def test_form_roundtrip_from_settings() -> None:
    settings = Settings(ebook_id="1465780", start_page=2, end_page=9, headless=True)
    values = FormValues.from_settings(settings)

    assert values.ebook_id == "1465780"
    assert values.start_page == "2"
    assert values.end_page == "9"
    assert values.headless is True
    assert values.overrides()["ebook_id"] == "1465780"
    assert values.overrides()["start_page"] == 2


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("ebook_id", "", "请填写电子书 ID"),
        ("ebook_id", "abc123", "应该是一串数字"),
        ("start_page", "", "起始页必须是整数"),
        ("end_page", "abc", "结束页必须是整数"),
    ],
)
def test_form_rejects_bad_input(field: str, value: str, match: str) -> None:
    values = FormValues(ebook_id="1465780", start_page="1", end_page="9")
    setattr(values, field, value)
    with pytest.raises(ConfigError, match=match):
        values.overrides()


def test_form_toml_is_loadable_by_settings() -> None:
    """界面保存的 config.local.toml 必须能被命令行那套装配逻辑读回来。"""
    values = FormValues(
        ebook_id="1465780",
        start_page="1",
        end_page="41",
        book_root="book",
        cookie_file="cookie.txt",
        headless=True,
        opportunistic=False,
        dedupe_overlap=False,
    )
    payload = tomllib.loads(values.to_toml())
    settings = settings_from_mapping(payload)

    assert settings.ebook_id == "1465780"
    assert (settings.start_page, settings.end_page) == (1, 41)
    assert settings.headless is True
    assert settings.opportunistic is False
    assert settings.dedupe_page_overlap is False


def test_build_settings_keeps_config_file_values(tmp_path: Path) -> None:
    """表单只管它自己的键，config.toml 里其它设置（如 sleep_min）要保留。"""
    config = tmp_path / "config.toml"
    config.write_text("sleep_min = 4.0\nsleep_max = 8.0\nstart_page = 1\nend_page = 200\n")
    values = FormValues(ebook_id="1465780", start_page="2", end_page="7")

    settings = build_settings(values, config_file=config, use_local=False)

    assert settings.sleep_range == (4.0, 8.0)
    assert (settings.start_page, settings.end_page) == (2, 7)


# --------------------------------------------------------------------- 计划


def test_build_plan_counts_cached_and_pending(tmp_path: Path) -> None:
    make_book(tmp_path, pages=5, intentional=2)
    settings = Settings(
        ebook_id="1465780",
        start_page=1,
        end_page=5,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )

    plan = build_plan(settings)

    assert isinstance(plan, PlanSummary)
    assert plan.cached_pages == 5
    assert plan.pending == [3, 4, 5]
    assert plan.pending_count == 3
    assert "待抓 3 页" in plan.describe()
    assert plan.root_dir.name == "测试书_1465780"


def test_build_plan_on_empty_book(tmp_path: Path) -> None:
    settings = Settings(
        ebook_id="999",
        start_page=1,
        end_page=4,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )
    plan = build_plan(settings)
    assert plan.cached_pages == 0
    assert plan.pending == [1, 2, 3, 4]


def test_plan_describe_when_nothing_pending(tmp_path: Path) -> None:
    make_book(tmp_path, pages=3, intentional=3)
    settings = Settings(
        ebook_id="1465780",
        start_page=1,
        end_page=3,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )
    assert "无需联网" in build_plan(settings).describe()


def test_latest_log_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "run-1.log").write_text("a", encoding="utf-8")
    newer = log_dir / "rebuild-2.log"
    newer.write_text("b", encoding="utf-8")
    assert latest_log_file(log_dir) == newer
    assert latest_log_file(tmp_path / "missing") is None


# --------------------------------------------------------------------- 事件与任务


def test_queue_log_handler_forwards_records() -> None:
    events: queue.Queue[TaskEvent] = queue.Queue()
    handler = QueueLogHandler(events)
    handler.setFormatter(logging.Formatter("%(levelname)s|%(message)s"))

    record = logging.LogRecord(
        name="douban_reader.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="小心",
        args=(),
        exc_info=None,
    )
    handler.emit(record)

    event = events.get_nowait()
    assert event.kind == "log"
    assert event.level == "WARNING"
    assert event.text == "WARNING|小心"


def test_task_runner_rebuild_reports_done(tmp_path: Path) -> None:
    """离线重建任务：日志与结果都要经事件队列回到界面。"""
    make_book(tmp_path, pages=3, intentional=3)
    settings = Settings(
        ebook_id="1465780",
        start_page=1,
        end_page=3,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )
    runner = TaskRunner()

    runner.start(settings, kind="rebuild")
    assert runner.wait(timeout=30) is True
    assert runner.is_running is False

    events: list[TaskEvent] = []
    while True:
        try:
            events.append(runner.events.get_nowait())
        except queue.Empty:
            break

    logs = [e for e in events if e.kind == "log"]
    done = [e for e in events if e.kind == "done"]
    assert any("任务开始" in e.text for e in logs)
    assert any("聚合完成" in e.text for e in logs)
    assert len(done) == 1
    result = done[0].result
    assert result is not None
    assert result.intentional_pages == 3
    assert result.written is not None and len(result.written.txt) == 1

    # 日志同时落了文件（GUI 用 pythonw 启动时看不到控制台，只能靠文件）
    log_files = list((tmp_path / "logs").glob("rebuild-*.log"))
    assert log_files, "应该写出 rebuild-*.log"
    assert "任务开始" in log_files[0].read_text(encoding="utf-8")


def test_task_runner_pending_total_zero_when_uncertain(tmp_path: Path, monkeypatch: object) -> None:
    """算不出计划时返回 0，界面据此改用"不确定进度条"，而不是崩掉。"""
    settings = Settings(
        ebook_id="999",
        start_page=1,
        end_page=3,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )

    def boom(self: object) -> dict[str, object]:
        raise OSError("模拟读目录失败")

    monkeypatch.setattr("douban_reader.gui.controller.Scraper.plan", boom)  # type: ignore[attr-defined]
    assert TaskRunner()._pending_total(settings) == 0
