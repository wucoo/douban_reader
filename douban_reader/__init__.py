"""douban-reader：豆瓣阅读电子书抓取器。

包内结构（按领域职责纵切，而不是按技术层横切）::

    settings.py      配置装配（默认值 < config.toml < config.local.toml < 命令行）
    logging_setup.py 日志装配
    errors.py        领域异常 + 退出码
    models.py        Page / Paragraph / Chapter / Block（纯数据）
    naming.py        文件与目录命名规范
    selectors.py     全部 CSS 选择器（唯一事实来源）
    cookies.py       cookie 解析/去重（纯函数）
    browser.py       Chrome 会话（启动、stealth、等待、元素操作）
    reader.py        阅读器导航（打开就绪、页码、翻页、智能跳页调度）
    search_jump.py   搜索面板跳页
    parsing.py       JS 返回值 → Page
    chapters.py      Page 列表 → 章节（纯函数）
    storage.py       pages 缓存 / 插图 / 章节落盘
    scraper.py       策略编排
    cli.py           命令行入口

快速开始::

    from douban_reader import Settings, Scraper

    result = Scraper(Settings(start_page=1, end_page=20)).run()
    print(result.exit_code, result.written)
"""

from __future__ import annotations

from .browser import Browser
from .errors import ExitCode, ReaderError
from .models import Block, Chapter, Page, Paragraph
from .reader import Reader
from .scraper import Scraper, ScrapeResult
from .settings import Settings, load_settings

__version__ = "1.0.0"

__all__ = [
    "Block",
    "Browser",
    "Chapter",
    "ExitCode",
    "Page",
    "Paragraph",
    "Reader",
    "ReaderError",
    "ScrapeResult",
    "Scraper",
    "Settings",
    "__version__",
    "load_settings",
]
