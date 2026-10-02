"""页面 DOM 解析：JS 返回值 → :class:`~douban_reader.models.Page`。

职责边界
--------
* JS 负责"从 DOM 抓数据"（``js/parse_page.js``，外置成文件，可单独在控制台调试）；
* 本模块负责"把返回值变成可信的对象"：校验页码、容错字段、给出可诊断的报错。

:class:`PageParser` 只依赖一个 ``execute_script`` 接口，
因此测试里传个假对象就能跑，不需要真的启动 Chrome。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import selectors
from .errors import PageParseError
from .models import Page

logger = logging.getLogger(__name__)

DEFAULT_JS_PATH = Path(__file__).resolve().parent / "js" / "parse_page.js"


def build_js_config() -> dict[str, Any]:
    """把选择器打包成 JS 侧入参（选择器的唯一定义处是 selectors.py）。"""
    return {
        "readerRoot": selectors.READER_ROOT,
        "anyPage": selectors.ANY_PAGE,
        "currentPage": list(selectors.CURRENT_PAGE_FALLBACKS),
        "pageContainer": selectors.PAGE_CONTAINER,
        "pageAttr": selectors.PAGE_ATTR,
        "content": list(selectors.CONTENT_FALLBACKS),
        "title": list(selectors.PAGE_TITLE_FALLBACKS),
        "illusClass": selectors.ILLUS_CLASS,
        "illusImage": selectors.ILLUS_IMAGE,
        "illusLegend": selectors.ILLUS_LEGEND,
    }


def validate_raw_page(raw: Any) -> Page:
    """校验 JS/磁盘返回的原始 dict，返回 :class:`Page`。

    Raises:
        PageParseError: 不是 dict、带 ``error`` 字段，或页码为 0。
    """
    if not isinstance(raw, Mapping):
        raise PageParseError(f"页面解析返回值类型异常: {type(raw).__name__} -> {raw!r}")
    if raw.get("error"):
        detail = ", ".join(f"{k}={v!r}" for k, v in raw.items() if k != "error")
        raise PageParseError(f"页面解析失败: error={raw['error']}（{detail}）")
    page = Page.from_raw(raw)
    if not page.page:
        raise PageParseError(
            f"页面解析结果缺少有效页码: {json.dumps(raw, ensure_ascii=False)[:300]}"
        )
    return page


class PageParser:
    """按需注入 ``parse_page.js`` 并解析当前页。"""

    def __init__(
        self,
        browser: Any,
        *,
        js_path: str | Path | None = None,
    ) -> None:
        self._browser = browser
        self._js_path = Path(js_path) if js_path else DEFAULT_JS_PATH
        self._script: str | None = None

    @property
    def js_path(self) -> Path:
        return self._js_path

    def script(self) -> str:
        """读取并缓存解析脚本（首次使用时才读盘，便于测试替换路径）。"""
        if self._script is None:
            try:
                self._script = self._js_path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise PageParseError(f"解析脚本不存在: {self._js_path}") from exc
            except OSError as exc:
                raise PageParseError(f"解析脚本读取失败 {self._js_path}: {exc}") from exc
        return self._script

    def parse_current_page(self, *, retries: int = 4, retry_wait: float = 0.5) -> Page:
        """解析当前页，失败重试；仍失败抛 :class:`PageParseError`。"""
        script = self.script()
        config = build_js_config()
        last_error = ""

        for attempt in range(1, max(1, retries) + 1):
            try:
                raw = self._browser.execute_script(script, config)
                return validate_raw_page(raw)
            except PageParseError as exc:
                last_error = str(exc)
            except Exception as exc:  # noqa: BLE001 - 驱动层异常统一降级为重试
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < retries:
                logger.debug("解析当前页失败（第 %d/%d 次）：%s", attempt, retries, last_error)
                time.sleep(retry_wait)

        raise PageParseError(f"解析当前页失败（重试 {retries} 次）：{last_error}")
