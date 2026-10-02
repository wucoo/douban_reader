"""领域异常与进程退出码。

约定
----
* 所有可预期错误都继承 :class:`ReaderError`，调用方只 catch 这一层即可兜底；
* :mod:`douban_reader.cli` 负责把异常类型映射为 :class:`ExitCode`，
  这样脚本/计划任务可以直接靠退出码判断结果，不必解析输出文本。
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """进程退出码。"""

    OK = 0
    UNEXPECTED = 1  # 未预期的异常
    COOKIE_EXPIRED = 2  # cookie 失效 / 需要重新登录
    PARTIAL = 3  # 部分页抓取失败，结果不完整
    NAVIGATION = 4  # 翻页/跳页中断
    CONFIG = 5  # 配置或凭据文件有问题


class ReaderError(Exception):
    """本项目所有可预期错误的基类。"""


class ConfigError(ReaderError):
    """配置项非法、配置文件损坏、凭据文件缺失等。"""


class CookieError(ReaderError):
    """cookie 解析或注入失败。"""


class CookieExpiredError(CookieError):
    """阅读器弹出登录框：cookie 已失效。

    Attributes:
        url: 出错时的页面地址。
        body_head: 页面开头文本，便于判断是不是被风控/滑块拦截。
    """

    def __init__(self, message: str, *, url: str = "", body_head: str = "") -> None:
        super().__init__(message)
        self.url = url
        self.body_head = body_head


class NavigationError(ReaderError):
    """翻页、跳页、等待页面就绪失败。"""


class PageParseError(ReaderError):
    """页面 DOM 解析失败（结构变化、内容未加载等）。"""


class StorageError(ReaderError):
    """章节/缓存/图片落盘失败。"""
