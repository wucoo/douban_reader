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
    """批处理脚本必须 CRLF + 无 BOM，且不能用 ``chcp`` 切换代码页。

    cmd.exe 是按**字节偏移**解析 .cmd 的，下列任一情况都会让偏移错位，
    于是它把注释的碎片当命令执行（症状：先冒出两行"不是内部或外部命令"，再正常输出）：

    * 只有 LF 换行（本项目真实踩过，run.cmd 因此假死）；
    * 文件中间 ``chcp`` 换代码页（本项目第二次踩过，中文注释被拆成碎片执行）；
    * UTF-8 BOM（首行会变成 ``锘?echo off`` 之类的未知命令）。
    """
    scripts = sorted(PROJECT_ROOT.glob("*.cmd")) + sorted(PROJECT_ROOT.glob("*.bat"))
    assert scripts, "项目根目录应至少有一个 Windows 启动脚本"

    for script in scripts:
        raw = script.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{script.name} 不应带 UTF-8 BOM"
        assert raw.count(b"\n") > 0, f"{script.name} 似乎是空文件"
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{script.name} 必须全部使用 CRLF 换行"

        # 只查真正会被执行的语句，注释里提到 chcp 是允许的（那是给未来的人看的警告）
        for line in raw.splitlines():
            stripped = line.strip().lower()
            if not stripped or stripped.startswith((b"rem", b"::", b"@")):
                continue
            assert not stripped.startswith(b"chcp"), (
                f"{script.name} 不应执行 chcp：读到一半切换代码页会让 cmd 的字节偏移错位"
            )


def test_windows_scripts_are_ascii_only() -> None:
    """批处理脚本必须是纯 ASCII：中文注释会让 cmd.exe 的按字节解析错位。

    这条规则踩过两次坑，所以单独立一个测试：只要在 .cmd 里写中文，
    轻则开头出现几行"不是内部或外部命令"，重则整段假死。
    中文用法请写在 ``run.cmd --help``（由 argparse 渲染）和 README.md 里。
    """
    for script in sorted(PROJECT_ROOT.glob("*.cmd")) + sorted(PROJECT_ROOT.glob("*.bat")):
        raw = script.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise AssertionError(
                f"{script.name} 含非 ASCII 字节（偏移 {exc.start}）：批处理里不要写中文，"
                f"改用英文注释，中文用法放 --help 与 README.md"
            ) from exc
