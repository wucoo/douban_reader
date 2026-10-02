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
from collections.abc import Callable
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
    stopped: bool = False  # 是否被手动停止（GUI 的"停止"按钮）

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
        if self.error:
            # 只有错误文本、没有异常对象时也绝不能算成功
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
        if self.stopped:
            lines.append("运行状态      已手动停止（已抓内容已保留，重跑即续抓）")
        if self.error:
            lines.append(f"错误          {self.error}")
        return lines


class Scraper:
    """一次运行的执行者。

    Args:
        settings: 本次运行的配置。
        on_page: 每成功落盘一个**正式页**时回调 ``(页码, intentional)``。
            仅缓存的页不回调。GUI 用它更新进度条。
        stop_requested: 返回 True 时尽快停下来（GUI 的"停止"按钮）。
            已抓到的内容照常聚合，未完成的页会出现在 ``incomplete_pages`` 里。
    """

    #: 连续失败达到这个次数就停下本轮（站点异常时不要反复敲）
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        settings: Settings,
        *,
        on_page: Callable[[int, bool], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self._on_page = on_page
        self._stop_requested = stop_requested or (lambda: False)

    def _notify_page(self, page_no: int, intentional: bool) -> None:
        if self._on_page is None or not intentional:
            return
        try:
            self._on_page(page_no, intentional)
        except Exception as exc:  # noqa: BLE001 - 进度回调不能影响抓取
            logger.debug("进度回调失败：%s", exc)

    def _should_stop(self) -> bool:
        try:
            return bool(self._stop_requested())
        except Exception as exc:  # noqa: BLE001
            logger.debug("停止检查失败：%s", exc)
            return False

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
            # 先离线看一眼计划：范围内都抓过了，就没必要为了"读一次书名"加载整个阅读器
            if root.is_dir():
                cached = self._store(root).load_pages()
                if not self._has_pending(cached):
                    logger.info(
                        "[跳过] %d-%d 已经全部是正式页，直接聚合（不启动浏览器）",
                        settings.start_page,
                        settings.end_page,
                    )
                    return self._finalize(
                        self._store(root),
                        cached,
                        fetched=0,
                        book_title=self._fallback_title(),
                    )
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

    # ------------------------------------------------------------ 抓取走位

    def _scrape(
        self,
        reader: Reader,
        parser: PageParser,
        store: BookStore,
        pages: dict[int, Page],
        book_title: str,
    ) -> ScrapeResult:
        """抓取 ``[start, end]``，目标是**每页最多只加载一次**。

        走位策略（与原实现的关键差别）
        ------------------------------
        原实现：先跳到 ``min(todo)``，再从 ``min`` 一页页走到 ``max``。
        于是"阅读器停在 p10、要抓 1-7"这种常见情形会倒着抓一遍、
        **再正向空翻一遍**：已经抓过的页被浏览器重新加载 6 次，纯属浪费请求。

        现在每一步只朝"下一个还没抓到的页"走：

        * 中间那些页都已抓过（或关掉了顺手缓存）→ 直接搜索跳页过去，不空翻；
        * 到达目标后只顺着**连续待抓**的页往前走，碰到已抓过的页/范围边界立刻停；
        * 走位或抓取失败记进 ``failed`` 并跳过该页，连续失败
          :attr:`MAX_CONSECUTIVE_FAILURES` 次才停下本轮（重跑即续抓）。

        ``on_route`` 仍负责"顺手缓存"路上经过的页，但仅缓存的页**不下载插图**：
        那些页可能永远用不到，为它们发请求是纯浪费，等它转正时再补。
        """
        settings = self.settings
        todo = [
            p
            for p in range(settings.start_page, settings.end_page + 1)
            if not self._is_intentional(pages, p)
        ]
        todo_set = set(todo)
        fetched = 0
        error: Exception | None = None
        stopped = False

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
        failed: set[int] = set()

        def is_pending(page_no: int) -> bool:
            return (
                page_no in todo_set
                and page_no not in failed
                and not self._is_intentional(pages, page_no)
            )

        def next_target() -> int | None:
            for page_no in range(low, high + 1):
                if is_pending(page_no):
                    return page_no
            return None

        def on_route(page_no: int) -> None:
            """翻页途中经过的页：待抓的顺手正式抓，范围外的按需顺手缓存。"""
            nonlocal fetched
            if is_pending(page_no):
                if self._capture(reader, parser, store, pages, http, page_no, intentional=True):
                    fetched += 1
                return
            if not settings.opportunistic or page_no in pages or page_no in failed:
                return
            self._capture(reader, parser, store, pages, http, page_no, intentional=False)

        try:
            consecutive = 0
            while True:
                if self._should_stop():
                    stopped = True
                    logger.info(
                        "[停止] 收到停止请求：本轮已抓 %d 页，未完成的页留待下次续抓", fetched
                    )
                    break

                target = next_target()
                if target is None:
                    break

                current = reader.current_page()
                # 走这一段若是"白走"（中间页全抓过了，或干脆关掉了顺手缓存），
                # 就强制走搜索跳页：max_direct=0 会让 goto_page_smart 直接选搜索分支
                skip_walk = (
                    settings.use_search_jump
                    and target > current + 1
                    and (
                        not settings.opportunistic
                        or all(self._is_intentional(pages, p) for p in range(current + 1, target))
                    )
                )
                logger.info("[前往] p%d（%s）", target, "搜索跳页" if skip_walk else "逐页翻")

                try:
                    reader.goto_page_smart(
                        target, on_page=on_route, max_direct=0 if skip_walk else None
                    )
                except CookieExpiredError:
                    raise
                except ReaderError as exc:
                    consecutive += 1
                    failed.add(target)
                    logger.warning(
                        "前往 p%d 失败（%s），已连续失败 %d 次", target, exc, consecutive
                    )
                    if consecutive >= self.MAX_CONSECUTIVE_FAILURES:
                        logger.error("连续 %d 次走位失败，停下本轮（重跑可续抓）", consecutive)
                        break
                    continue
                except Exception as exc:  # noqa: BLE001 - 非预期异常必须暴露，不吞
                    raise RuntimeError(f"前往 p%d 时出现未预期异常: {exc}") from exc

                captured = True
                if is_pending(target):
                    captured = self._capture(
                        reader, parser, store, pages, http, target, intentional=True
                    )
                    if captured:
                        fetched += 1
                if not captured:
                    consecutive += 1
                    failed.add(target)
                    logger.warning("p%d 抓取失败，跳过（已连续失败 %d 次）", target, consecutive)
                    if consecutive >= self.MAX_CONSECUTIVE_FAILURES:
                        logger.error("连续 %d 页抓取失败，停下本轮（重跑可续抓）", consecutive)
                        break
                    continue
                consecutive = 0

                # 顺着"连续待抓"的页往前走；遇到已抓过的页或范围边界就停，
                # 这样不会出现"走过一遍再空翻回来"的重复加载
                while True:
                    nxt = target + 1
                    if nxt > high or not is_pending(nxt):
                        break
                    if self._should_stop():
                        stopped = True
                        logger.info("[停止] 收到停止请求：停在 p%d，未完成的页留待下次续抓", target)
                        break
                    try:
                        reader.next_page()
                    except CookieExpiredError:
                        raise
                    except ReaderError as exc:
                        logger.warning("翻页失败（p%d -> p%d）：%s，重新规划走位", target, nxt, exc)
                        break
                    target = nxt
                    if self._capture(reader, parser, store, pages, http, target, intentional=True):
                        fetched += 1
                    reader.browser.sleep_random()
        except Exception as exc:  # noqa: BLE001 - 记录后仍要聚合已完成的部分
            error = exc
            if isinstance(exc, ReaderError):
                logger.error("抓取中断：%s", exc)
            else:
                logger.exception("抓取中断（未预期异常）")
        finally:
            result = self._finalize(
                store,
                pages,
                fetched=fetched,
                book_title=book_title,
                error=error,
                stopped=stopped,
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

            if intentional:
                # 只有正式页才下载插图：仅缓存的页可能永远不会用到，
                # 为它们发 HTTP 请求是纯浪费（等转正时会被重新解析并补下）
                store.attach_images(page, http)
            else:
                logger.debug("p%d 仅缓存，跳过插图下载", page_no)
            written = store.save_page(page, intentional=intentional)
            if not written and not page.intentional:
                # 磁盘上已有正式版本，本次读到的是缓存版本
                page.intentional = True
            pages[page.page] = page
            logger.info("  [%s] p%d %r", "正式" if intentional else "缓存", page_no, page.title)
            self._notify_page(page_no, intentional)
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
        stopped: bool = False,
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
            stopped=stopped,
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

    def _has_pending(self, pages: dict[int, Page]) -> bool:
        """配置范围内是否还有没正式抓过的页（纯离线判断，不碰浏览器）。"""
        return any(
            not self._is_intentional(pages, p)
            for p in range(self.settings.start_page, self.settings.end_page + 1)
        )
