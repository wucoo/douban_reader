"""随包第三方资源、JS 脚本与 Windows 启动脚本的完整性检查（纯文本，无需浏览器）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from douban_reader.parsing import DEFAULT_JS_PATH
from douban_reader.settings import DEFAULT_STEALTH_JS, PROJECT_ROOT

JS_DIR = Path(__file__).resolve().parent.parent / "douban_reader" / "js"
STEALTH_SHA_FILE = DEFAULT_STEALTH_JS.with_name(DEFAULT_STEALTH_JS.name + ".sha256")


def test_stealth_asset_exists() -> None:
    assert DEFAULT_STEALTH_JS.is_file()
    assert DEFAULT_STEALTH_JS.stat().st_size > 100_000, "stealth.min.js 疑似被截断"


def test_stealth_asset_matches_recorded_hash() -> None:
    """vendored 脚本必须与记录的 sha256 一致（防止误改/截断/换行符被转换）。"""
    recorded = STEALTH_SHA_FILE.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(DEFAULT_STEALTH_JS.read_bytes()).hexdigest()
    assert actual == recorded, "stealth.min.js 与 js/stealth.min.js.sha256 不符"

    raw = DEFAULT_STEALTH_JS.read_bytes()
    assert b"\r\n" not in raw, "压缩脚本的换行符不应被转换"


def test_parse_script_ends_with_top_level_return() -> None:
    """回归防护：脚本必须以顶层 return 结束。

    把逻辑包进 IIFE 再调用（``(function(){})(...)``）会让 chromedriver 丢掉返回值，
    Python 侧只能拿到 None —— 这个坑真实发生过（由 tests/test_browser_smoke.py 发现）。
    """
    script = DEFAULT_JS_PATH.read_text(encoding="utf-8")
    code_lines = [
        line
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith(("*", "/*", "//"))
    ]
    assert "(function" not in "\n".join(code_lines), "不要用 IIFE 包裹脚本，返回值会被丢弃"

    returns = [i for i, line in enumerate(code_lines) if line.startswith("return")]
    assert returns, "脚本缺少顶层 return"

    # 最后一个顶层 return 之后不允许再有语句（对象字面量续行是缩进的，允许）
    for line in code_lines[returns[-1] + 1 :]:
        assert line.startswith((" ", "\t", "}")), f"顶层 return 之后不应再有语句：{line!r}"


def test_parse_script_takes_config_from_arguments() -> None:
    script = DEFAULT_JS_PATH.read_text(encoding="utf-8")
    assert "arguments[0]" in script


def test_windows_scripts_use_crlf_without_bom() -> None:
    """批处理脚本必须 CRLF + UTF-8 无 BOM。

    cmd.exe 是按行解析 .cmd 的：只有 LF 换行时它会把行拆错，表现为
    "把半个词当命令执行 + 卡住不返回"（本项目真实踩过，run.cmd 曾因此假死）；
    带 UTF-8 BOM 则会让首行变成 ``锘?echo off`` 之类的未知命令。
    """
    scripts = sorted(PROJECT_ROOT.glob("*.cmd")) + sorted(PROJECT_ROOT.glob("*.bat"))
    assert scripts, "项目根目录应至少有一个 Windows 启动脚本"

    for script in scripts:
        raw = script.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{script.name} 不应带 UTF-8 BOM"
        assert raw.count(b"\n") > 0, f"{script.name} 似乎是空文件"
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{script.name} 必须全部使用 CRLF 换行"
