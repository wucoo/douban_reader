# douban_reader.py
import os
import re
import time
import json
import random
import traceback
from datetime import datetime
from typing import Optional, List, Dict, Callable

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from stealth_driver import StealthDriver


class DoubanReader(StealthDriver):
    """豆瓣阅读器封装。

    输出目录：
        ./book/<书名id>/
        ├── pages/   每页 JSON（唯一数据源，带 intentional 标记）
        ├── chapter/ 只由 intentional=True 的页合成
        ├── json/    同上，JSON 格式
        └── image/

    跳页策略（goto_page_smart）：
        - 距离 <= max_direct (默认 15) → 逐页翻
        - 距离 >  max_direct           → 搜索框精确跳页
        - 搜索不可用                   → 兜底逐页翻

    顺手存策略（scrape_to_files.on_route）：
        - 翻页路上经过的页，若在 [start, end] 范围内 → 直接 intentional=True
        - 若在范围外且 opportunistic=True            → intentional=False（仅缓存）
    """

    BOOK_ROOT = "book"

    # ---- 阅读区 ----
    CURR_PAGE = "#ark-reader .page.curr-page"
    TITLE     = CURR_PAGE + " .hd h3 a"

    NEXT_BTN = (
        "aside.pagination .turn-next",
        ".pagination .turn-next",
        "a.tiny-page-switcher.page-next-switcher",
    )
    PREV_BTN = (
        "aside.pagination .turn-prev",
        ".pagination .turn-prev",
        "a.tiny-page-switcher.page-prev-switcher",
    )

    # ---- 搜索面板 ----
    SEARCH_BTN   = "#fn-search"
    SEARCH_DLG   = ".search-dialog"
    SEARCH_INPUT = ".search-dialog .search-form input.query"
    SEARCH_CLOSE = ".search-dialog .bubble-close"
    SEARCH_PNO   = ".search-dialog .pagenum-result ul li"

    def __init__(self, ebook_id: str, book_title: Optional[str] = None, **kwargs):
        self.ebook_id = str(ebook_id)
        self._book_title_override = book_title
        self._book_title_cache: Optional[str] = None
        self.reader_url = f"https://read.douban.com/reader/ebook/{self.ebook_id}/"
        super().__init__(**kwargs)

    # ============ 打开 ============

    def open(self, settle: float = 2.0, timeout: float = 30.0) -> "DoubanReader":
        self.get(self.reader_url)

        end = time.time() + timeout
        last_state: Optional[dict] = None

        while time.time() < end:
            try:
                last_state = self.driver.execute_script(r"""
                    return {
                        url: location.href,
                        title: document.title,
                        readyState: document.readyState,
                        has_ark: !!document.querySelector('#ark-reader'),
                        has_curr: !!document.querySelector('#ark-reader .page.curr-page'),
                        has_content: !!document.querySelector(
                            '#ark-reader .page.curr-page .bd .content'
                        ),
                        has_login: !!(
                            document.querySelector('.login-dialog')
                            || document.querySelector('.login-dialog-wrapper')
                            || document.querySelector('#login-dialog-container .login-dialog')
                        ),
                        has_captcha: !!document.querySelector(
                            '#tcaptcha_transform_dy, .tencent-captcha-dy__warp'
                        ),
                        body_head: (document.body.innerText || '').slice(0, 200),
                    };
                """)
            except Exception as e:
                last_state = {"error": str(e)}

            if last_state and last_state.get("has_login"):
                self._raise_cookie_expired(last_state)

            if last_state and last_state.get("has_content"):
                time.sleep(settle)
                try:
                    still_login = self.driver.execute_script(
                        "return !!document.querySelector("
                        "'.login-dialog, .login-dialog-wrapper, "
                        "#login-dialog-container .login-dialog');"
                    )
                    if still_login:
                        self._raise_cookie_expired(last_state)
                except Exception:
                    pass
                return self

            time.sleep(0.4)

        diag = []
        if last_state:
            diag.append(f"url={last_state.get('url')}")
            diag.append(f"title={last_state.get('title')!r}")
            diag.append(f"readyState={last_state.get('readyState')}")
            diag.append(f"ark={last_state.get('has_ark')}")
            diag.append(f"curr={last_state.get('has_curr')}")
            diag.append(f"content={last_state.get('has_content')}")
            if last_state.get("has_captcha"):
                diag.append("可能原因=触发腾讯滑块")
            snippet = (last_state.get("body_head") or "").replace("\n", " ")
            diag.append(f"body[:200]={snippet[:200]!r}")
        raise RuntimeError(f"阅读器打开超时（{timeout}s）。诊断: " + " | ".join(diag))

    def _raise_cookie_expired(self, state: dict):
        body = (state.get("body_head") or "").replace("\n", " ")[:200]
        cookie_src = self.cookie_file or "(cookie_string 参数)"
        raise RuntimeError(
            f"Cookie 已失效：阅读器出现登录弹窗。\n"
            f"  请从浏览器重新复制 cookie 到: {cookie_src}\n"
            f"  当前 URL: {state.get('url')}\n"
            f"  页面开头: {body!r}"
        )

    # ---------------- 书名 / 根目录 ----------------

    def get_book_title(self) -> str:
        if self._book_title_override:
            return self._book_title_override
        if self._book_title_cache:
            return self._book_title_cache
        title = ""
        try:
            raw = (self.driver.title or "").strip()
            if raw:
                parts = [p.strip() for p in re.split(r"\s*[-–—|]\s*", raw) if p.strip()]
                for p in parts:
                    if "豆瓣阅读" in p or p == "豆瓣":
                        continue
                    title = p
                    break
        except Exception:
            pass
        if not title:
            title = f"ebook_{self.ebook_id}"
        self._book_title_cache = title
        return title

    def book_dir_name(self) -> str:
        name = self._safe_name(self.get_book_title(), max_len=40)
        return f"{name}_{self.ebook_id}"

    def default_root_dir(self) -> str:
        return os.path.join(self.BOOK_ROOT, self.book_dir_name())

    @classmethod
    def _normalize_root_dir(cls, root_dir: Optional[str]) -> Optional[str]:
        if root_dir is None:
            return None
        p = root_dir.replace("\\", "/").lstrip("./")
        prefix = cls.BOOK_ROOT + "/"
        if p.startswith(prefix):
            return p
        if os.path.isabs(root_dir):
            return os.path.join(cls.BOOK_ROOT,
                                os.path.basename(root_dir.rstrip("/\\")))
        return os.path.join(cls.BOOK_ROOT, p)

    # ---------------- 页码 / 标题 ----------------

    def current_page(self) -> int:
        v = self.driver.execute_script(
            "const e = document.querySelector(arguments[0]);"
            "return e ? e.getAttribute('data-pagination') : null;",
            self.CURR_PAGE,
        )
        if v is None:
            v = self.driver.execute_script(
                "const e = document.querySelector('.page.curr-page');"
                "return e ? e.getAttribute('data-pagination') : null;",
            )
        if v is None:
            raise RuntimeError("找不到当前页元素")
        return int(v)

    def total_pages(self) -> Optional[int]:
        try:
            return int(self.driver.find_element(
                By.CSS_SELECTOR, ".page-portal .total-num"
            ).get_attribute("textContent").strip())
        except Exception:
            return None

    def current_title(self) -> str:
        try:
            return (self.driver.find_element(
                By.CSS_SELECTOR, self.TITLE
            ).get_attribute("textContent") or "").strip()
        except Exception:
            return ""

    # ---------------- 翻页 ----------------

    def next_page(self, timeout: int = 20) -> int:
        old = self.current_page()
        btn = self.find_visible(self.NEXT_BTN, timeout=5)
        if btn is None:
            raise RuntimeError("找不到后翻按钮")
        self.click_el_js(btn)
        self.wait_attr(self.CURR_PAGE, "data-pagination", old, timeout)
        return self.current_page()

    def prev_page(self, timeout: int = 20) -> int:
        old = self.current_page()
        btn = self.find_visible(self.PREV_BTN, timeout=5)
        if btn is None:
            raise RuntimeError("找不到前翻按钮")
        self.click_el_js(btn)
        self.wait_attr(self.CURR_PAGE, "data-pagination", old, timeout)
        return self.current_page()

    def goto_page(self, target: int, max_turns: int = 500,
                  sleep_range=(1.5, 3.5), settle: float = 0.3,
                  on_page: Optional[Callable[[int], None]] = None) -> int:
        """逐页翻到目标页。on_page 每一步翻页前被调用。"""
        target = int(target)
        for _ in range(max_turns):
            cur = self.current_page()
            if cur == target:
                if settle > 0:
                    time.sleep(settle)
                return cur
            if on_page is not None:
                try:
                    on_page(cur)
                except Exception as e:
                    print(f"[warn] on_page({cur}) 失败: {e}")
            (self.next_page if cur < target else self.prev_page)()
            time.sleep(random.uniform(*sleep_range))
        raise RuntimeError(f"翻页 {max_turns} 次仍未到第 {target} 页")

    # ============ 搜索跳页 ============

    def _open_search_dialog(self, timeout: float = 6.0,
                            debug: bool = True) -> bool:
        def dlg_visible():
            try:
                return self.driver.execute_script(
                    "const e = document.querySelector(arguments[0]);"
                    "if (!e) return false;"
                    "const r = e.getBoundingClientRect();"
                    "return r.width > 0 && r.height > 0 "
                    "  && getComputedStyle(e).display !== 'none';",
                    self.SEARCH_DLG,
                )
            except Exception:
                return False

        def has_result():
            try:
                return self.driver.execute_script(
                    "return !!document.querySelectorAll(arguments[0]).length;",
                    self.SEARCH_PNO,
                )
            except Exception:
                return False

        if dlg_visible() and has_result():
            self._close_search_dialog()
            time.sleep(0.2)

        if dlg_visible():
            return True

        try:
            self.driver.execute_script(
                "const li = document.querySelector(arguments[0]);"
                "if (!li) return false;"
                "const a = li.querySelector('a') || li;"
                "a.click(); return true;",
                self.SEARCH_BTN,
            )
        except Exception as e:
            print(f"[warn] 点搜索按钮失败: {e}")
            return False

        end = time.time() + timeout
        while time.time() < end:
            if dlg_visible():
                time.sleep(0.2)
                return True
            time.sleep(0.1)
        return False

    def _close_search_dialog(self):
        try:
            self.driver.execute_script(
                "const e = document.querySelector(arguments[0]);"
                "if (e) e.click();",
                self.SEARCH_CLOSE,
            )
            time.sleep(0.2)
        except Exception:
            pass

    def goto_page_by_search(self, target: int, timeout: float = 12.0,
                            debug: bool = False) -> int:
        target = int(target)

        if not self._open_search_dialog(debug=debug):
            raise RuntimeError("无法打开搜索框")

        try:
            inp = self.find_visible([self.SEARCH_INPUT], timeout=3)
        except Exception:
            inp = None
        if inp is None:
            self._close_search_dialog()
            raise RuntimeError("找不到搜索输入框")

        try:
            inp.clear()
            self.driver.execute_script(r"""
                const e = arguments[0];
                e.value = '';
                e.dispatchEvent(new Event('input', {bubbles: true}));
                e.dispatchEvent(new Event('change', {bubbles: true}));
            """, inp)
            time.sleep(0.15)
        except Exception as e:
            self._close_search_dialog()
            raise RuntimeError(f"清空输入框失败: {e}")

        try:
            inp.click()
            inp.send_keys(str(target))
            time.sleep(0.15)
            inp.send_keys(Keys.ENTER)
        except Exception as e:
            self._close_search_dialog()
            raise RuntimeError(f"输入/回车失败: {e}")

        end = time.time() + timeout
        found = False
        while time.time() < end:
            try:
                found = self.driver.execute_script(r"""
                    const want = String(arguments[0]);
                    const items = document.querySelectorAll(arguments[1]);
                    for (const li of items) {
                        const pno = (li.getAttribute('data-pno') || '').trim();
                        if (pno === want) return true;
                    }
                    return false;
                """, target, self.SEARCH_PNO)
            except Exception:
                found = False
            if found:
                break
            time.sleep(0.2)

        if not found:
            self._close_search_dialog()
            raise RuntimeError(f"搜索结果里没有 data-pno={target} 的页码命中项")

        try:
            clicked = self.driver.execute_script(r"""
                const want = String(arguments[0]);
                const items = document.querySelectorAll(arguments[1]);
                for (const li of items) {
                    const pno = (li.getAttribute('data-pno') || '').trim();
                    if (pno === want) { li.click(); return true; }
                }
                return false;
            """, target, self.SEARCH_PNO)
        except Exception as e:
            self._close_search_dialog()
            raise RuntimeError(f"点击页码结果失败: {e}")

        if not clicked:
            self._close_search_dialog()
            raise RuntimeError("点击页码结果返回 false")

        end = time.time() + timeout
        while time.time() < end:
            try:
                cur = self.current_page()
                if cur == target:
                    self._close_search_dialog()
                    time.sleep(0.2)
                    return cur
            except Exception:
                pass
            time.sleep(0.2)

        self._close_search_dialog()
        return self.current_page()

    # ============ 智能跳页：核心策略 ============

    def goto_page_smart(self, target: int,
                        max_direct: int = 15,
                        sleep_range=(1.5, 3.5),
                        settle: float = 0.3,
                        on_page: Optional[Callable[[int], None]] = None,
                        use_search: bool = True) -> int:
        """智能跳页。

        规则：
            distance = |cur - target|
            if distance <= max_direct  → 逐页翻（触发 on_page）
            else                       → 搜索跳页（不触发 on_page）
            搜索失败                    → 兜底逐页翻
        """
        target = int(target)
        cur = self.current_page()
        distance = abs(cur - target)

        # ---- 距离近：逐页翻 ----
        if distance <= max_direct or not use_search:
            print(f"[walk] p{cur} → p{target}（距离 {distance} ≤ {max_direct}，逐页翻）")
            return self.goto_page(target, sleep_range=sleep_range,
                                  settle=settle, on_page=on_page)

        # ---- 距离远：搜索跳页 ----
        try:
            print(f"[search] p{cur} → p{target}（距离 {distance} > {max_direct}）")
            actual = self.goto_page_by_search(target)
            print(f"[search] 实际到达 p{actual}")
            if actual == target:
                if settle > 0:
                    time.sleep(settle)
                return actual
            # 没跳准，逐页微调
            return self.goto_page(target, sleep_range=sleep_range,
                                  settle=settle, on_page=on_page)
        except Exception as e:
            print(f"[warn] 搜索跳转失败: {e}，回退逐页翻")

        return self.goto_page(target, sleep_range=sleep_range,
                              settle=settle, on_page=on_page)

    # ---------------- 内容解析 ----------------

    def parse_page(self, retries: int = 4, retry_wait: float = 0.5) -> dict:
        script = r"""
        let page = document.querySelector('#ark-reader .page.curr-page');
        if (!page) page = document.querySelector('.page.curr-page');
        if (!page) {
            for (const p of document.querySelectorAll('.page[data-pagination]')) {
                const r = p.getBoundingClientRect();
                if (r.width > 50 && r.height > 50 && r.left > -300 && r.left < 600) {
                    page = p;
                    break;
                }
            }
        }
        if (!page) {
            return {
                error: 'no_page',
                ark_exists: !!document.querySelector('#ark-reader'),
                page_total: document.querySelectorAll('.page').length,
            };
        }

        const paragraphs = [];
        const content = page.querySelector('.bd .content')
                     || page.querySelector('.content');
        if (content) {
            for (const p of content.children) {
                if (p.tagName !== 'P') continue;
                const cls = p.className || '';
                if (cls.indexOf('illus') >= 0) {
                    const img = p.querySelector('img');
                    const legendEl = p.querySelector('.legend .text-content');
                    const src = img
                        ? (img.getAttribute('data-orig-src')
                           || img.getAttribute('src') || '')
                        : '';
                    const legend = legendEl
                        ? (legendEl.textContent || '').trim() : '';
                    if (src || legend) {
                        paragraphs.push({type: 'image', src: src, legend: legend});
                    }
                    continue;
                }
                const text = (p.textContent || '')
                    .replace(/[\r\n\t]+/g, ' ')
                    .replace(/\s{2,}/g, ' ')
                    .trim();
                if (!text) continue;
                paragraphs.push({
                    type: cls.indexOf('headline') >= 0 ? 'title' : 'text',
                    text: text
                });
            }
        }

        const titleEl = page.querySelector('.hd h3 a')
                     || page.querySelector('.hd h3');
        return {
            page: parseInt(page.getAttribute('data-pagination') || '0', 10),
            title: titleEl ? (titleEl.textContent || '').trim() : '',
            paragraphs: paragraphs,
        };
        """

        last = None
        for i in range(retries):
            try:
                last = self.driver.execute_script(script)
            except Exception as e:
                last = {"error": f"script_exception: {e}"}
            if isinstance(last, dict) and "error" not in last and last.get("page"):
                return last
            if i < retries - 1:
                time.sleep(retry_wait)

        raise RuntimeError(f"无法读取当前页内容，最后一次返回: {last}")

    # ===================== pages 存储 =====================

    @staticmethod
    def _page_file(pages_dir: str, page: int) -> str:
        return os.path.join(pages_dir, f"p{page}.json")

    def _load_page_index(self, pages_dir: str) -> Dict[int, Dict]:
        idx: Dict[int, Dict] = {}
        if not os.path.isdir(pages_dir):
            return idx
        for fname in os.listdir(pages_dir):
            m = re.fullmatch(r"p(\d+)\.json", fname)
            if not m:
                continue
            try:
                with open(os.path.join(pages_dir, fname), encoding="utf-8") as f:
                    data = json.load(f)
                idx[int(m.group(1))] = data
            except Exception as e:
                print(f"[warn] 读取 {fname} 失败: {e}")
        return idx

    def _save_page_json(self, pages_dir: str, data: dict, intentional: bool):
        os.makedirs(pages_dir, exist_ok=True)
        fpath = self._page_file(pages_dir, data["page"])
        if os.path.exists(fpath) and not intentional:
            try:
                with open(fpath, encoding="utf-8") as f:
                    if json.load(f).get("intentional"):
                        return
            except Exception:
                pass
        out = dict(data)
        out["intentional"] = bool(intentional)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    def _process_page_images(self, data, chapter_dir, image_dir, http):
        seq = 0
        for item in data["paragraphs"]:
            if item["type"] != "image":
                continue
            seq += 1
            ext = ".jpg"
            if item["src"]:
                m = re.search(r"\.(jpe?g|png|gif|webp)", item["src"], re.I)
                if m:
                    ext = "." + m.group(1).lower()
            fpath = os.path.join(image_dir, f"p{data['page']}_{seq:04d}{ext}")
            local = self.download_file(item["src"], fpath, session=http)
            if local:
                item["path"] = os.path.relpath(local, chapter_dir).replace("\\", "/")
            else:
                item["path"] = item["src"]
                item["download_failed"] = True

    def _capture_page(self, page_num: int, pages_dir: str,
                      chapter_dir: str, image_dir: str, http,
                      intentional: bool, max_retries: int = 3) -> bool:
        """解析当前页 → 校验页码 → 处理图片 → 存 pages/。"""
        for attempt in range(max_retries):
            try:
                data = self.parse_page()
            except Exception as e:
                print(f"[warn] parse p{page_num} 失败 (第{attempt + 1}次): {e}")
                time.sleep(0.8)
                continue
            actual = int(data.get("page", 0))
            if actual != int(page_num):
                print(f"[warn] 期望 p{page_num} 实得 p{actual}，"
                      f"重试 ({attempt + 1}/{max_retries})")
                time.sleep(0.8)
                continue
            self._process_page_images(data, chapter_dir, image_dir, http)
            self._save_page_json(pages_dir, data, intentional=intentional)
            tag = "★" if intentional else "○"
            print(f"  [{tag}] p{page_num} → {data.get('title')!r}")
            return True
        print(f"[error] p{page_num} 重试 {max_retries} 次仍失败")
        return False

    # ===================== 聚合 =====================

    def _rebuild(self, root_dir: str, save_json: bool = True) -> dict:
        pages_dir   = os.path.join(root_dir, "pages")
        chapter_dir = os.path.join(root_dir, "chapter")
        json_dir    = os.path.join(root_dir, "json")
        image_dir   = os.path.join(root_dir, "image")

        os.makedirs(pages_dir, exist_ok=True)
        os.makedirs(chapter_dir, exist_ok=True)
        os.makedirs(image_dir, exist_ok=True)
        if save_json:
            os.makedirs(json_dir, exist_ok=True)

        for d in (chapter_dir, json_dir):
            if os.path.isdir(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp):
                        try:
                            os.remove(fp)
                        except Exception:
                            pass

        all_pages = self._load_page_index(pages_dir)
        intentional_pages = [
            all_pages[p] for p in sorted(all_pages)
            if all_pages[p].get("intentional")
        ]
        cached_only = sorted(
            p for p in all_pages if not all_pages[p].get("intentional")
        )

        chapters: List[Dict] = []
        current: Optional[Dict] = None
        for page in intentional_pages:
            title = (page.get("title") or "未命名章节").strip()
            if current is None or current["title"] != title:
                current = {
                    "title": title,
                    "start_page": page["page"],
                    "end_page": page["page"],
                    "blocks": [],
                }
                chapters.append(current)
            else:
                current["end_page"] = page["page"]

            for item in page.get("paragraphs", []):
                t = item.get("type")
                if t == "image":
                    blk = {
                        "type": "image",
                        "path": item.get("path") or item.get("src", ""),
                        "legend": item.get("legend", ""),
                    }
                    if item.get("download_failed"):
                        blk["download_failed"] = True
                    current["blocks"].append(blk)
                elif t == "title":
                    if item.get("text", "").strip() == title:
                        continue
                    current["blocks"].append({
                        "type": "heading",
                        "text": item.get("text", ""),
                    })
                else:
                    current["blocks"].append({
                        "type": "text",
                        "text": item.get("text", ""),
                    })

        written_txt, written_json = [], []
        for idx, ch in enumerate(chapters, 1):
            if not ch["blocks"]:
                continue
            base = (f"{idx:03d}_{self._safe_name(ch['title'])}"
                    f"_p{ch['start_page']}-{ch['end_page']}")
            txt_path = os.path.join(chapter_dir, base + ".txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(self._render_markdown(ch))
            written_txt.append(txt_path)
            if save_json:
                json_path = os.path.join(json_dir, base + ".json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(ch, f, ensure_ascii=False, indent=2)
                written_json.append(json_path)

        index_path = None
        if save_json:
            index_path = os.path.join(json_dir, "_index.json")
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump({
                    "ebook_id": self.ebook_id,
                    "book_title": self.get_book_title(),
                    "root_dir": root_dir,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "cached_pages": len(all_pages),
                    "intentional_pages": len(intentional_pages),
                    "covered_intentional": sorted(
                        p["page"] for p in intentional_pages
                    ),
                    "covered_cache_only": cached_only,
                    "chapters": [
                        {
                            "file": (f"{i:03d}_{self._safe_name(c['title'])}"
                                     f"_p{c['start_page']}-{c['end_page']}"),
                            "title": c["title"],
                            "start_page": c["start_page"],
                            "end_page": c["end_page"],
                            "block_count": len(c["blocks"]),
                        }
                        for i, c in enumerate(chapters, 1) if c["blocks"]
                    ],
                }, f, ensure_ascii=False, indent=2)

        return {
            "root_dir": root_dir,
            "pages_dir": pages_dir,
            "chapter_dir": chapter_dir,
            "json_dir": json_dir,
            "image_dir": image_dir,
            "txt": written_txt,
            "json": written_json,
            "index": index_path,
            "chapters": chapters,
            "cached_pages": len(all_pages),
            "intentional_pages": len(intentional_pages),
            "cache_only_pages": cached_only,
        }

    @staticmethod
    def _render_markdown(chapter: Dict) -> str:
        lines: List[str] = []
        for blk in chapter.get("blocks", []):
            t = blk.get("type")
            if t == "text":
                lines.append(blk["text"])
                lines.append("")
            elif t == "heading":
                lines.append(f"## {blk['text']}")
                lines.append("")
            elif t == "image":
                legend = (blk.get("legend") or "")
                legend = legend.replace("\\", "\\\\").replace("]", "\\]")
                path = blk.get("path") or ""
                lines.append(f"![{legend}]({path})")
                lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    # ===================== 对外接口 =====================

    def scrape_to_files(self, start: int, end: int,
                        root_dir: Optional[str] = None,
                        sleep_range=(1.5, 3.5),
                        save_json: bool = True,
                        opportunistic: bool = True,
                        max_direct: int = 15,
                        use_search_jump: bool = True) -> dict:
        """按页码抓取 [start, end]。

        翻页路上经过的页：
        - 在 [start, end] 范围内 → 直接 intentional=True（一次到位）
        - 在范围外且 opportunistic=True → intentional=False（仅缓存）

        goto_page_smart 距离判断：
        - distance <= max_direct → 逐页翻（触发 on_route）
        - distance >  max_direct → 搜索跳页（不触发 on_route）
        """
        root_dir = self._normalize_root_dir(root_dir) or self.default_root_dir()
        pages_dir   = os.path.join(root_dir, "pages")
        chapter_dir = os.path.join(root_dir, "chapter")
        image_dir   = os.path.join(root_dir, "image")
        os.makedirs(pages_dir, exist_ok=True)
        os.makedirs(chapter_dir, exist_ok=True)
        os.makedirs(image_dir, exist_ok=True)
        if save_json:
            os.makedirs(os.path.join(root_dir, "json"), exist_ok=True)

        index = self._load_page_index(pages_dir)

        def is_intentional(p: int) -> bool:
            return bool(index.get(p, {}).get("intentional"))

        fetched = 0
        error = None

        try:
            todo = [p for p in range(int(start), int(end) + 1)
                    if not is_intentional(p)]
            todo_set = set(todo)
            print(f"[plan] 范围 {start}-{end}，已缓存 {len(index)} 页，"
                  f"待正式抓 {len(todo)} 页")

            if not todo:
                print("[skip] 范围内所有页已存为 intentional，直接聚合")
                return self._rebuild(root_dir, save_json=save_json)

            http = self.to_requests_session()
            lo, hi = min(todo), max(todo)

            def on_route(p: int):
                """翻页路上的回调：目标页正式抓，非目标页按需顺手存。"""
                nonlocal fetched

                # ---- 目标页：直接正式抓 ----
                if p in todo_set:
                    if is_intentional(p):
                        return
                    if self._capture_page(p, pages_dir, chapter_dir, image_dir,
                                          http, intentional=True):
                        index[p] = {"intentional": True}
                        fetched += 1
                    return

                # ---- 非目标页：顺手存 ----
                if not opportunistic:
                    return
                if p in index:
                    return
                if self._capture_page(p, pages_dir, chapter_dir, image_dir,
                                      http, intentional=False):
                    index[p] = {"intentional": False}

            print(f"[goto] 前往 p{lo}"
                  f"{'（路上顺手存）' if opportunistic else ''}")

            # 距离 <= max_direct 时，goto_page_smart 逐页翻，会触发 on_route
            # 距离 >  max_direct 时，走搜索跳页，on_route 不触发
            self.goto_page_smart(
                lo,
                max_direct=max_direct,
                sleep_range=sleep_range,
                on_page=on_route,
                use_search=use_search_jump,
            )

            # ---- 从 lo 向后扫到 hi，补抓尚未 intentional 的页 ----
            cur = lo
            while True:
                if not is_intentional(cur):
                    if self._capture_page(cur, pages_dir, chapter_dir,
                                          image_dir, http, intentional=True):
                        index[cur] = {"intentional": True}
                        fetched += 1

                if cur >= hi:
                    break
                try:
                    self.next_page()
                except Exception as e:
                    print(f"[warn] 翻页失败: {e}")
                    break
                cur += 1
                time.sleep(random.uniform(*sleep_range))

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            print(f"\n[error] 抓取中断: {error}")
            traceback.print_exc()

        finally:
            result = self._rebuild(root_dir, save_json=save_json)
            result["pages_fetched"] = fetched
            result["error"] = error

        return result

    @staticmethod
    def _safe_name(name: str, max_len: int = 60) -> str:
        name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip().strip(". ")
        return (name[:max_len] if len(name) > max_len else name) or "untitled"