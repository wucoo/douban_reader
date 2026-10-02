"""Chrome 会话封装：启动参数、stealth 注入、cookie 注入、常用交互。

与原 ``stealth_driver.py`` 的区别
--------------------------------
* **组合替代继承**：不再用 ``__getattr__`` 把一切属性转发给 ``driver``。
  那种写法的代价是 IDE 补全失效、类型检查失效、未启动时报错含糊；
  现在需要什么方法就显式提供什么方法。
* **cookie 逻辑搬到** :mod:`douban_reader.cookies`（纯函数，可离线单测）；
* **超时/等待全部来自** :class:`~douban_reader.settings.Settings`，不再有散落的魔法数；
* **HTTP 下载搬到** :class:`~douban_reader.storage.BookStore`（插图归存储层管）。

启动参数刻意保留原样（``excludeSwitches`` / ``AutomationControlled`` / ``lang`` 等），
它们直接参与反自动化检测，改动需要重新验证，不属于"顺手美化"的范围。
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .errors import ConfigError, NavigationError

logger = logging.getLogger(__name__)


class Browser:
    """一个受 stealth 补丁保护的 Chrome 会话。

    典型用法::

        with Browser(stealth_js=path, headless=False, cookies=loaded) as browser:
            browser.get("https://read.douban.com/reader/ebook/450696/")
    """

    def __init__(
        self,
        *,
        stealth_js: str | Path | None = None,
        headless: bool = False,
        page_load_timeout: float = 30.0,
        element_timeout: float = 10.0,
        user_data_dir: str | Path | None = None,
        chrome_driver_path: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
        block_resources: bool = False,
        blocked_urls: tuple[str, ...] = (),
        sleep_range: tuple[float, float] = (1.5, 3.5),
    ) -> None:
        self.stealth_js = Path(stealth_js) if stealth_js else None
        self.headless = headless
        self.page_load_timeout = page_load_timeout
        self.element_timeout = element_timeout
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        self.chrome_driver_path = chrome_driver_path
        self.block_resources = block_resources
        self.blocked_urls = tuple(blocked_urls)
        self.sleep_range = sleep_range

        self.driver: webdriver.Chrome | None = None
        self.wait: WebDriverWait | None = None

        self._cookies: list[dict[str, Any]] = list(cookies or [])
        self._cookies_injected = False

    # ------------------------------------------------------------ 生命周期

    def __enter__(self) -> Browser:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.quit()
        return False

    def start(self) -> Browser:
        """启动 Chrome 并完成 stealth 注入。"""
        options = self._build_options()
        if self.chrome_driver_path:
            service = Service(executable_path=self.chrome_driver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
        else:
            # Selenium 4.6+ 自带 Selenium Manager，无需手工指定驱动路径
            self.driver = webdriver.Chrome(options=options)

        self.driver.set_page_load_timeout(self.page_load_timeout)
        self.wait = WebDriverWait(self.driver, self.element_timeout)

        self._inject_stealth()
        if self.block_resources:
            self._setup_resource_blocking()
        logger.debug("浏览器已启动（headless=%s）", self.headless)
        return self

    def quit(self) -> None:
        """关闭浏览器；重复调用安全。"""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as exc:  # noqa: BLE001 - 关闭失败无需影响主流程
                logger.debug("关闭浏览器时出错：%s", exc)
            finally:
                self.driver = None
                self.wait = None
                logger.debug("浏览器已关闭")

    @property
    def is_running(self) -> bool:
        return self.driver is not None

    def _require_driver(self) -> webdriver.Chrome:
        if self.driver is None:
            raise NavigationError("浏览器尚未启动，请先调用 Browser.start() 或使用 with 语句")
        return self.driver

    # ------------------------------------------------------------ 启动参数

    def _build_options(self) -> Options:
        options = Options()
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=zh-CN")
        if self.headless:
            options.add_argument("--headless=new")
        if self.user_data_dir:
            options.add_argument(f"--user-data-dir={self.user_data_dir}")
        return options

    def _inject_stealth(self) -> None:
        """注入 stealth.min.js（每个新文档生效）。"""
        if self.stealth_js is None:
            raise ConfigError("未指定 stealth 脚本路径")
        if not self.stealth_js.is_file():
            raise ConfigError(
                f"stealth 脚本不存在: {self.stealth_js}\n"
                "可通过 npx extract-stealth-evasions 重新生成，"
                "或在 config.toml 里用 stealth_js 指定路径。"
            )
        try:
            source = self.stealth_js.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"stealth 脚本读取失败 {self.stealth_js}: {exc}") from exc
        self._require_driver().execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": source}
        )
        logger.debug("已注入 stealth 脚本（%d 字节）", len(source))

    def _setup_resource_blocking(self) -> None:
        """CDP 资源拦截（默认关闭）。

        历史教训（务必读一遍再开启）：
            1. 拦 ``*.css`` → 阅读器分页失效、插图不显示；
            2. 拦字体/统计脚本 → 部分环境下搜索跳页异常。
        结论：阅读器依赖大量动态资源，拦截收益小、风险大。
        确需开启时，在 config.toml 里设 ``block_resources = true`` 并**逐个**加白名单。
        """
        if not self.blocked_urls:
            logger.warning("block_resources 已开启但 blocked_urls 为空，跳过资源拦截")
            return
        driver = self._require_driver()
        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": list(self.blocked_urls)})
            logger.info("已启用资源拦截：%s", list(self.blocked_urls))
        except Exception as exc:  # noqa: BLE001 - 拦截失败不影响主流程
            logger.warning("资源拦截设置失败：%s", exc)

    # ------------------------------------------------------------ 导航

    def get(
        self,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,
        inject_cookies: bool = True,
    ) -> None:
        """打开页面；首次导航后自动注入 cookie 并刷新一次。"""
        driver = self._require_driver()
        driver.get(url)
        if inject_cookies and self._cookies and not self._cookies_injected:
            self._inject_cookies_once()
            driver.refresh()
        if wait_until:
            WebDriverWait(driver, timeout or self.element_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_until))
            )

    @property
    def current_url(self) -> str:
        try:
            return self._require_driver().current_url
        except NavigationError:
            return ""

    @property
    def page_title(self) -> str:
        try:
            return self._require_driver().title or ""
        except NavigationError:
            return ""

    def execute_script(self, script: str, *args: Any) -> Any:
        return self._require_driver().execute_script(script, *args)

    # ------------------------------------------------------------ cookie

    def _inject_cookies_once(self) -> None:
        if self._cookies_injected or not self._cookies:
            return
        driver = self._require_driver()
        failed = 0
        for cookie in self._cookies:
            try:
                driver.add_cookie(cookie)
            except Exception as exc:  # noqa: BLE001 - 单条失败不应中断
                failed += 1
                logger.warning("cookie 注入失败 %s: %s", cookie.get("name"), exc)
        self._cookies_injected = True
        logger.debug("cookie 注入完成：成功 %d，失败 %d", len(self._cookies) - failed, failed)

    def get_cookies(self) -> list[dict[str, Any]]:
        return self._require_driver().get_cookies()

    def save_cookies(self, path: str | Path) -> Path:
        """把当前浏览器 cookie 导出为 JSON（换 cookie 时可直接改回 cookie.txt）。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"cookies": self.get_cookies()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        logger.info("已导出 %d 条 cookie 到 %s", len(self.get_cookies()), target)
        return target

    def to_requests_session(self) -> requests.Session:
        """用当前浏览器的 cookie / UA / Referer 造一个 requests 会话（用于下载插图）。"""
        session = requests.Session()
        for cookie in self.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))
        try:
            user_agent = self.execute_script("return navigator.userAgent")
        except Exception:  # noqa: BLE001
            user_agent = "Mozilla/5.0"
        try:
            referer = self.current_url
        except Exception:  # noqa: BLE001
            referer = ""
        session.headers.update({"User-Agent": user_agent, "Referer": referer})
        return session

    # ------------------------------------------------------------ 元素操作

    def find(self, css_selector: str, timeout: float | None = None) -> WebElement:
        driver = self._require_driver()
        return WebDriverWait(driver, timeout or self.element_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )

    def find_all(self, css_selector: str, timeout: float | None = None) -> list[WebElement]:
        driver = self._require_driver()
        WebDriverWait(driver, timeout or self.element_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        return driver.find_elements(By.CSS_SELECTOR, css_selector)

    def find_visible(
        self,
        css_selectors: str | list[str] | tuple[str, ...],
        *,
        timeout: float | None = None,
    ) -> WebElement | None:
        """在候选选择器里找第一个"可见"元素；超时返回 None（不抛异常）。"""
        selectors = [css_selectors] if isinstance(css_selectors, str) else list(css_selectors)
        driver = self._require_driver()
        deadline = time.monotonic() + (timeout if timeout is not None else self.element_timeout)
        while time.monotonic() < deadline:
            for selector in selectors:
                try:
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed():
                        return element
                except Exception:  # noqa: BLE001 - 逐个候选尝试，找不到很正常
                    continue
            time.sleep(0.2)
        return None

    def click(self, css_selector: str, timeout: float | None = None) -> WebElement:
        driver = self._require_driver()
        element = WebDriverWait(driver, timeout or self.element_timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector))
        )
        element.click()
        return element

    def click_js(self, css_selector: str, timeout: float | None = None) -> WebElement:
        """用 JS 点击：绕开"元素被遮挡/不可交互"的常见报错。"""
        element = self.find(css_selector, timeout)
        return self.click_element_js(element)

    def click_element_js(self, element: WebElement) -> WebElement:
        self.execute_script("arguments[0].click();", element)
        return element

    def type_text(
        self,
        css_selector: str,
        text: str,
        *,
        clear: bool = True,
        timeout: float | None = None,
    ) -> WebElement:
        element = self.find(css_selector, timeout)
        if clear:
            element.clear()
        element.send_keys(text)
        return element

    def wait_for_attribute(
        self,
        css_selector: str,
        attribute: str,
        old_value: Any = None,
        *,
        timeout: float | None = None,
    ) -> None:
        """等待某个属性值发生变化（翻页完成判定用）。"""
        driver = self._require_driver()
        WebDriverWait(driver, timeout or self.element_timeout).until(
            lambda d: (
                d.find_element(By.CSS_SELECTOR, css_selector).get_attribute(attribute) != old_value
            )
        )

    def screenshot(self, path: str | Path) -> Path:
        """截图存盘（排查页面改版时最省事的证据）。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._require_driver().save_screenshot(str(target))
        logger.info("已截图：%s", target)
        return target

    # ------------------------------------------------------------ 杂项

    def sleep_random(self, sleep_range: tuple[float, float] | None = None) -> float:
        """按配置的区间随机等待（降低触发风控的概率）。"""
        low, high = sleep_range or self.sleep_range
        seconds = random.uniform(low, high)
        time.sleep(seconds)
        return seconds
