"""翻页确认逻辑的测试：页码抖动、回跳、点击被吞时的行为。

不需要浏览器：用一个"点击驱动"的假浏览器模拟阅读器。
这一组对应真实踩到的问题 —— 阅读器加载新页时 ``data-pagination`` 会先跳到目标、
再回跳，旧实现看到"页码变了"就认定翻页成功，于是去读内容时读到上一页，
日志里出现成片「期望 p14 实得 p13」，接着整段范围都被判失败。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from selenium.common.exceptions import TimeoutException

from douban_reader.errors import NavigationError
from douban_reader.reader import TURN_ATTEMPTS, Reader
from douban_reader.settings import Settings


class FakeElement:
    def is_displayed(self) -> bool:
        return True


class FakeBrowser:
    """页码只在"点击"或"读取"时按脚本变化，用来模拟真实阅读器的抖动。

    Args:
        page: 起始页码。
        on_click: 第 n 次点击后页码依次变成什么（空列表 = 这次点击被吞了）。
    """

    def __init__(self, page: int, *, on_click: Callable[[int], list[int]] | None = None) -> None:
        self.page = page
        self.clicks = 0
        self._pending: list[int] = []
        self._on_click = on_click

    def _apply(self) -> None:
        if self._pending:
            self.page = self._pending.pop(0)

    # --- Reader 用到的接口 ---

    def execute_script(self, script: str, *args: Any) -> Any:
        self._apply()
        return str(self.page)

    def find_visible(self, selectors: Any, *, timeout: float | None = None) -> FakeElement:
        return FakeElement()

    def click_element_js(self, element: Any) -> Any:
        self.clicks += 1
        if self._on_click is not None:
            self._pending.extend(self._on_click(self.clicks))
        return element

    def wait_for_attribute(
        self, selector: str, attribute: str, old_value: Any = None, *, timeout: float | None = None
    ) -> None:
        self._apply()
        if self.page == old_value:
            raise TimeoutException("模拟：页码没有变化")

    def sleep_random(self, sleep_range: Any = None) -> float:
        return 0.0


def make_reader(
    page: int,
    *,
    on_click: Callable[[int], list[int]] | None = None,
    ready: float = 0.4,
    turn: float = 0.3,
) -> tuple[Reader, FakeBrowser]:
    browser = FakeBrowser(page, on_click=on_click)
    settings = Settings(turn_timeout=turn, page_ready_timeout=ready)
    return Reader(browser, settings), browser  # type: ignore[arg-type]


def test_lands_immediately_with_one_click() -> None:
    reader, browser = make_reader(13, on_click=lambda _n: [14])
    assert reader.next_page(expect=14) == 14
    assert browser.clicks == 1


def test_waits_out_a_bounce_back() -> None:
    """页码先跳 14、又回跳 13、最后才稳住：应当耐心等到 14，而不是顺手抓错页。"""
    reader, browser = make_reader(13, on_click=lambda _n: [14, 13, 13, 14], ready=2.0)
    assert reader.next_page(expect=14) == 14
    assert browser.clicks == 1, "页码最终到位了，不该重复点击"


def test_reclicks_when_first_click_is_swallowed() -> None:
    """第一次点击完全没反应（页面不动）：再点一次，而不是判定抓取失败。"""
    reader, browser = make_reader(13, on_click=lambda n: [14] if n >= 2 else [], ready=0.4)
    assert reader.next_page(expect=14) == 14
    assert browser.clicks == 2


def test_gives_up_after_bounded_attempts() -> None:
    """页码始终不动：点满 TURN_ATTEMPTS 次就报错，绝不无限重试。"""
    reader, browser = make_reader(13, on_click=lambda _n: [])
    with pytest.raises(NavigationError, match=f"连续 {TURN_ATTEMPTS} 次"):
        reader.next_page(expect=14)
    assert browser.clicks == TURN_ATTEMPTS


def test_already_on_target_does_not_click() -> None:
    reader, browser = make_reader(14, on_click=lambda _n: [15])
    assert reader.next_page(expect=14) == 14
    assert browser.clicks == 0


def test_turn_without_expect_accepts_any_change() -> None:
    """不指定期望页码时，只要页码变了就算成功（保留给外部调用）。"""
    reader, browser = make_reader(13, on_click=lambda _n: [15])
    assert reader.next_page() == 15
    assert browser.clicks == 1


def test_wait_for_page_returns_false_on_timeout() -> None:
    reader, _ = make_reader(13, on_click=lambda _n: [])
    assert reader.wait_for_page(14, timeout=0.4) is False


def test_wait_for_page_is_immediate_when_already_there() -> None:
    reader, browser = make_reader(20, on_click=lambda _n: [21])
    assert reader.wait_for_page(20, timeout=0.4) is True
    assert browser.clicks == 0
