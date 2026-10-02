"""豆瓣阅读器 DOM 选择器（唯一事实来源）。

页面改版时只需要改这个文件。
注意：``douban_reader/js/parse_page.js`` 里不再硬编码任何选择器，
全部由 :mod:`douban_reader.parsing` 通过参数注入，避免"同一事实写两遍"
（旧实现里 ``#ark-reader .page.curr-page`` 在 Python 和 JS 里各写了一份）。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 阅读区
READER_ROOT = "#ark-reader"
CURRENT_PAGE = "#ark-reader .page.curr-page"
CURRENT_PAGE_FALLBACKS: tuple[str, ...] = ("#ark-reader .page.curr-page", ".page.curr-page")
# 兜底：按 data-pagination 找可见页（上面两个都失效时使用）
PAGE_CONTAINER = ".page[data-pagination]"
PAGE_ATTR = "data-pagination"
# 任意页容器（只用于"一页都没解析出来"时的诊断计数）
ANY_PAGE = ".page"

PAGE_TITLE = f"{CURRENT_PAGE} .hd h3 a"
PAGE_TITLE_FALLBACKS: tuple[str, ...] = (".hd h3 a", ".hd h3")

CONTENT = f"{CURRENT_PAGE} .bd .content"
CONTENT_FALLBACKS: tuple[str, ...] = (".bd .content", ".content")
ILLUS_CLASS = "illus"
ILLUS_IMAGE = "img"
ILLUS_LEGEND = ".legend .text-content"

TOTAL_PAGES = ".page-portal .total-num"

# ---------------------------------------------------------------- 翻页按钮
NEXT_BUTTONS: tuple[str, ...] = (
    "aside.pagination .turn-next",
    ".pagination .turn-next",
    "a.tiny-page-switcher.page-next-switcher",
)
PREV_BUTTONS: tuple[str, ...] = (
    "aside.pagination .turn-prev",
    ".pagination .turn-prev",
    "a.tiny-page-switcher.page-prev-switcher",
)

# ---------------------------------------------------------------- 搜索面板
SEARCH_TRIGGER = "#fn-search"
SEARCH_DIALOG = ".search-dialog"
SEARCH_INPUT = ".search-dialog .search-form input.query"
SEARCH_CLOSE = ".search-dialog .bubble-close"
SEARCH_PAGE_ITEM = ".search-dialog .pagenum-result ul li"
SEARCH_PAGE_ATTR = "data-pno"

# ---------------------------------------------------------------- 风控/登录
LOGIN_DIALOGS: tuple[str, ...] = (
    ".login-dialog",
    ".login-dialog-wrapper",
    "#login-dialog-container .login-dialog",
)
CAPTCHA_WIDGETS: tuple[str, ...] = (
    "#tcaptcha_transform_dy",
    ".tencent-captcha-dy__warp",
)
