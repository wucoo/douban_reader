"""阅读器导航：打开就绪、读页码/标题、翻页、智能跳页调度。

本模块只关心"怎么在阅读器里移动"，不关心"抓什么、存哪里"
（那是 :mod:`douban_reader.scraper` 与 :mod:`douban_reader.storage` 的事）。

跳页策略（``goto_page_smart``）::

    距离 = |当前页 - 目标页|
    距离 <= max_direct  → 逐页翻（会触发 on_page 回调，可顺手缓存沿途页）
    距离 >  max_direct  → 搜索框精确跳页（不触发 on_page）
    搜索不可用/没跳准    → 兜底逐页翻

这个"混合策略"是刻意保留的：搜索跳页快但依赖站点搜索面板，
逐页翻慢但稳，两者互补。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any, Self

from . import search_jump, selectors
from .browser import Browser
from .errors import CookieExpiredError, NavigationError
from .naming import book_dir_name
from .settings import Settings

logger = logging.getLogger(__name__)

#: 查找翻页按钮/次要元素的等待时间（与原实现一致，故意短于 element_timeout）
NAV_BUTTON_TIMEOUT = 5.0
#: 等待阅读器就绪的轮询间隔（秒）
STATE_POLL_INTERVAL = 0.4

#: 阅读器就绪探针：一次性返回所有判据，避免多次往返
_READER_STATE_JS = r"""
const cfg = arguments[0];
const one = (sel) => document.querySelector(sel);
const anyOf = (list) => (list || []).some((sel) => !!one(sel));
return {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    has_ark: !!one(cfg.readerRoot),
    has_curr: anyOf(cfg.currentPage),
    has_content: !!one(cfg.content),
    has_login: anyOf(cfg.loginDialogs),
    has_captcha: anyOf(cfg.captcha),
    body_head: (document.body.innerText || '').slice(0, 200),
};
"""

#: 读当前页码：按候选选择器顺序取第一个带有页码属性的元素
_CURRENT_PAGE_JS = r"""
const cfg = arguments[0];
for (const sel of cfg.selectors) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const value = el.getAttribute(cfg.attr);
    if (value !== null && value !== '') return value;
}
return null;
"""

#: 登录弹窗可见性（用于首屏就绪后的二次确认）
_LOGIN_VISIBLE_JS = r"""
const list = arguments[0];
return (list || []).some((sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
});
"""


class Reader:
    """豆瓣阅读器会话。"""

    def __init__(self, browser: Browser, settings: Settings) -> None:
        self.browser = browser
        self.settings = settings
        self._title_cache: str | None = None

    # ------------------------------------------------------------ 地址

    @property
    def url(self) -> str:
        return f"https://read.douban.com/reader/ebook/{self.settings.ebook_id}/"

    # ------------------------------------------------------------ 打开就绪

    def open(self, *, settle: float | None = None, timeout: float | None = None) -> Self:
        """打开阅读器并等待正文出现；发现登录弹窗立即报"cookie 失效"。"""
        settle = self.settings.open_settle if settle is None else settle
        timeout = self.settings.open_timeout if timeout is None else timeout

        self.browser.get(self.url)
        deadline = time.monotonic() + timeout
        state: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            try:
                state = self.browser.execute_script(_READER_STATE_JS, self._state_config())
            except Exception as exc:  # noqa: BLE001 - 探测失败继续重试
                state = {"error": str(exc)}

            if state and state.get("has_login"):
                self._raise_cookie_expired(state)

            if state and state.get("has_content"):
                time.sleep(settle)
                if self._login_dialog_visible():
                    self._raise_cookie_expired(state)
                logger.info("阅读器就绪：%s", state.get("title") or self.url)
                return self

            time.sleep(STATE_POLL_INTERVAL)

        raise NavigationError(f"阅读器打开超时（{timeout:.0f}s）。诊断: {self._diagnose(state)}")

    def _login_dialog_visible(self) -> bool:
        try:
            return bool(
                self.browser.execute_script(_LOGIN_VISIBLE_JS, list(selectors.LOGIN_DIALOGS))
            )
        except Exception:  # noqa: BLE001
            return False

    def _raise_cookie_expired(self, state: dict[str, Any]) -> None:
        body = (state.get("body_head") or "").replace("\n", " ")[:200]
        source = self.settings.cookie_path or "(cookie_string)"
        raise CookieExpiredError(
            "Cookie 已失效：阅读器出现登录弹窗。\n"
            f"  请从浏览器重新复制 cookie 内容到: {source}\n"
            f"  当前 URL: {state.get('url')}\n"
            f"  页面开头: {body!r}",
            url=str(state.get("url") or ""),
            body_head=body,
        )

    @staticmethod
    def _state_config() -> dict[str, Any]:
        return {
            "readerRoot": selectors.READER_ROOT,
            "currentPage": list(selectors.CURRENT_PAGE_FALLBACKS),
            "content": selectors.CONTENT,
            "loginDialogs": list(selectors.LOGIN_DIALOGS),
            "captcha": list(selectors.CAPTCHA_WIDGETS),
        }

    @staticmethod
    def _diagnose(state: dict[str, Any] | None) -> str:
        if not state:
            return "未取得任何页面状态"
        parts = [
            f"url={state.get('url')}",
            f"title={state.get('title')!r}",
            f"readyState={state.get('readyState')}",
            f"ark={state.get('has_ark')}",
            f"curr={state.get('has_curr')}",
            f"content={state.get('has_content')}",
        ]
        if state.get("has_captcha"):
            parts.append("可能原因=触发腾讯滑块")
        snippet = (state.get("body_head") or "").replace("\n", " ")
        parts.append(f"body[:200]={snippet[:200]!r}")
        return " | ".join(parts)

    # ------------------------------------------------------------ 页码 / 标题

    def current_page(self) -> int:
        """当前页码；取不到则抛 :class:`NavigationError`。"""
        value = self.browser.execute_script(
            _CURRENT_PAGE_JS,
            {"selectors": list(selectors.CURRENT_PAGE_FALLBACKS), "attr": selectors.PAGE_ATTR},
        )
        if value is None:
            raise NavigationError("找不到当前页元素（页码属性缺失）")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise NavigationError(f"页码不是整数: {value!r}") from exc

    def total_pages(self) -> int | None:
        """总页数；取不到返回 None（不影响主流程）。"""
        try:
            text = self.browser.find(selectors.TOTAL_PAGES, timeout=2.0).get_attribute(
                "textContent"
            )
            return int(str(text).strip())
        except Exception:  # noqa: BLE001 - 总页数只是展示信息
            return None

    def current_title(self) -> str:
        """当前页显示的章节标题；取不到返回空串。"""
        try:
            text = self.browser.find(selectors.PAGE_TITLE, timeout=2.0).get_attribute("textContent")
            return str(text or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def get_book_title(self) -> str:
        """书名：优先用配置里的覆盖值，其次从 ``<title>`` 里剥离站点名。"""
        if self.settings.book_title:
            return self.settings.book_title
        if self._title_cache:
            return self._title_cache

        title = ""
        raw = (self.browser.page_title or "").strip()
        if raw:
            # 站点标题形如「书名 - 豆瓣阅读」，取第一个非站点名的片段；
            # 分隔符与过滤规则照搬旧实现，避免悄悄改变目录名。
            parts = [p.strip() for p in re.split(r"\s*[-–—|]\s*", raw) if p.strip()]
            for part in parts:
                if "豆瓣阅读" in part or part == "豆瓣":
                    continue
                title = part
                break
        if not title:
            title = f"ebook_{self.settings.ebook_id}"
            logger.warning("无法从页面标题解析书名，改用 %s", title)
        self._title_cache = title
        return title

    def book_dir_name(self) -> str:
        return book_dir_name(self.get_book_title(), self.settings.ebook_id)

    # ------------------------------------------------------------ 翻页

    def next_page(self, *, timeout: float | None = None) -> int:
        """后翻一页，返回新页码。"""
        return self._turn(selectors.NEXT_BUTTONS, "后翻", timeout=timeout)

    def prev_page(self, *, timeout: float | None = None) -> int:
        """前翻一页，返回新页码。"""
        return self._turn(selectors.PREV_BUTTONS, "前翻", timeout=timeout)

    def _turn(self, buttons: tuple[str, ...], label: str, *, timeout: float | None = None) -> int:
        timeout = self.settings.nav_timeout if timeout is None else timeout
        before = self.current_page()
        button = self.browser.find_visible(buttons, timeout=NAV_BUTTON_TIMEOUT)
        if button is None:
            raise NavigationError(f"找不到{label}按钮（尝试过 {list(buttons)}）")
        self.browser.click_element_js(button)
        self.browser.wait_for_attribute(
            selectors.CURRENT_PAGE, selectors.PAGE_ATTR, before, timeout=timeout
        )
        return self.current_page()

    def goto_page(
        self,
        target: int,
        *,
        max_turns: int | None = None,
        on_page: Callable[[int], None] | None = None,
        sleep_range: tuple[float, float] | None = None,
        settle: float | None = None,
    ) -> int:
        """逐页翻到目标页。

        Args:
            on_page: 每翻一页**之前**回调当前页码（用于顺手缓存沿途页）。
        """
        target = int(target)
        max_turns = self.settings.max_turns if max_turns is None else max_turns
        settle = self.settings.page_settle if settle is None else settle

        for _ in range(max_turns):
            current = self.current_page()
            if current == target:
                if settle > 0:
                    time.sleep(settle)
                return current
            if on_page is not None:
                try:
                    on_page(current)
                except Exception as exc:  # noqa: BLE001 - 回调出错不阻断导航
                    logger.warning("on_page(p%d) 失败：%s", current, exc)
            if current < target:
                self.next_page()
            else:
                self.prev_page()
            self.browser.sleep_random(sleep_range)

        raise NavigationError(f"翻页 {max_turns} 次仍未到达第 {target} 页")

    def goto_page_smart(
        self,
        target: int,
        *,
        on_page: Callable[[int], None] | None = None,
        max_direct: int | None = None,
        use_search: bool | None = None,
    ) -> int:
        """按距离自动选择"逐页翻"或"搜索跳页"，见模块文档。"""
        target = int(target)
        max_direct = self.settings.max_direct if max_direct is None else max_direct
        use_search = self.settings.use_search_jump if use_search is None else use_search

        current = self.current_page()
        distance = abs(current - target)

        if distance <= max_direct or not use_search:
            logger.info("[逐页] p%d -> p%d（距离 %d <= %d）", current, target, distance, max_direct)
            return self.goto_page(target, on_page=on_page)

        logger.info("[搜索] p%d -> p%d（距离 %d > %d）", current, target, distance, max_direct)
        try:
            actual = search_jump.jump_to_page(self, target, timeout=self.settings.search_timeout)
            logger.info("[搜索] 实际到达 p%d", actual)
            if actual == target:
                if self.settings.page_settle > 0:
                    time.sleep(self.settings.page_settle)
                return actual
            logger.warning("搜索跳页未精确命中（期望 p%d，实到 p%d），逐页微调", target, actual)
        except Exception as exc:  # noqa: BLE001 - 搜索不可用时必须能兜底
            logger.warning("搜索跳转失败（%s），回退逐页翻", exc)

        return self.goto_page(target, on_page=on_page)
