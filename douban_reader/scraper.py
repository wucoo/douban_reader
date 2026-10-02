"""抓取编排：计划 → 抓取 → 聚合。

这里是唯一"知道全流程"的地方，但它只做策略，不做细节：
解析交给 :mod:`douban_reader.parsing`，导航交给 :mod:`douban_reader.reader`，
落盘交给 :mod:`douban_reader.storage`，分章交给 :mod:`douban_reader.chapters`。

断点续抓语义
------------
``pages/pN.json`` 是唯一事实源，``intentional`` 区分两种页：

* ``True``  —— 落在配置的 ``[start, end]`` 范围内，正式抓取，参与章节合成；
* ``False`` —— 翻页路上"顺手"缓存的页，只作缓存（``opportunistic`` 开关控制）。

因此重跑只补缺失的正式页，已完成的页不会重复请求。

两种运行模式
------------
* :meth:`Scraper.run`      —— 完整流程，需要浏览器；
* :meth:`Scraper.rebuild`  —— 只聚合已缓存内容，**不启动浏览器、不联网**，
  用于验证解析/分章改动是否等价，也是"我只想重新生成章节"时的快捷方式。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import chapters as chapter_ops
from . import cookies as cookie_ops
from .browser import Browser
from .errors import (
    ConfigError,
    CookieError,
    CookieExpiredError,
    ExitCode,
    NavigationError,
    ReaderError,
)
from .models import Chapter, Page
from .parsing import PageParser
from .reader import Reader
from .settings import Settings
from .storage import BookStore, WrittenBook

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScrapeResult:
    """一次抓取/聚合的结构化结果（cli 用它打印摘要并决定退出码）。"""

    root_dir: Path
    ebook_id: str
    book_title: str
    pages_fetched: int = 0
    cached_pages: int = 0
    intentional_pages: int = 0
    cache_only_pages: list[int] = field(default_factory=list)
    incomplete_pages: list[int] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    written: WrittenBook | None = None
    error: str | None = None
    error_exc: Exception | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.error is None and not self.incomplete_pages

    @property
    def exit_code(self) -> ExitCode:
        exc = self.error_exc
        if exc is not None:
            if isinstance(exc, CookieExpiredError):
                return ExitCode.COOKIE_EXPIRED
            if isinstance(exc, (ConfigError, CookieError)):
                return ExitCode.CONFIG
            if isinstance(exc, NavigationError):
                return ExitCode.NAVIGATION
            return ExitCode.UNEXPECTED
        if self.incomplete_pages:
            return ExitCode.PARTIAL
        return ExitCode.OK

    def summary_lines(self) -> list[str]:
        """给人看的摘要（cli 负责缩进与颜色）。"""
        lines = [
            f"输出目录      {self.root_dir}",
            f"本次正式抓取  {self.pages_fetched} 页",
            f"缓存总页数    {self.cached_pages}",
            f"其中正式页    {self.intentional_pages}",
            f"仅缓存(顺手)  {len(self.cache_only_pages)}",
            f"输出章节      {sum(1 for c in self.chapters if c.has_content)} 个",
        ]
        if self.written is not None:
            lines.append(f"txt / json    {len(self.written.txt)} / {len(self.written.json)}")
            if self.written.index is not None:
                lines.append(f"索引文件      {self.written.index}")
        if self.incomplete_pages:
            preview = ", ".join(str(p) for p in self.incomplete_pages[:20])
            more = "..." if len(self.incomplete_pages) > 20 else ""
            lines.append(f"未完成页      {len(self.incomplete_pages)} 页（{preview}{more}）")
        if self.error:
            lines.append(f"错误          {self.error}")
        return lines


class Scraper:
    """一次运行的执行者。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------ 离线：只聚合

    def rebuild(self) -> ScrapeResult:
        """只根据 ``pages/`` 缓存重新生成章节，不启动浏览器。"""
        settings = self.settings
        store = self._store(settings.book_dir())
        pages = store.load_pages()
        logger.info("聚合模式：共 %d 页缓存（不访问网络）", len(pages))
        # 离线聚合不评价"范围是否抓完"，否则一条 --rebuild-only 会被判成部分失败
        return self._finalize(
            store, pages, fetched=0, book_title=self._fallback_title(), report_incomplete=False
        )

    # ------------------------------------------------------------ 在线：完整抓取

    def run(self) -> ScrapeResult:
        """打开阅读器抓取 ``[start, end]``，然后聚合成章节。"""
        settings = self.settings
        root = settings.book_dir(settings.book_title)
        try:
            cookies = cookie_ops.load_cookies(
                cookie_file=settings.cookie_path, cookie_string=settings.cookie_string
            )
            with Browser(
                stealth_js=settings.stealth_js_path,
                headless=settings.headless,
                page_load_timeout=settings.page_load_timeout,
                element_timeout=settings.element_timeout,
                user_data_dir=settings.user_data_path,
                chrome_driver_path=settings.chrome_driver_path,
                cookies=cookies,
                block_resources=settings.block_resources,
                blocked_urls=settings.blocked_urls,
                sleep_range=settings.sleep_range,
            ) as browser:
                parser = PageParser(browser)
                reader = Reader(browser, settings)
                reader.open()

                title = reader.get_book_title()
                root = settings.book_dir(title)
                store = self._store(root)
                pages = store.load_pages()
                logger.info(
                    "书名：%s ｜ 总页数：%s ｜ 已缓存：%d 页",
                    title,
                    reader.total_pages(),
                    len(pages),
                )
                return self._scrape(reader, parser, store, pages, title)
        except Exception as exc:  # noqa: BLE001 - 统一转成结构化结果，交由 cli 决定退出码
            if isinstance(exc, ReaderError):
                logger.error("抓取失败：%s", exc)
            else:
                logger.exception("抓取过程中出现未预期异常")
            return ScrapeResult(
                root_dir=root,
                ebook_id=settings.ebook_id,
                book_title=self._fallback_title(),
                error=f"{type(exc).__name__}: {exc}",
                error_exc=exc,
            )

    def _scrape(
        self,
        reader: Reader,
        parser: PageParser,
        store: BookStore,
        pages: dict[int, Page],
        book_title: str,
    ) -> ScrapeResult:
        settings = self.settings
        todo = [
            p
            for p in range(settings.start_page, settings.end_page + 1)
            if not self._is_intentional(pages, p)
        ]
        todo_set = set(todo)
        fetched = 0
        error: Exception | None = None

        logger.info(
            "[计划] 范围 %d-%d，已缓存 %d 页，待正式抓 %d 页",
            settings.start_page,
            settings.end_page,
            len(pages),
            len(todo),
        )

        if not todo:
            logger.info("[跳过] 范围内所有页都已是正式页，直接聚合")
            return self._finalize(store, pages, fetched=0, book_title=book_title)

        http = reader.browser.to_requests_session()
        low, high = min(todo), max(todo)

        def on_route(page_no: int) -> None:
            """翻页途中的回调：目标页正式抓，路过的页按需顺手缓存。"""
            nonlocal fetched
            if page_no in todo_set:
                if not self._is_intentional(pages, page_no) and self._capture(
                    reader, parser, store, pages, http, page_no, intentional=True
                ):
                    fetched += 1
                return
            if not settings.opportunistic or page_no in pages:
                return
            self._capture(reader, parser, store, pages, http, page_no, intentional=False)

        try:
            logger.info("[前往] p%d%s", low, "（沿途顺手缓存）" if settings.opportunistic else "")
            reader.goto_page_smart(low, on_page=on_route)

            current = low
            while True:
                if not self._is_intentional(pages, current) and self._capture(
                    reader, parser, store, pages, http, current, intentional=True
                ):
                    fetched += 1

                if current >= high:
                    break
                try:
                    reader.next_page()
                except Exception as exc:  # noqa: BLE001 - 翻页中断即停止扫描
                    logger.warning("翻页失败（p%d -> p%d）：%s", current, current + 1, exc)
                    break
                current += 1
                reader.browser.sleep_random()
        except Exception as exc:  # noqa: BLE001 - 记录后仍要聚合已完成的部分
            error = exc
            if isinstance(exc, ReaderError):
                logger.error("抓取中断：%s", exc)
            else:
                logger.exception("抓取中断（未预期异常）")
        finally:
            result = self._finalize(
                store, pages, fetched=fetched, book_title=book_title, error=error
            )

        return result

    def _capture(
        self,
        reader: Reader,
        parser: PageParser,
        store: BookStore,
        pages: dict[int, Page],
        http: Any,
        page_no: int,
        *,
        intentional: bool,
    ) -> bool:
        """解析当前页并落盘；校验页码，失败重试。"""
        settings = self.settings
        for attempt in range(1, settings.capture_retries + 1):
            try:
                page = parser.parse_current_page(retries=settings.parse_retries)
            except Exception as exc:  # noqa: BLE001 - 解析失败按重试处理
                logger.warning(
                    "解析 p%d 失败（第 %d/%d 次）：%s",
                    page_no,
                    attempt,
                    settings.capture_retries,
                    exc,
                )
                reader.browser.sleep_random((0.5, 1.0))
                continue

            if page.page != page_no:
                logger.warning(
                    "期望 p%d 实得 p%d，重试（%d/%d）",
                    page_no,
                    page.page,
                    attempt,
                    settings.capture_retries,
                )
                reader.browser.sleep_random((0.5, 1.0))
                continue

            store.attach_images(page, http)
            written = store.save_page(page, intentional=intentional)
            if not written and not page.intentional:
                # 磁盘上已有正式版本，本次读到的是缓存版本
                page.intentional = True
            pages[page.page] = page
            logger.info("  [%s] p%d %r", "正式" if intentional else "缓存", page_no, page.title)
            return True

        logger.error("p%d 重试 %d 次仍未抓到，跳过", page_no, settings.capture_retries)
        return False

    # ------------------------------------------------------------ 聚合与汇总

    def _finalize(
        self,
        store: BookStore,
        pages: dict[int, Page],
        *,
        fetched: int,
        book_title: str,
        error: Exception | None = None,
        report_incomplete: bool = True,
    ) -> ScrapeResult:
        settings = self.settings
        chapters = chapter_ops.build_chapters(
            pages.values(), dedupe_overlap=settings.dedupe_page_overlap
        )
        intentional = sorted(p for p in pages if pages[p].intentional)
        cache_only = sorted(p for p in pages if not pages[p].intentional)
        incomplete = (
            [p for p in range(settings.start_page, settings.end_page + 1) if p not in intentional]
            if report_incomplete
            else []
        )

        written = store.write_book(
            chapters,
            ebook_id=settings.ebook_id,
            book_title=book_title,
            cached_pages=len(pages),
            intentional_pages=len(intentional),
            covered_intentional=intentional,
            covered_cache_only=cache_only,
        )

        result = ScrapeResult(
            root_dir=store.root,
            ebook_id=settings.ebook_id,
            book_title=book_title,
            pages_fetched=fetched,
            cached_pages=len(pages),
            intentional_pages=len(intentional),
            cache_only_pages=cache_only,
            incomplete_pages=incomplete,
            chapters=chapters,
            written=written,
            error=f"{type(error).__name__}: {error}" if error else None,
            error_exc=error,
        )
        if result.incomplete_pages:
            logger.warning(
                "范围内还有 %d 页不是正式页，重跑本命令即可继续补抓",
                len(result.incomplete_pages),
            )
        return result

    # ------------------------------------------------------------ 小工具

    def plan(self) -> dict[str, object]:
        """只读地算一次抓取计划（供 --dry-run 使用，不启动浏览器）。"""
        settings = self.settings
        store = self._store(settings.book_dir(settings.book_title))
        pages = store.load_pages()
        todo = [
            p
            for p in range(settings.start_page, settings.end_page + 1)
            if not self._is_intentional(pages, p)
        ]
        return {
            "root_dir": store.root,
            "cached_pages": len(pages),
            "todo": todo,
        }

    def _store(self, root: Path) -> BookStore:
        return BookStore(
            root, save_json=self.settings.save_json, image_timeout=self.settings.image_timeout
        )

    def _fallback_title(self) -> str:
        settings = self.settings
        if settings.book_title:
            return settings.book_title
        name = settings.book_dir().name
        suffix = f"_{settings.ebook_id}"
        return name[: -len(suffix)] if name.endswith(suffix) else name

    @staticmethod
    def _is_intentional(pages: dict[int, Page], page_no: int) -> bool:
        page = pages.get(page_no)
        return bool(page and page.intentional)
