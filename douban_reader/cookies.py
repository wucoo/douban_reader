"""cookie 的解析、去重、过滤与序列化（纯函数，可离线单测）。

原实现把这段逻辑埋在 ``StealthDriver`` 里，必须启动浏览器才能测；
现在它是独立的纯函数模块，且补上了原实现缺的一步：**按 (name, domain, path) 去重**。

为什么必须去重：实测 ``cookie.txt`` 里 ``_ga`` 出现了两次（浏览器导出的常见现象），
``driver.add_cookie`` 对同名 cookie 会抛异常或互相覆盖，旧实现只会刷一堆
``[warn] cookie 添加失败`` 却没人注意。去重规则取"后者胜"，与浏览器语义一致。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import CookieError

logger = logging.getLogger(__name__)

#: 允许传给 selenium 的字段（其余一律丢弃，避免 add_cookie 报参数错误）
ALLOWED_KEYS = ("path", "domain", "secure", "httpOnly", "expiry", "sameSite")


def parse_cookie_string(cookie_str: str) -> list[dict[str, str]]:
    """把 ``a=1; b=2`` 形式的请求头解析成 cookie 列表。

    引号内的分号不当作分隔符（cookie 值里可能出现 ``;``）。
    """
    cookies: list[dict[str, str]] = []
    if not cookie_str or not cookie_str.strip():
        return cookies

    parts: list[str] = []
    buffer: list[str] = []
    in_quote = False
    for char in cookie_str:
        if char == '"':
            in_quote = not in_quote
            buffer.append(char)
        elif char == ";" and not in_quote:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    if buffer:
        parts.append("".join(buffer))

    for part in parts:
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            cookies.append({"name": name, "value": value})
    return cookies


def cookie_key(cookie: Mapping[str, Any]) -> tuple[str, str, str]:
    """去重键：名称 + 域（小写）+ 路径。"""
    return (
        str(cookie.get("name", "")),
        str(cookie.get("domain") or "").lower(),
        str(cookie.get("path") or "/"),
    )


def dedupe(cookies: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按 :func:`cookie_key` 去重，同名后者覆盖前者，并保持出现顺序。"""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookie in cookies:
        sanitized = sanitize(cookie)
        if sanitized is not None:
            merged[cookie_key(sanitized)] = sanitized
    return list(merged.values())


def sanitize(cookie: Mapping[str, Any]) -> dict[str, Any] | None:
    """只保留 selenium 认得的字段；缺 name/value 则丢弃。"""
    if not isinstance(cookie, Mapping) or "name" not in cookie or "value" not in cookie:
        return None
    item: dict[str, Any] = {"name": str(cookie["name"]), "value": str(cookie["value"])}
    for key in ALLOWED_KEYS:
        value = cookie.get(key)
        if value is not None:
            item[key] = value
    return item


def parse_cookie_file(path: Path) -> list[dict[str, Any]]:
    """读取 cookie 文件：支持 JSON（list 或 ``{"cookies": [...]}``）与纯文本两种格式。"""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise CookieError(f"cookie 文件不存在: {path}") from exc
    except OSError as exc:
        raise CookieError(f"cookie 文件读取失败: {path}: {exc}") from exc

    if not raw:
        raise CookieError(f"cookie 文件是空的: {path}")

    if raw[0] in "{[":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CookieError(f"cookie 文件 JSON 解析失败 {path}: {exc}") from exc
        if isinstance(data, Mapping):
            data = data.get("cookies", [])
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise CookieError(f"cookie JSON 应为列表或 {{'cookies': [...]}} 形式: {path}")
        return [dict(item) for item in data if isinstance(item, Mapping)]

    lines = [line.strip().rstrip(";").strip() for line in raw.splitlines() if line.strip()]
    return parse_cookie_string("; ".join(lines))


def load_cookies(
    *,
    cookie_file: str | Path | None = None,
    cookie_string: str | None = None,
) -> list[dict[str, Any]]:
    """装配最终要注入浏览器的 cookie 列表（文件优先，字符串补充，最后统一去重）。"""
    collected: list[dict[str, Any]] = []
    if cookie_file:
        collected.extend(parse_cookie_file(Path(cookie_file)))
    if cookie_string:
        collected.extend(parse_cookie_string(cookie_string))

    cleaned = dedupe(collected)
    if len(cleaned) != len(collected):
        logger.debug("cookie 去重：%d -> %d 条", len(collected), len(cleaned))
    if not cleaned:
        logger.warning("没有可用的 cookie，阅读器大概率会弹出登录框")
    return cleaned


def to_cookie_header(cookies: Iterable[Mapping[str, Any]]) -> str:
    """序列化成 ``Cookie:`` 请求头里使用的字符串。"""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
