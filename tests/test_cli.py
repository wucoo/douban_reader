"""命令行入口测试。

只覆盖**不启动浏览器**的路径：配置装配、dry-run、离线重建、退出码映射。
所有输出目录都指向 tmp_path，绝不碰真实的 book/ 数据。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from douban_reader import cli
from douban_reader.errors import ExitCode
from douban_reader.scraper import Scraper
from douban_reader.settings import PROJECT_ROOT, Settings, load_toml


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def tmp_config(tmp_path: Path, extra: str = "") -> Path:
    """把输出、日志、凭据全部隔离到 tmp_path 的配置。"""
    return write_config(
        tmp_path,
        f'book_root = "{(tmp_path / "book").as_posix()}"\n'
        f'log_dir = "{(tmp_path / "logs").as_posix()}"\n'
        f'cookie_file = "{(tmp_path / "cookie.txt").as_posix()}"\n'
        f"{extra}",
    )


def test_dry_run_prints_settings_and_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_config(tmp_path, "start_page = 1\nend_page = 3\n")
    code = cli.main(["--config", str(config), "--no-local-config", "--dry-run"])

    out = capsys.readouterr().out
    assert code == ExitCode.OK
    assert "生效配置" in out
    assert "待正式抓取    3 页" in out
    assert "不启动浏览器" in out


def test_dry_run_reports_cached_pages(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """已有正式页时应从计划里排除，避免重复抓取。"""
    book = tmp_path / "book" / "书_450696" / "pages"
    book.mkdir(parents=True)
    page = {
        "page": 1,
        "title": "甲",
        "paragraphs": [{"type": "text", "text": "正文"}],
        "intentional": True,
    }
    (book / "p1.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")

    config = tmp_config(tmp_path, "start_page = 1\nend_page = 3\n")
    code = cli.main(["--config", str(config), "--no-local-config", "--dry-run"])

    out = capsys.readouterr().out
    assert code == ExitCode.OK
    assert "已缓存页数    1" in out
    assert "待正式抓取    2 页" in out


def test_unknown_config_key_returns_config_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path, 'eboook_id = "450696"\n')
    code = cli.main(["--config", str(config), "--no-local-config", "--dry-run"])
    assert code == ExitCode.CONFIG
    assert "未知配置项" in capsys.readouterr().err


def test_broken_config_returns_config_exit_code(tmp_path: Path) -> None:
    config = write_config(tmp_path, "这不是 = = toml\n")
    assert cli.main(["--config", str(config), "--no-local-config", "--dry-run"]) == ExitCode.CONFIG


def test_rebuild_only_on_empty_book_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """离线重建端到端：不联网也能跑完，并写出索引文件。"""
    config = tmp_config(tmp_path, "start_page = 1\nend_page = 5\n")
    code = cli.main(["--config", str(config), "--no-local-config", "--rebuild-only"])

    out = capsys.readouterr().out
    assert code == ExitCode.OK
    assert "运行结果" in out
    indexes = list((tmp_path / "book").rglob("_index.json"))
    assert len(indexes) == 1
    payload = json.loads(indexes[0].read_text(encoding="utf-8"))
    assert payload["chapters"] == []
    assert payload["cached_pages"] == 0


def test_missing_cookie_file_maps_to_config_error(tmp_path: Path) -> None:
    """凭据缺失属于配置问题（退出码 5），而且必须在启动浏览器之前就报出来。"""
    settings = Settings(
        ebook_id="450696",
        book_root=str(tmp_path / "book"),
        cookie_file=str(tmp_path / "nope.txt"),
        log_dir=str(tmp_path / "logs"),
    )
    result = Scraper(settings).run()
    assert result.exit_code == ExitCode.CONFIG
    assert result.error is not None and "cookie 文件不存在" in result.error
    assert not (tmp_path / "book").exists(), "凭据就绪前不应创建任何目录"


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert cli.VERSION in capsys.readouterr().out


def test_shipped_config_file_is_valid() -> None:
    """仓库里的 config.toml 必须能被解析并且不含未知键。"""
    from douban_reader.settings import DEFAULT_CONFIG_FILE, settings_from_mapping

    assert DEFAULT_CONFIG_FILE.is_file()
    settings = settings_from_mapping(load_toml(DEFAULT_CONFIG_FILE))
    assert settings.ebook_id == "450696"
    assert settings.stealth_js_path.is_file()


def test_docs_do_not_pass_values_to_boolean_flags() -> None:
    """布尔开关不接受值：``--headless false`` 会被 argparse 当成未知位置参数而报错。

    这类写法真的出现在过提示语与 README 里，所以用测试钉住：
    要关掉某个开关就写 ``--no-headless``。
    """
    pattern = re.compile(r"(--[a-z][a-z-]*)\s+(true|false)\b", re.IGNORECASE)
    targets = [PROJECT_ROOT / "README.md"]
    targets += sorted((PROJECT_ROOT / "douban_reader").glob("*.py"))

    offenders = [
        f"{path.name}: {match.group(0)}"
        for path in targets
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "布尔开关不能用值传参，请改用 --no-xxx：" + "; ".join(offenders)
