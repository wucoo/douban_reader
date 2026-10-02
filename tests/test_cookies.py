"""cookie 解析/去重/过滤的离线测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from douban_reader.cookies import (
    dedupe,
    load_cookies,
    parse_cookie_file,
    parse_cookie_string,
    sanitize,
    to_cookie_header,
)
from douban_reader.errors import CookieError


def test_parse_basic() -> None:
    cookies = parse_cookie_string("bid=abc; ark_session=xyz;")
    assert cookies == [{"name": "bid", "value": "abc"}, {"name": "ark_session", "value": "xyz"}]


def test_parse_keeps_semicolon_inside_quotes() -> None:
    cookies = parse_cookie_string('a="x;y"; b=2')
    assert cookies == [{"name": "a", "value": '"x;y"'}, {"name": "b", "value": "2"}]


def test_parse_ignores_junk_entries() -> None:
    assert parse_cookie_string("no-equals; =novalue; ok=1") == [{"name": "ok", "value": "1"}]


def test_parse_empty() -> None:
    assert parse_cookie_string("") == []
    assert parse_cookie_string("   ") == []


def test_sanitize_drops_unknown_fields() -> None:
    cookie = sanitize({"name": "a", "value": "1", "size": 12, "priority": "Medium"})
    assert cookie == {"name": "a", "value": "1"}


def test_sanitize_requires_name_and_value() -> None:
    assert sanitize({"name": "a"}) is None
    assert sanitize({"value": "1"}) is None


def test_dedupe_keeps_last_occurrence() -> None:
    """真实 cookie.txt 里 _ga 出现两次，去重后应只剩最后一条。"""
    cookies = dedupe(
        [
            {"name": "_ga", "value": "old", "domain": ".douban.com"},
            {"name": "bid", "value": "keep"},
            {"name": "_ga", "value": "new", "domain": ".douban.com"},
        ]
    )
    assert [c["name"] for c in cookies] == ["_ga", "bid"]
    assert cookies[0]["value"] == "new"


def test_dedupe_distinguishes_domain_and_path() -> None:
    cookies = dedupe(
        [
            {"name": "a", "value": "1", "domain": ".douban.com", "path": "/"},
            {"name": "a", "value": "2", "domain": ".arkread.com", "path": "/"},
        ]
    )
    assert len(cookies) == 2


def test_parse_cookie_file_plain_text(tmp_path: Path) -> None:
    path = tmp_path / "cookie.txt"
    path.write_text("bid=abc;\nark_session=xyz\n", encoding="utf-8")
    cookies = parse_cookie_file(path)
    assert {c["name"]: c["value"] for c in cookies} == {"bid": "abc", "ark_session": "xyz"}


def test_parse_cookie_file_json_list(tmp_path: Path) -> None:
    path = tmp_path / "cookie.json"
    path.write_text(
        json.dumps([{"name": "bid", "value": "abc", "domain": ".douban.com", "size": 5}]),
        encoding="utf-8",
    )
    cookies = parse_cookie_file(path)
    assert cookies == [{"name": "bid", "value": "abc", "domain": ".douban.com", "size": 5}]


def test_parse_cookie_file_json_cookies_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "cookie.json"
    path.write_text(json.dumps({"cookies": [{"name": "a", "value": "1"}]}), encoding="utf-8")
    assert parse_cookie_file(path) == [{"name": "a", "value": "1"}]


def test_parse_cookie_file_missing(tmp_path: Path) -> None:
    with pytest.raises(CookieError, match="不存在"):
        parse_cookie_file(tmp_path / "nope.txt")


def test_parse_cookie_file_empty(tmp_path: Path) -> None:
    path = tmp_path / "cookie.txt"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(CookieError, match="空的"):
        parse_cookie_file(path)


def test_parse_cookie_file_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "cookie.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CookieError, match="JSON 解析失败"):
        parse_cookie_file(path)


def test_load_cookies_merges_file_and_string(tmp_path: Path) -> None:
    path = tmp_path / "cookie.txt"
    path.write_text("bid=from-file", encoding="utf-8")
    cookies = load_cookies(cookie_file=path, cookie_string="ark_session=from-string; bid=override")
    assert {c["name"]: c["value"] for c in cookies} == {
        "bid": "override",
        "ark_session": "from-string",
    }


def test_to_cookie_header() -> None:
    assert (
        to_cookie_header([{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]) == "a=1; b=2"
    )
