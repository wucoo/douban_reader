"""搜索面板跳页：用阅读器自带的"页码搜索"直达目标页。

这是全项目最容易失效、也最不该被拆散的一段逻辑：它同时涉及
DOM 结构、面板开合时序、输入事件模拟和落地校验。
因此它被完整地放在一个模块里，只暴露一个函数 :func:`jump_to_page`。

调用方 :meth:`douban_reader.reader.Reader.goto_page_smart` 会在本模块抛错时
自动回退到逐页翻，所以这里可以"该失败就失败"，不必自己兜底。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from selenium.webdriver.common.keys import Keys

from . import selectors
from .errors import NavigationError

if TYPE_CHECKING:  # pragma: no cover
    from .reader import Reader

logger = logging.getLogger(__name__)

#: 打开搜索面板的等待时间（秒）
DIALOG_OPEN_TIMEOUT = 6.0
#: 输入框出现后的短暂稳定等待（秒）
INPUT_SETTLE = 0.15
#: 结果轮询间隔（秒）
POLL_INTERVAL = 0.2

_DIALOG_VISIBLE_JS = r"""
const el = document.querySelector(arguments[0]);
if (!el) return false;
const rect = el.getBoundingClientRect();
return rect.width > 0 && rect.height > 0 && getComputedStyle(el).display !== 'none';
"""

_HAS_RESULT_JS = r"""
const items = document.querySelectorAll(arguments[0]);
for (const li of items) {
    if ((li.getAttribute(arguments[1]) || '').trim() !== '') return true;
}
return false;
"""

_CLICK_TRIGGER_JS = r"""
const li = document.querySelector(arguments[0]);
if (!li) return false;
const anchor = li.querySelector('a') || li;
anchor.click();
return true;
"""

_CLEAR_INPUT_JS = r"""
const el = arguments[0];
el.value = '';
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
"""

_FIND_PAGE_ITEM_JS = r"""
const want = String(arguments[0]);
const items = document.querySelectorAll(arguments[1]);
for (const li of items) {
    if ((li.getAttribute(arguments[2]) || '').trim() === want) return true;
}
return false;
"""

_CLICK_PAGE_ITEM_JS = r"""
const want = String(arguments[0]);
const items = document.querySelectorAll(arguments[1]);
for (const li of items) {
    if ((li.getAttribute(arguments[2]) || '').trim() === want) {
        li.click();
        return true;
    }
}
return false;
"""


def jump_to_page(reader: Reader, target: int, *, timeout: float = 12.0) -> int:
    """用搜索框跳到 ``target`` 页，返回实际落地页码。

    Raises:
        NavigationError: 面板打不开、输入框找不到、结果里没有该页码、点击失败。
    """
    target = int(target)
    browser = reader.browser

    if not _open_dialog(browser):
        raise NavigationError("无法打开搜索面板")

    try:
        input_box = browser.find_visible([selectors.SEARCH_INPUT], timeout=3.0)
        if input_box is None:
            raise NavigationError("找不到搜索输入框")

        try:
            input_box.clear()
            browser.execute_script(_CLEAR_INPUT_JS, input_box)
            time.sleep(INPUT_SETTLE)
        except Exception as exc:  # noqa: BLE001
            raise NavigationError(f"清空搜索输入框失败: {exc}") from exc

        try:
            input_box.click()
            input_box.send_keys(str(target))
            time.sleep(INPUT_SETTLE)
            input_box.send_keys(Keys.ENTER)
        except Exception as exc:  # noqa: BLE001
            raise NavigationError(f"输入页码并回车失败: {exc}") from exc

        if not _wait_for_page_item(browser, target, timeout=timeout):
            raise NavigationError(f"搜索结果里没有页码 {target} 的命中项")

        try:
            clicked = browser.execute_script(
                _CLICK_PAGE_ITEM_JS, target, selectors.SEARCH_PAGE_ITEM, selectors.SEARCH_PAGE_ATTR
            )
        except Exception as exc:  # noqa: BLE001
            raise NavigationError(f"点击页码结果失败: {exc}") from exc
        if not clicked:
            raise NavigationError("点击页码结果返回 false")

        actual = _wait_for_landing(reader, target, timeout=timeout)
        if actual != target:
            logger.warning("搜索跳页落地在 p%d（期望 p%d）", actual, target)
        return actual
    finally:
        _close_dialog(browser)


def _open_dialog(browser: Any) -> bool:
    """打开搜索面板；已经开着就直接复用。"""
    if _dialog_visible(browser):
        # 面板里如果还留着上次的结果，先关掉重开，避免命中旧列表
        if _has_result(browser):
            _close_dialog(browser)
            time.sleep(POLL_INTERVAL)
        else:
            return True

    if _dialog_visible(browser):
        return True

    try:
        browser.execute_script(_CLICK_TRIGGER_JS, selectors.SEARCH_TRIGGER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("点击搜索按钮失败：%s", exc)
        return False

    deadline = time.monotonic() + DIALOG_OPEN_TIMEOUT
    while time.monotonic() < deadline:
        if _dialog_visible(browser):
            time.sleep(POLL_INTERVAL)
            return True
        time.sleep(0.1)
    return False


def _close_dialog(browser: Any) -> None:
    try:
        browser.execute_script(
            "const el = document.querySelector(arguments[0]); if (el) el.click();",
            selectors.SEARCH_CLOSE,
        )
        time.sleep(POLL_INTERVAL)
    except Exception as exc:  # noqa: BLE001 - 关闭失败不影响已完成的跳转
        logger.debug("关闭搜索面板失败：%s", exc)


def _dialog_visible(browser: Any) -> bool:
    try:
        return bool(browser.execute_script(_DIALOG_VISIBLE_JS, selectors.SEARCH_DIALOG))
    except Exception:  # noqa: BLE001
        return False


def _has_result(browser: Any) -> bool:
    try:
        return bool(
            browser.execute_script(
                _HAS_RESULT_JS, selectors.SEARCH_PAGE_ITEM, selectors.SEARCH_PAGE_ATTR
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _wait_for_page_item(browser: Any, target: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if browser.execute_script(
                _FIND_PAGE_ITEM_JS,
                target,
                selectors.SEARCH_PAGE_ITEM,
                selectors.SEARCH_PAGE_ATTR,
            ):
                return True
        except Exception:  # noqa: BLE001 - 面板重绘期间查询失败很正常
            pass
        time.sleep(POLL_INTERVAL)
    return False


def _wait_for_landing(reader: Reader, target: int, *, timeout: float) -> int:
    """等待阅读器真正翻到目标页，返回最后观察到的页码。"""
    deadline = time.monotonic() + timeout
    last: int | None = None
    while time.monotonic() < deadline:
        try:
            last = reader.current_page()
            if last == target:
                return last
        except Exception:  # noqa: BLE001 - 翻页过程中读页码可能短暂失败
            pass
        time.sleep(POLL_INTERVAL)
    try:
        return reader.current_page()
    except Exception:  # noqa: BLE001
        return last or 0
