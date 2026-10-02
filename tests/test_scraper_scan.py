"""抓取走位测试：用替身浏览器记录"每一次页面加载"，证明没有多余请求。

这里验证的正是真实踩到的问题：阅读器恢复在第 10 页、要抓 1-7 时，
旧实现会倒着抓一遍、**再正向空翻一遍**，已抓过的 6 页被重新加载。
新实现要求：每页最多只加载一次。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from douban_reader.errors import CookieExpiredError, ExitCode
from douban_reader.models import Page, Paragraph
from douban_reader.scraper import Scraper
from douban_reader.settings import Settings
from douban_reader.storage import BookStore


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: bytes = b"img") -> None:
        self.status_code = status_code
        self._payload = payload

    def iter_content(self, chunk_size: int = 8192) -> Any:
        yield self._payload


class FakeSession:
    """记录每一次插图下载请求。"""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.requested: list[str] = []

    def get(self, url: str, timeout: float | None = None, stream: bool = False) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(self.status_code)


class FakeBrowser:
    def __init__(self) -> None:
        self.session = FakeSession()

    def to_requests_session(self) -> FakeSession:
        return self.session

    def sleep_random(self, sleep_range: tuple[float, float] | None = None) -> float:
        return 0.0


class FakeReader:
    """按 ``Reader`` 的走位语义工作，并记录每次"页面被加载"。"""

    def __init__(self, current: int = 1, *, search_works: bool = True) -> None:
        self.page = current
        self.search_works = search_works
        self.browser = FakeBrowser()
        self.loads: list[int] = []  # 每次翻页/跳页 = 一次页面加载
        self.turns = 0
        self.jumps = 0

    def _arrive(self, target: int, *, via_search: bool) -> None:
        self.page = target
        self.loads.append(target)
        if via_search:
            self.jumps += 1
        else:
            self.turns += 1

    def current_page(self) -> int:
        return self.page

    def next_page(self) -> int:
        self._arrive(self.page + 1, via_search=False)
        return self.page

    def prev_page(self) -> int:
        self._arrive(self.page - 1, via_search=False)
        return self.page

    def goto_page_smart(
        self,
        target: int,
        *,
        on_page: Any = None,
        max_direct: int | None = None,
        use_search: bool | None = None,
    ) -> int:
        """与真实实现一致：近则逐页（触发 on_page），远则搜索跳页（不触发）。"""
        max_direct = 15 if max_direct is None else max_direct
        distance = abs(self.page - target)
        if distance == 0:
            return self.page

        if distance <= max_direct or not self.search_works:
            # 搜索不可用时也走这里：真实实现同样会回退逐页翻
            step = 1 if self.page < target else -1
            while self.page != target:
                if on_page is not None:
                    on_page(self.page)
                self._arrive(self.page + step, via_search=False)
            return self.page

        self._arrive(target, via_search=True)
        return self.page


class FakeParser:
    """当前页 → Page；可指定某些页解析失败。"""

    def __init__(self, reader: FakeReader, *, fail_pages: set[int] | None = None) -> None:
        self.reader = reader
        self.fail_pages = fail_pages or set()
        self.parsed: list[int] = []

    def parse_current_page(self, *, retries: int = 4, retry_wait: float = 0.5) -> Page:
        page_no = self.reader.page
        self.parsed.append(page_no)
        if page_no in self.fail_pages:
            raise RuntimeError(f"p{page_no} 解析失败（测试注入）")
        return Page(
            page=page_no,
            title=f"章{page_no}",
            paragraphs=[
                Paragraph(type="text", text=f"正文 p{page_no}"),
                Paragraph(type="image", src=f"https://example.com/p{page_no}.jpg", legend="图"),
            ],
            intentional=False,
        )


def scan(
    tmp_path: Path,
    *,
    current: int,
    start: int,
    end: int,
    preloaded: set[int] | None = None,
    fail_pages: set[int] | None = None,
    search_works: bool = True,
    **overrides: Any,
) -> tuple[Any, FakeReader, dict[int, Page], FakeSession]:
    settings = Settings(
        ebook_id="1",
        start_page=start,
        end_page=end,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
        **overrides,
    )
    store = BookStore(tmp_path / "book" / "测试书_1")
    # 与真实流程一致：先读磁盘缓存（模拟续抓），再注入测试想预置的"已抓过"页
    pages: dict[int, Page] = store.load_pages()
    for page_no in preloaded or set():
        pages[page_no] = Page(
            page=page_no,
            title=f"章{page_no}",
            paragraphs=[Paragraph(type="text", text="已有")],
            intentional=True,
        )
    reader = FakeReader(current, search_works=search_works)
    parser = FakeParser(reader, fail_pages=fail_pages)
    result = Scraper(settings)._scrape(reader, parser, store, pages, "测试书")
    return result, reader, pages, reader.browser.session


# --------------------------------------------------------------- 不多翻一页


def test_backward_walk_does_not_reload_captured_pages(tmp_path: Path) -> None:
    """真实场景：阅读器停在 p10，抓 1-7 —— 倒着走一趟就够，不该再正向空翻。"""
    result, reader, pages, _ = scan(tmp_path, current=10, start=1, end=7)

    assert reader.loads == [9, 8, 7, 6, 5, 4, 3, 2, 1], "只应倒着走一趟"
    assert len(reader.loads) == len(set(reader.loads)), "同一页不该被加载两次"
    assert reader.jumps == 0, "距离 9 在阈值内，应该逐页翻"

    assert sorted(pages) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert sorted(p for p in pages if pages[p].intentional) == [1, 2, 3, 4, 5, 6, 7]
    assert sorted(result.cache_only_pages) == [8, 9, 10]
    assert result.pages_fetched == 7
    assert result.incomplete_pages == []


def test_forward_scan_walks_each_page_once(tmp_path: Path) -> None:
    result, reader, pages, _ = scan(tmp_path, current=1, start=1, end=7)

    assert reader.loads == [2, 3, 4, 5, 6, 7]
    assert result.pages_fetched == 7
    assert all(pages[p].intentional for p in range(1, 8))


def test_single_page_range_loads_nothing(tmp_path: Path) -> None:
    """只要一页且已经在那一页：不应产生任何加载。"""
    result, reader, pages, _ = scan(tmp_path, current=5, start=5, end=5)

    assert reader.loads == []
    assert result.pages_fetched == 1
    assert pages[5].intentional is True


# --------------------------------------------------------------- 空翻换搜索跳页


def test_clean_gap_uses_search_jump(tmp_path: Path) -> None:
    """1 和 7 待抓、2-6 已抓过：别空翻 6 页，直接跳过去。"""
    result, reader, pages, _ = scan(tmp_path, current=1, start=1, end=7, preloaded={2, 3, 4, 5, 6})

    assert reader.jumps == 1, "应该走搜索跳页"
    assert reader.turns == 0, "不该逐页空翻"
    assert reader.loads == [7]
    assert sorted(p for p in pages if pages[p].intentional) == [1, 2, 3, 4, 5, 6, 7]
    assert result.pages_fetched == 2  # p1 + p7


def test_clean_gap_falls_back_to_walking_when_search_fails(tmp_path: Path) -> None:
    """搜索面板挂了也不能少抓：自动回退逐页翻。"""
    result, reader, pages, _ = scan(
        tmp_path,
        current=1,
        start=1,
        end=7,
        preloaded={2, 3, 4, 5, 6},
        search_works=False,
    )

    assert reader.jumps == 0
    assert reader.loads == [2, 3, 4, 5, 6, 7]
    assert pages[7].intentional is True
    assert result.pages_fetched == 2


def test_opportunistic_off_does_not_cache_pages_outside_range(tmp_path: Path) -> None:
    """关掉顺手缓存后，路过的 8/9/10 不该被存下来，但走位仍只走一趟。"""
    result, reader, pages, _ = scan(tmp_path, current=10, start=1, end=7, opportunistic=False)

    assert sorted(pages) == [1, 2, 3, 4, 5, 6, 7], "范围外的页不该入库"
    assert reader.loads == [9, 8, 7, 6, 5, 4, 3, 2, 1], "仍然只倒着走一趟"
    assert result.cache_only_pages == []
    assert result.pages_fetched == 7


def test_opportunistic_off_skips_clean_gap_with_jump(tmp_path: Path) -> None:
    """关掉顺手缓存 + 中间页已抓过：走这一段毫无收益 → 直接跳过去。"""
    _, reader, _, _ = scan(
        tmp_path,
        current=1,
        start=1,
        end=7,
        preloaded={2, 3, 4, 5, 6},
        opportunistic=False,
    )

    assert reader.jumps == 1
    assert reader.loads == [7]


# --------------------------------------------------------------- 请求总量


def test_cache_only_pages_do_not_download_images(tmp_path: Path) -> None:
    """仅缓存的页不下载插图：那些页可能永远用不到，为它们发请求是纯浪费。"""
    result, _, pages, session = scan(tmp_path, current=10, start=1, end=7)

    assert sorted(pages) == list(range(1, 11))
    # 只有正式页（1-7）的插图被下载，8/9/10 的没有（请求顺序是 p10 往回走的顺序）
    assert sorted(session.requested) == sorted(f"https://example.com/p{p}.jpg" for p in range(1, 8))

    stored = BookStore(tmp_path / "book" / "测试书_1").load_pages()
    for page_no in (8, 9, 10):
        paragraph = stored[page_no].paragraphs[1]
        assert paragraph.download_failed is False
        assert paragraph.path == f"https://example.com/p{page_no}.jpg", "仅缓存页保留远端地址"
    for page_no in (1, 7):
        assert stored[page_no].paragraphs[1].path == f"../image/p{page_no}_0001.jpg"
    assert sorted(result.cache_only_pages) == [8, 9, 10]


def test_no_duplicate_requests_on_rerun(tmp_path: Path) -> None:
    """第二次跑同一范围：不应再有任何页面加载或插图请求。"""
    first, _, pages, _ = scan(tmp_path, current=1, start=1, end=7)
    assert first.pages_fetched == 7

    second, reader, _, session = scan(tmp_path, current=1, start=1, end=7)
    assert reader.loads == [], "已经抓过的页不该再去走"
    assert session.requested == [], "插图也不该重复下载"
    assert second.pages_fetched == 0
    assert second.incomplete_pages == []


# --------------------------------------------------------------- 失败处理


def test_failed_page_is_skipped_and_scan_continues(tmp_path: Path) -> None:
    result, _, pages, _ = scan(tmp_path, current=1, start=1, end=7, fail_pages={3})

    assert sorted(p for p in pages if pages[p].intentional) == [1, 2, 4, 5, 6, 7]
    assert result.incomplete_pages == [3]
    assert result.pages_fetched == 6


def test_stops_after_consecutive_failures(tmp_path: Path) -> None:
    """连续失败不该把整轮时间耗光：3 次就停，剩下的留给下次续抓。"""
    result, reader, _, _ = scan(
        tmp_path, current=1, start=1, end=7, fail_pages={1, 2, 3, 4, 5, 6, 7}
    )

    assert result.incomplete_pages == [1, 2, 3, 4, 5, 6, 7]
    assert result.pages_fetched == 0
    assert reader.loads == [2, 3], "失败 3 次后应停下，而不是把 1-7 全走一遍"


def test_cookie_expired_is_reported_as_cookie_error(tmp_path: Path) -> None:
    """cookie 失效必须立刻定为退出码 2，不能被当成"某页失败"降级成"部分完成"。"""
    settings = Settings(
        ebook_id="1",
        start_page=1,
        end_page=7,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )
    reader = FakeReader(1)
    reader.goto_page_smart = lambda *a, **kw: (_ for _ in ()).throw(  # type: ignore[method-assign]
        CookieExpiredError("cookie 已失效")
    )

    result = Scraper(settings)._scrape(
        reader,  # type: ignore[arg-type]
        FakeParser(reader),
        BookStore(tmp_path / "book" / "测试书_1"),
        {},
        "测试书",
    )

    assert result.exit_code == ExitCode.COOKIE_EXPIRED
    assert result.error is not None and "cookie 已失效" in result.error
    assert result.pages_fetched == 0


# --------------------------------------------------------------- 提前退出


def test_run_skips_browser_when_nothing_pending(tmp_path: Path, monkeypatch: Any) -> None:
    """范围内全都抓过了：连浏览器都不该启动（省掉一次整页加载）。"""
    book = tmp_path / "book" / "测试书_1" / "pages"
    book.mkdir(parents=True)
    for page_no in range(1, 8):
        payload = {
            "page": page_no,
            "title": "章1",
            "paragraphs": [{"type": "text", "text": f"p{page_no}"}],
            "intentional": True,
        }
        import json

        (book / f"p{page_no}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("范围内已无待抓页，不应启动浏览器")

    monkeypatch.setattr("douban_reader.scraper.Browser", explode)

    settings = Settings(
        ebook_id="1",
        start_page=1,
        end_page=7,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
    )
    result = Scraper(settings).run()

    assert result.pages_fetched == 0
    assert result.exit_code == 0
    assert result.intentional_pages == 7


def test_run_launches_browser_when_pages_are_missing(tmp_path: Path, monkeypatch: Any) -> None:
    """还有待抓页时必须真的去开浏览器（这里让它在构造时就失败，以证明被调用）。"""
    launched: list[str] = []

    def fake_browser(**kwargs: Any) -> Any:
        launched.append("yes")
        raise RuntimeError("测试用：到此为止")

    monkeypatch.setattr("douban_reader.scraper.Browser", fake_browser)

    settings = Settings(
        ebook_id="1",
        start_page=1,
        end_page=7,
        book_root=str(tmp_path / "book"),
        log_dir=str(tmp_path / "logs"),
        cookie_file=str(tmp_path / "cookie.txt"),
    )
    (tmp_path / "cookie.txt").write_text("bid=1", encoding="utf-8")

    result = Scraper(settings).run()

    assert launched == ["yes"]
    assert result.error is not None
    assert result.exit_code == 1
