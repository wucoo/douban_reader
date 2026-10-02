"""页面解析测试：JS 返回值 → Page 的校验与容错，全部用替身浏览器，不启动 Chrome。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from douban_reader import parsing, selectors
from douban_reader.errors import PageParseError
from douban_reader.models import Page
from douban_reader.parsing import DEFAULT_JS_PATH, PageParser, build_js_config, validate_raw_page


class FakeBrowser:
    """只提供 execute_script 的最小替身。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute_script(self, script: str, *args: Any) -> Any:
        self.calls.append((script, args))
        if not self._results:
            raise AssertionError("替身浏览器没有更多返回值了")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


GOOD_RAW = {
    "page": 3,
    "title": "小偶像",
    "paragraphs": [
        {"type": "text", "text": "正文"},
        {"type": "image", "src": "https://example.com/a.jpg", "legend": "图注"},
    ],
}


# --------------------------------------------------------------------- 纯校验


def test_validate_raw_page_ok() -> None:
    page = validate_raw_page(GOOD_RAW)
    assert page.page == 3
    assert page.title == "小偶像"
    assert [p.type for p in page.paragraphs] == ["text", "image"]
    assert page.intentional is False


def test_validate_raw_page_reports_error_payload() -> None:
    with pytest.raises(PageParseError, match="no_page"):
        validate_raw_page({"error": "no_page", "ark_exists": False, "page_total": 0})


def test_validate_raw_page_requires_page_number() -> None:
    with pytest.raises(PageParseError, match="页码"):
        validate_raw_page({"page": 0, "title": "x", "paragraphs": []})


def test_validate_raw_page_rejects_non_dict() -> None:
    with pytest.raises(PageParseError, match="类型异常"):
        validate_raw_page(["not", "a", "dict"])


def test_from_raw_tolerates_broken_paragraphs() -> None:
    """旧实现直接 item["type"] 取键，结构一变就 KeyError；现在坏数据被跳过。

    过滤规则与 parse_page.js 保持一致：没有文字的正文段、既无地址又无图注的插图都不保留。
    """
    page = Page.from_raw(
        {
            "page": 5,
            "title": "t",
            "paragraphs": [
                "not-a-dict",
                {"no_type": 1},
                {"type": "text"},
                {"type": "text", "text": None},
                {"type": "text", "text": "   "},
                {"type": "image"},
                {"type": "image", "legend": "只有图注"},
                {"type": "text", "text": "正常"},
            ],
        }
    )
    assert [(p.type, p.text or p.legend) for p in page.paragraphs] == [
        ("image", "只有图注"),
        ("text", "正常"),
    ]


def test_page_roundtrip_dict() -> None:
    page = validate_raw_page(GOOD_RAW)
    page.intentional = True
    restored = Page.from_raw(page.to_dict())
    assert restored.to_dict() == page.to_dict()


def test_image_paragraph_dict_shape() -> None:
    page = Page.from_raw(GOOD_RAW)
    image = page.paragraphs[1]
    image.path = "../image/p3_0001.jpg"
    assert image.to_dict() == {
        "type": "image",
        "src": "https://example.com/a.jpg",
        "legend": "图注",
        "path": "../image/p3_0001.jpg",
    }


# --------------------------------------------------------------------- PageParser


def test_parser_passes_selectors_as_argument() -> None:
    browser = FakeBrowser([GOOD_RAW])
    PageParser(browser).parse_current_page()
    script, args = browser.calls[0]
    assert "arguments[0]" in script
    config = args[0]
    assert config["currentPage"] == list(selectors.CURRENT_PAGE_FALLBACKS)
    assert config["content"] == list(selectors.CONTENT_FALLBACKS)


def test_parser_retries_then_succeeds() -> None:
    browser = FakeBrowser([{"error": "no_page"}, RuntimeError("boom"), GOOD_RAW])
    page = PageParser(browser).parse_current_page(retries=3, retry_wait=0)
    assert page.page == 3
    assert len(browser.calls) == 3


def test_parser_raises_after_retries() -> None:
    browser = FakeBrowser([{"error": "no_page"}] * 3)
    with pytest.raises(PageParseError, match="重试 3 次"):
        PageParser(browser).parse_current_page(retries=3, retry_wait=0)


def test_parser_reports_missing_script(tmp_path: Path) -> None:
    browser = FakeBrowser([GOOD_RAW])
    with pytest.raises(PageParseError, match="解析脚本不存在"):
        PageParser(browser, js_path=tmp_path / "nope.js").parse_current_page()


def test_validate_raw_page_reports_bad_config() -> None:
    """JS 侧缺少选择器参数时自报 bad_config，错误信息应能直接看懂。"""
    with pytest.raises(PageParseError, match="bad_config"):
        validate_raw_page({"error": "bad_config", "missing": ["pageAttr"]})


# --------------------------------------------------------------------- JS 文件约束


def test_js_script_exists() -> None:
    assert DEFAULT_JS_PATH.is_file()


def test_js_script_has_no_hardcoded_selectors() -> None:
    """选择器只能来自 selectors.py —— JS 里出现 DOM 选择器即视为回归。"""
    script = DEFAULT_JS_PATH.read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.strip().startswith("*"))
    for forbidden in ("#ark-reader", ".curr-page", ".search-dialog", ".pagenum-result"):
        assert forbidden not in code, f"parse_page.js 里不应出现硬编码选择器：{forbidden}"


def test_build_js_config_covers_all_keys() -> None:
    config = build_js_config()
    assert set(config) == {
        "readerRoot",
        "anyPage",
        "currentPage",
        "pageContainer",
        "pageAttr",
        "content",
        "title",
        "illusClass",
        "illusImage",
        "illusLegend",
    }
    assert parsing.selectors is selectors
