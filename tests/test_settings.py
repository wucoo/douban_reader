"""配置装配测试：默认值、覆盖顺序、校验与路径解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from douban_reader.errors import ConfigError
from douban_reader.settings import (
    PROJECT_ROOT,
    Settings,
    load_settings,
    load_toml,
    merge_layers,
    settings_from_mapping,
)


def test_defaults_match_legacy_behaviour() -> None:
    """默认值必须与重构前的 main.py 常量一致，否则重跑会改变行为。"""
    settings = Settings()
    assert settings.ebook_id == "450696"
    assert (settings.start_page, settings.end_page) == (1, 200)
    assert settings.headless is False
    assert settings.cookie_file == "cookie.txt"
    assert settings.opportunistic is True
    assert settings.max_direct == 15
    assert settings.use_search_jump is True
    assert settings.sleep_range == (1.5, 3.5)
    assert settings.save_json is True


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ConfigError, match="未知配置项: eboook_id"):
        settings_from_mapping({"eboook_id": "1"})


def test_empty_string_means_unset() -> None:
    settings = settings_from_mapping({"book_title": "  ", "cookie_string": ""})
    assert settings.book_title is None
    assert settings.cookie_string is None


def test_blocked_urls_becomes_tuple() -> None:
    settings = settings_from_mapping({"blocked_urls": ["*.css", "*.png"]})
    assert settings.blocked_urls == ("*.css", "*.png")


@pytest.mark.parametrize(
    "payload",
    [
        {"ebook_id": " "},
        {"start_page": 0},
        {"start_page": 10, "end_page": 3},
        {"sleep_min": 5.0, "sleep_max": 1.0},
        {"max_direct": -1},
        {"parse_retries": 0},
        {"log_level": "LOUD"},
    ],
)
def test_validation_rejects_bad_values(payload: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        settings_from_mapping(payload)


def test_merge_layers_ignores_none() -> None:
    assert merge_layers({"a": 1, "b": 2}, {"b": None, "c": 3}) == {"a": 1, "b": 2, "c": 3}


def test_load_toml_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_toml(tmp_path / "nope.toml") == {}


def test_load_toml_broken_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("this is not toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="解析失败"):
        load_toml(path)


def test_precedence_config_local_cli(tmp_path: Path) -> None:
    base = tmp_path / "config.toml"
    base.write_text("start_page = 1\nend_page = 100\nheadless = false\n", encoding="utf-8")
    local = tmp_path / "config.local.toml"
    local.write_text("end_page = 50\nheadless = true\n", encoding="utf-8")

    settings = load_settings(
        config_file=base,
        local_file=local,
        overrides={"end_page": 20},
    )

    assert settings.start_page == 1  # 基础配置
    assert settings.end_page == 20  # 命令行最优先
    assert settings.headless is True  # 本地配置覆盖基础配置


def test_local_config_can_be_disabled(tmp_path: Path) -> None:
    local = tmp_path / "config.local.toml"
    local.write_text("headless = true\n", encoding="utf-8")
    settings = load_settings(
        config_file=tmp_path / "missing.toml", local_file=local, use_local=False
    )
    assert settings.headless is False


def test_relative_paths_resolve_against_project_root() -> None:
    settings = Settings(cookie_file="cookie.txt", book_root="book", log_dir="logs")
    assert settings.cookie_path == PROJECT_ROOT / "cookie.txt"
    assert settings.book_root_path == PROJECT_ROOT / "book"
    assert settings.log_path == PROJECT_ROOT / "logs"


def test_absolute_paths_are_preserved(tmp_path: Path) -> None:
    target = tmp_path / "custom.txt"
    assert Settings(cookie_file=str(target)).cookie_path == target
    assert Settings(out_dir=str(tmp_path)).book_dir() == tmp_path


def test_stealth_script_defaults_into_package() -> None:
    settings = Settings()
    assert settings.stealth_js_path.name == "stealth.min.js"
    assert settings.stealth_js_path.parent.name == "js"


def test_book_dir_uses_title_and_id(tmp_path: Path) -> None:
    settings = Settings(ebook_id="450696", book_root=str(tmp_path / "book"))
    assert settings.book_dir("一觉睡到小时候").name == "一觉睡到小时候_450696"


def test_book_dir_prefers_existing_directory(tmp_path: Path) -> None:
    """离线命令（--rebuild-only）拿不到书名，也必须命中已有目录。"""
    existing = tmp_path / "book" / "某本书_450696"
    existing.mkdir(parents=True)
    settings = Settings(ebook_id="450696", book_root=str(tmp_path / "book"))
    assert settings.book_dir() == existing
    assert settings.existing_book_dirs() == [existing]


def test_with_overrides_and_describe() -> None:
    settings = Settings().with_overrides(headless=True, start_page=5)
    assert settings.headless is True and settings.start_page == 5
    labels = [label for label, _ in settings.describe()]
    assert "输出目录" in labels
    with pytest.raises(ConfigError, match="未知配置项"):
        Settings().with_overrides(nope=1)
