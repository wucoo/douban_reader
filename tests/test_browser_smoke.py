"""浏览器层烟囱测试（可选，标记 ``browser``）。

用**本地夹具页面** ``tests/fixtures/reader_page.html`` 驱动真实 Chrome，
不访问豆瓣、不需要 cookie，验证那些纯函数测不到的部分：

* Chrome 能否启动、stealth 是否生效（``navigator.webdriver`` 应为 False）；
* 选择器是否真的能定位到元素、页码/标题/总页数读取是否正确；
* 翻页后"等待页码属性变化"是否可靠；
* 注入的 ``parse_page.js`` 返回值能否被正确解析（**必须顶层 return**，
  包成 IIFE 会让 chromedriver 丢掉返回值——这是本项目踩过的真实坑）；
* 当前页选择器失效时，按可见性兜底定位是否管用。

单独运行::

    pytest -m browser

没有 Chrome / 驱动不可用时自动跳过，因此不会拖累常规测试。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from selenium.common.exceptions import WebDriverException

from douban_reader.browser import Browser
from douban_reader.errors import ConfigError
from douban_reader.parsing import PageParser
from douban_reader.reader import Reader
from douban_reader.settings import Settings

pytestmark = pytest.mark.browser

FIXTURE = Path(__file__).parent / "fixtures" / "reader_page.html"


class _CountingHandler(BaseHTTPRequestHandler):
    """返回一个极简页面，并记录被请求的路径（用于数"加载了几次"）。"""

    protocol_version = "HTTP/1.0"
    requested: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口名
        type(self).requested.append(self.path)
        body = b"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # 静音，别污染测试输出
        return


@pytest.fixture(scope="module")
def browser() -> Browser:
    settings = Settings(headless=True)
    instance = Browser(
        stealth_js=settings.stealth_js_path,
        headless=True,
        element_timeout=5.0,
        sleep_range=(0.05, 0.1),
    )
    try:
        instance.start()
    except (WebDriverException, ConfigError, OSError) as exc:  # pragma: no cover
        pytest.skip(f"无法启动 Chrome，跳过浏览器层烟囱测试：{exc}")
    instance.get(FIXTURE.as_uri())
    yield instance
    instance.quit()


@pytest.fixture(scope="module")
def reader(browser: Browser) -> Reader:
    return Reader(browser, Settings(headless=True))


def test_stealth_patch_is_effective(browser: Browser) -> None:
    assert browser.execute_script("return navigator.webdriver") is False


def test_reads_page_number_title_and_total(reader: Reader) -> None:
    assert reader.current_page() == 7
    assert reader.total_pages() == 200
    assert reader.current_title() == "小偶像"
    assert reader.get_book_title() == "小偶像"


def test_turns_pages_in_both_directions(reader: Reader) -> None:
    assert reader.next_page() == 8
    assert reader.next_page() == 9
    assert reader.prev_page() == 8
    assert reader.goto_page(11, max_turns=10) == 11


def test_parses_dom_into_page(browser: Browser) -> None:
    page = PageParser(browser).parse_current_page()

    assert page.page == 11
    assert page.title == "小偶像"
    assert [p.type for p in page.paragraphs] == [
        "title",
        "text",
        "image",
        "text",
        "text",
        "title",
    ]
    assert page.paragraphs[0].text == "小偶像"
    assert page.paragraphs[1].text == "第一段文字。"
    assert page.paragraphs[2].src == "https://example.com/figure.jpg"
    assert page.paragraphs[2].legend == "图注：记忆里总有些光亮到难忘。"
    # 行内标签的文字要一起取到；连续空白要折叠；空段落要被跳过
    assert page.paragraphs[3].text == "第二段文字强调结尾"
    assert page.paragraphs[4].text == "多余 空白 行"
    assert page.paragraphs[5].text == "内文小标题"


def test_falls_back_to_visible_page_container(browser: Browser) -> None:
    """当前页选择器失效时，仍应按可见性找到页容器。"""
    browser.execute_script(
        "document.querySelector('#ark-reader .page').classList.remove('curr-page')"
    )
    try:
        page = PageParser(browser).parse_current_page()
        assert page.page == 11
    finally:
        browser.execute_script(
            "document.querySelector('#ark-reader .page').classList.add('curr-page')"
        )


class _QuietServer(ThreadingHTTPServer):
    """浏览器会主动断开空闲连接、自动抓 favicon，这些都不该污染测试输出。"""

    def handle_error(self, request: object, client_address: object) -> None:
        return


@pytest.fixture(scope="module")
def local_site() -> Iterator[str]:
    """本地 HTTP 服务：请求会被逐条记录，用来数"目标页到底加载了几次"。"""
    server = _QuietServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_cookies_are_injected_before_the_first_navigation(local_site: str) -> None:
    """cookie 必须在导航前写好，避免"先加载一页再刷新"的额外整页请求。

    用本地服务数请求次数：走老流程（加载 → 注入 → 刷新）的话，
    同一个路径会出现两次。
    """
    settings = Settings(headless=True)
    _CountingHandler.requested.clear()

    instance = Browser(
        stealth_js=settings.stealth_js_path,
        headless=True,
        element_timeout=5.0,
        sleep_range=(0.05, 0.1),
        cookies=[{"name": "dsh_probe", "value": "1"}],
    )
    try:
        instance.start()
    except (WebDriverException, ConfigError, OSError) as exc:  # pragma: no cover
        pytest.skip(f"无法启动 Chrome，跳过浏览器层烟囱测试：{exc}")
    try:
        instance.get(f"{local_site}/probe")
        assert instance.execute_script("return document.cookie") == "dsh_probe=1"
        assert [c["name"] for c in instance.get_cookies()] == ["dsh_probe"]
    finally:
        instance.quit()

    hits = _CountingHandler.requested.count("/probe")
    assert hits == 1, (
        f"目标页只应加载一次（出现 {hits} 次说明注入后还刷新了一遍）；"
        f"实际请求：{_CountingHandler.requested}"
    )
