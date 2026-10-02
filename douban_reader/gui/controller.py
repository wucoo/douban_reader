"""GUI 的无界面部分：表单 ↔ 配置、离线计划、后台任务、日志与进度事件。

刻意与 tkinter 解耦
------------------
所有"能测的逻辑"都在这里：解析表单、装配配置、算计划、跑任务、转发日志。
`tests/test_gui_controller.py` 不需要开窗口就能覆盖它，
界面层（``app.py``）只剩下摆放控件与线程间通信。
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..logging_setup import setup_logging
from ..scraper import Scraper, ScrapeResult
from ..settings import Settings, load_settings

logger = logging.getLogger(__name__)

#: 任务类型 → 中文名（用于日志文件名与界面提示）
KIND_LABELS = {"scrape": "抓取", "rebuild": "重建章节"}


# --------------------------------------------------------------------- 表单


def _to_int(text: str, label: str) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label}必须是整数，当前填的是 {text!r}") from exc


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


@dataclass(slots=True)
class FormValues:
    """界面上那几个输入框的原始值（字符串 + 复选框）。"""

    ebook_id: str = ""
    start_page: str = "1"
    end_page: str = ""
    book_root: str = "book"
    cookie_file: str = "cookie.txt"
    headless: bool = False
    opportunistic: bool = True
    use_search_jump: bool = True
    dedupe_overlap: bool = True
    save_json: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_settings(cls, settings: Settings) -> FormValues:
        """用现有配置（config.toml / config.local.toml）填满表单。"""
        return cls(
            ebook_id=str(settings.ebook_id),
            start_page=str(settings.start_page),
            end_page=str(settings.end_page),
            book_root=settings.book_root,
            cookie_file=settings.cookie_file or "",
            headless=settings.headless,
            opportunistic=settings.opportunistic,
            use_search_jump=settings.use_search_jump,
            dedupe_overlap=settings.dedupe_page_overlap,
            save_json=settings.save_json,
            log_level=settings.log_level.upper(),
        )

    def overrides(self) -> dict[str, Any]:
        """转成 :class:`Settings` 的覆盖字典；输入不合法时抛 :class:`ConfigError`。"""
        ebook_id = self.ebook_id.strip()
        if not ebook_id:
            raise ConfigError(
                "请填写电子书 ID（阅读器地址 read.douban.com/reader/ebook/【这串数字】/）"
            )
        if not ebook_id.isdigit():
            raise ConfigError(f"电子书 ID 应该是一串数字，当前填的是 {ebook_id!r}")
        return {
            "ebook_id": ebook_id,
            "start_page": _to_int(self.start_page, "起始页"),
            "end_page": _to_int(self.end_page, "结束页"),
            "book_root": self.book_root.strip() or "book",
            "cookie_file": self.cookie_file.strip(),
            "headless": self.headless,
            "opportunistic": self.opportunistic,
            "use_search_jump": self.use_search_jump,
            "dedupe_page_overlap": self.dedupe_overlap,
            "save_json": self.save_json,
            "log_level": self.log_level.upper(),
        }

    def to_toml(self) -> str:
        """写成 ``config.local.toml`` 的内容（只含界面管得着的键）。"""
        lines = [
            "# 由图形界面（run_gui.cmd）保存：这些值会覆盖 config.toml。",
            "# 想恢复 config.toml 的默认值，删掉本文件即可。",
            f'ebook_id = "{self.ebook_id.strip()}"',
            f"start_page = {_to_int(self.start_page, '起始页')}",
            f"end_page = {_to_int(self.end_page, '结束页')}",
            f'book_root = "{self.book_root.strip() or "book"}"',
            f'cookie_file = "{self.cookie_file.strip()}"',
            f"headless = {_toml_bool(self.headless)}",
            f"opportunistic = {_toml_bool(self.opportunistic)}",
            f"use_search_jump = {_toml_bool(self.use_search_jump)}",
            f"dedupe_page_overlap = {_toml_bool(self.dedupe_overlap)}",
            f"save_json = {_toml_bool(self.save_json)}",
            f'log_level = "{self.log_level.upper()}"',
            "",
        ]
        return "\n".join(lines)


def build_settings(
    values: FormValues,
    *,
    config_file: Path | None = None,
    use_local: bool = True,
) -> Settings:
    """表单 + 配置文件 → 最终 :class:`Settings`（与命令行共用同一套装配逻辑）。"""
    return load_settings(config_file=config_file, use_local=use_local, overrides=values.overrides())


# --------------------------------------------------------------------- 计划


@dataclass(slots=True)
class PlanSummary:
    """离线计划：不启动浏览器就能算出来的东西。"""

    root_dir: Path
    cached_pages: int
    pending: list[int] = field(default_factory=list)

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    def describe(self) -> str:
        if not self.pending:
            return f"已缓存 {self.cached_pages} 页 · 范围内全部抓完，无需联网"
        preview = ", ".join(str(p) for p in self.pending[:15])
        more = " ..." if len(self.pending) > 15 else ""
        return f"已缓存 {self.cached_pages} 页 · 待抓 {self.pending_count} 页（{preview}{more}）"


def build_plan(settings: Settings) -> PlanSummary:
    """只读地盘一次计划（等价于命令行的 ``--dry-run``）。"""
    plan = Scraper(settings).plan()
    return PlanSummary(
        root_dir=Path(str(plan["root_dir"])),
        cached_pages=int(str(plan["cached_pages"])),
        pending=list(plan["todo"]),  # type: ignore[arg-type]
    )


def latest_log_file(log_dir: Path) -> Path | None:
    """日志目录里最新的一份日志（"打开日志"按钮用）。"""
    if not log_dir.is_dir():
        return None
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# --------------------------------------------------------------------- 事件


@dataclass(slots=True)
class TaskEvent:
    """后台线程 → 界面主线程的一条消息。"""

    kind: str  # "log" | "progress" | "done"
    text: str = ""
    level: str = "INFO"
    done: int = 0
    total: int = 0
    result: ScrapeResult | None = None


class QueueLogHandler(logging.Handler):
    """把日志记录丢进队列，由界面主线程取出来显示。

    为什么绕这一圈：tkinter 控件只能从主线程访问，
    而抓取跑在后台线程里，直接往 Text 里写会随机崩。
    """

    def __init__(self, events: queue.Queue[TaskEvent]) -> None:
        super().__init__()
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._events.put_nowait(
                TaskEvent(kind="log", level=record.levelname, text=self.format(record))
            )
        except Exception:  # noqa: BLE001 - 日志绝不能反过来把主流程搞崩
            pass


# --------------------------------------------------------------------- 任务


class TaskRunner:
    """在后台线程里跑抓取/聚合，支持停止。

    界面用 ``after()`` 轮询 :attr:`events`：

    * ``kind == "log"``      → 追加到日志区
    * ``kind == "progress"`` → 更新进度条（done / total）
    * ``kind == "done"``     → 收尾：打印摘要、恢复按钮
    """

    def __init__(self) -> None:
        self.events: queue.Queue[TaskEvent] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._log_handler = QueueLogHandler(self.events)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self) -> None:
        """请求停止：当前页抓完就停，已抓内容照常聚合落盘。"""
        self._stop.set()

    def wait(self, timeout: float | None = None) -> bool:
        """等待当前任务结束；返回是否已结束。测试与退出前清理时用得上。"""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def start(self, settings: Settings, *, kind: str = "scrape") -> None:
        """启动任务。``kind`` 取 ``"scrape"``（联网抓取）或 ``"rebuild"``（离线重建）。"""
        if self.is_running:
            raise RuntimeError("已有任务在运行")
        self._stop.clear()

        total = self._pending_total(settings) if kind == "scrape" else 0
        setup_logging(
            settings.log_level,
            settings.log_path,
            prefix=kind,
            console=False,  # GUI 可能由 pythonw 启动，此时 sys.stdout 是 None
            extra_handlers=[self._log_handler],
        )
        logger.info(
            "任务开始：%s（电子书 %s，范围 %d-%d）",
            KIND_LABELS.get(kind, kind),
            settings.ebook_id,
            settings.start_page,
            settings.end_page,
        )
        self._thread = threading.Thread(
            target=self._work, args=(settings, kind, total), name=f"douban-{kind}", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _pending_total(settings: Settings) -> int:
        """进度条上限 = 待抓页数；算不出来就返回 0（界面改为不确定进度）。"""
        try:
            return len(Scraper(settings).plan()["todo"])  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - 只是个进度条上限，不值得报错
            return 0

    def _work(self, settings: Settings, kind: str, total: int) -> None:
        done = 0

        def on_page(page_no: int, intentional: bool) -> None:
            nonlocal done
            done += 1
            self.events.put_nowait(TaskEvent(kind="progress", done=done, total=total))

        scraper = Scraper(settings, on_page=on_page, stop_requested=self._stop.is_set)
        try:
            result = scraper.rebuild() if kind == "rebuild" else scraper.run()
        except Exception as exc:  # noqa: BLE001 - 兜底，保证界面一定能收到 done
            logger.exception("任务异常结束")
            result = ScrapeResult(
                root_dir=settings.book_dir(),
                ebook_id=str(settings.ebook_id),
                book_title="",
                error=f"{type(exc).__name__}: {exc}",
                error_exc=exc,
            )
        self.events.put_nowait(TaskEvent(kind="done", result=result, done=done, total=total))
