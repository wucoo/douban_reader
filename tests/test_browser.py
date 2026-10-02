"""浏览器层的纯逻辑测试（不需要 Chrome、不联网）。"""

from __future__ import annotations

from douban_reader.browser import cdp_cookie_params


def test_uses_url_when_cookie_has_no_domain() -> None:
    """纯文本 cookie（F12 复制的那种）没有 domain → 由 CDP 从目标 URL 推导。"""
    params = cdp_cookie_params({"name": "bid", "value": "abc"}, "https://read.douban.com/reader/")
    assert params == {"name": "bid", "value": "abc", "url": "https://read.douban.com/reader/"}
    assert "domain" not in params, "url 与 domain 不能同时给，CDP 会拒绝"


def test_uses_domain_when_present() -> None:
    """JSON 格式的 cookie 带 domain → 按 domain + path 写。"""
    params = cdp_cookie_params(
        {"name": "bid", "value": "abc", "domain": ".douban.com"}, "https://read.douban.com/"
    )
    assert params == {"name": "bid", "value": "abc", "domain": ".douban.com", "path": "/"}
    assert "url" not in params


def test_keeps_explicit_path() -> None:
    params = cdp_cookie_params(
        {"name": "a", "value": "1", "domain": ".douban.com", "path": "/reader"}, "https://x/"
    )
    assert params["path"] == "/reader"


def test_maps_optional_flags_and_expiry() -> None:
    params = cdp_cookie_params(
        {
            "name": "ark_session",
            "value": "xyz",
            "secure": True,
            "httpOnly": True,
            "expiry": 1800000000,
            "sameSite": "Lax",
        },
        "https://read.douban.com/",
    )
    assert params["secure"] is True
    assert params["httpOnly"] is True
    assert params["expires"] == 1800000000.0, "selenium 的 expiry 要映射成 CDP 的 expires"
    assert params["sameSite"] == "Lax"


def test_ignores_unknown_same_site() -> None:
    """有些导出工具写 Unspecified / no_restriction，CDP 不认，直接忽略而不是报错。"""
    params = cdp_cookie_params(
        {"name": "a", "value": "1", "sameSite": "Unspecified"}, "https://read.douban.com/"
    )
    assert "sameSite" not in params


def test_ignores_non_numeric_expiry() -> None:
    params = cdp_cookie_params(
        {"name": "a", "value": "1", "expiry": "soon"}, "https://read.douban.com/"
    )
    assert "expires" not in params


def test_coerces_name_and_value_to_str() -> None:
    params = cdp_cookie_params({"name": 12, "value": None}, "https://read.douban.com/")
    assert params["name"] == "12"
    assert params["value"] == "None"
