# stealth_driver.py
import os
import json
import time
import random
from typing import Union, List, Dict, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class StealthDriver:
    """通用 Selenium Chrome 封装。

    - stealth 注入
    - Cookie 管理（字符串 / JSON / 纯文本文件）
    - CDP 资源拦截（默认关闭，避免破坏阅读器搜索等功能）
    - 常用工具（find_visible / download_file / ...）
    """

    DEFAULT_STEALTH_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "stealth.min.js"
    )

    # 默认不拦截任何 URL。
    #
    # 历史教训：
    #   1. 拦 *.css  → 阅读器分页失效、图片不显示
    #   2. 拦字体/统计 → 部分环境下搜索功能异常
    # 结论：阅读器页面依赖大量动态加载的资源，拦截收益小、风险大。
    #      需要时通过 block_resources=True + 自定义 BLOCKED_URLS 逐个加回。
    BLOCKED_URLS: List[str] = []

    def __init__(
        self,
        stealth_path: Optional[str] = None,
        headless: bool = False,
        chrome_driver_path: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        page_load_timeout: int = 30,
        cookie_string: Optional[str] = None,
        cookie_file: Optional[str] = None,
        block_resources: bool = False,      # ← 默认关闭
    ):
        self.stealth_path = stealth_path or self.DEFAULT_STEALTH_PATH
        self.headless = headless
        self.chrome_driver_path = chrome_driver_path
        self.user_data_dir = user_data_dir
        self.page_load_timeout = page_load_timeout
        self.cookie_string = cookie_string
        self.cookie_file = cookie_file
        self.block_resources = block_resources

        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None

        self._cookie_queue: List[Dict] = []
        self._cookies_injected = False
        self._prepare_cookies()

    # ---------------- 生命周期 ----------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit()
        return False

    def __getattr__(self, name):
        if name in ("driver", "_cookie_queue"):
            raise AttributeError(name)
        driver = self.__dict__.get("driver")
        if driver is None:
            raise AttributeError(f"{name!r} 不可用：请先调用 start()")
        return getattr(driver, name)

    def start(self):
        options = self._build_options()
        if self.chrome_driver_path:
            service = Service(executable_path=self.chrome_driver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
        else:
            self.driver = webdriver.Chrome(options=options)

        self.driver.set_page_load_timeout(self.page_load_timeout)
        self.wait = WebDriverWait(self.driver, 10)

        self._inject_stealth()
        if self.block_resources:
            self._setup_resource_blocking()
        return self

    def quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ---------------- ChromeOptions ----------------

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

    # ---------------- stealth.min.js ----------------

    def _inject_stealth(self):
        if not os.path.exists(self.stealth_path):
            raise FileNotFoundError(
                f"stealth.min.js 未找到: {self.stealth_path}\n"
                f"请运行 npx extract-stealth-evasions 生成，"
                f"或通过 stealth_path 参数指定路径。"
            )
        with open(self.stealth_path, "r", encoding="utf-8") as f:
            stealth_js = f.read()
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": stealth_js},
        )

    # ---------------- CDP 资源拦截（默认不启用） ----------------

    def _setup_resource_blocking(self):
        if not self.BLOCKED_URLS:
            return
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        try:
            self.driver.execute_cdp_cmd("Network.setBlockedURLs", {
                "urls": self.BLOCKED_URLS,
            })
            print(f"[info] 已启用资源拦截: {self.BLOCKED_URLS}")
        except Exception as e:
            print(f"[warn] 资源拦截设置失败: {e}")

    # ---------------- Cookie ----------------

    def _prepare_cookies(self):
        cookies: List[Dict] = []

        if self.cookie_file:
            if not os.path.exists(self.cookie_file):
                raise FileNotFoundError(f"cookie 文件不存在: {self.cookie_file}")
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if raw:
                if raw[0] in "{[":
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"cookie 文件 JSON 解析失败: {e}")
                    if isinstance(data, dict) and "cookies" in data:
                        data = data["cookies"]
                    if not isinstance(data, list):
                        raise ValueError("cookie JSON 应为 list 或 {'cookies': list}")
                    cookies.extend(data)
                else:
                    lines = [
                        line.strip().rstrip(";").strip()
                        for line in raw.splitlines()
                        if line.strip()
                    ]
                    cookies.extend(self.parse_cookie_string("; ".join(lines)))

        if self.cookie_string:
            cookies.extend(self.parse_cookie_string(self.cookie_string))

        allowed = ("path", "domain", "secure", "httpOnly", "expiry", "sameSite")
        cleaned = []
        for c in cookies:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            item = {"name": str(c["name"]), "value": str(c["value"])}
            for k in allowed:
                if k in c and c[k] is not None:
                    item[k] = c[k]
            cleaned.append(item)
        self._cookie_queue = cleaned

    @staticmethod
    def parse_cookie_string(cookie_str: str) -> List[Dict]:
        cookies: List[Dict] = []
        if not cookie_str:
            return cookies
        parts, buf, in_quote = [], [], False
        for ch in cookie_str:
            if ch == '"':
                in_quote = not in_quote
                buf.append(ch)
            elif ch == ";" and not in_quote:
                parts.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        if buf:
            parts.append("".join(buf))
        for part in parts:
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name, value = name.strip(), value.strip()
            if name:
                cookies.append({"name": name, "value": value})
        return cookies

    def _inject_cookies_once(self):
        if self._cookies_injected or not self._cookie_queue:
            return
        for c in self._cookie_queue:
            try:
                self.driver.add_cookie(c)
            except Exception as e:
                print(f"[warn] cookie 添加失败: {c.get('name')} -> {e}")
        self._cookies_injected = True

    def set_cookies(self, cookies, domain=None, path="/") -> int:
        if self.driver is None:
            raise RuntimeError("请先调用 start()")
        if isinstance(cookies, str):
            cookies = self.parse_cookie_string(cookies)
        elif isinstance(cookies, dict):
            if "name" in cookies and "value" in cookies:
                cookies = [cookies]
            else:
                cookies = [{"name": k, "value": v} for k, v in cookies.items()]
        count = 0
        for c in cookies:
            item = {"name": str(c["name"]), "value": str(c["value"]),
                    "path": c.get("path", path)}
            if domain:
                item["domain"] = domain
            elif c.get("domain"):
                item["domain"] = c["domain"]
            for k in ("secure", "httpOnly", "expiry", "sameSite"):
                if k in c and c[k] is not None:
                    item[k] = c[k]
            try:
                self.driver.add_cookie(item)
                count += 1
            except Exception as e:
                print(f"[warn] cookie 添加失败: {item['name']} -> {e}")
        return count

    def get_cookies(self) -> List[Dict]:
        return self.driver.get_cookies()

    def save_cookies(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cookies": self.driver.get_cookies()},
                      f, ensure_ascii=False, indent=2)

    def cookies_to_string(self) -> str:
        return "; ".join(f"{c['name']}={c['value']}"
                         for c in self.driver.get_cookies())

    # ---------------- 导航与查找 ----------------

    def get(self, url: str, wait_until: Optional[str] = None,
            timeout: int = 15, inject_cookies: bool = True):
        self.driver.get(url)
        if inject_cookies and self._cookie_queue and not self._cookies_injected:
            self._inject_cookies_once()
            self.driver.refresh()
        if wait_until:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_until))
            )

    def find(self, css_selector: str, timeout: int = 10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )

    def find_all(self, css_selector: str, timeout: int = 10):
        WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        return self.driver.find_elements(By.CSS_SELECTOR, css_selector)

    def click(self, css_selector: str, timeout: int = 10):
        el = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector))
        )
        el.click()
        return el

    def type_text(self, css_selector: str, text: str,
                  clear: bool = True, timeout: int = 10):
        el = self.find(css_selector, timeout)
        if clear:
            el.clear()
        el.send_keys(text)
        return el

    def screenshot(self, path: str = "screenshot.png"):
        self.driver.save_screenshot(path)

    # ---------------- 通用工具 ----------------

    def find_visible(self, selectors, timeout: int = 10):
        if isinstance(selectors, str):
            selectors = [selectors]
        end = time.time() + timeout
        while time.time() < end:
            for sel in selectors:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        return el
                except Exception:
                    pass
            time.sleep(0.2)
        return None

    def click_js(self, css_selector: str, timeout: int = 10):
        el = self.find(css_selector, timeout)
        self.driver.execute_script("arguments[0].click();", el)
        return el

    def click_el_js(self, el):
        self.driver.execute_script("arguments[0].click();", el)
        return el

    def wait_attr(self, css_selector: str, attr: str,
                  old_value=None, timeout: int = 15):
        WebDriverWait(self.driver, timeout).until(
            lambda d: d.find_element(By.CSS_SELECTOR, css_selector)
                        .get_attribute(attr) != old_value
        )

    def to_requests_session(self):
        import requests
        s = requests.Session()
        for c in self.driver.get_cookies():
            s.cookies.set(c["name"], c["value"], domain=c.get("domain"))
        try:
            ua = self.driver.execute_script("return navigator.userAgent")
        except Exception:
            ua = "Mozilla/5.0"
        try:
            referer = self.driver.current_url
        except Exception:
            referer = ""
        s.headers.update({"User-Agent": ua, "Referer": referer})
        return s

    def download_file(self, url: str, save_path: str,
                      session=None, timeout: int = 30) -> Optional[str]:
        if not url:
            return None
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            return save_path
        sess = session or self.to_requests_session()
        try:
            r = sess.get(url, timeout=timeout, stream=True)
            if r.status_code != 200:
                print(f"[warn] 下载失败 HTTP {r.status_code}: {url}")
                return None
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return save_path
        except Exception as e:
            print(f"[warn] 下载异常 {url}: {e}")
            return None

    def sleep_random(self, a: float = 1.5, b: float = 3.5):
        time.sleep(random.uniform(a, b))