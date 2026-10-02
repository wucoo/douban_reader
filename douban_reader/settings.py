"""配置装配：默认值 ◀ config.toml ◀ config.local.toml ◀ 命令行参数。

三个刻意的设计决定
------------------
1. **相对路径一律相对项目根目录解析**。原实现用 ``"./cookie.txt"`` 与 ``"book"``，
   换个工作目录运行就会找不到文件；现在从任何目录运行结果都一样。
2. **未知配置键直接报错**。配置写错键名是最典型的静默故障，
   这里宁愿启动失败，也不让"改了配置却没生效"发生。
3. **本模块不导入 selenium**，可离线单测，也方便 ``--dry-run`` 只做配置检查。
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from . import naming
from .errors import ConfigError

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config.toml"
LOCAL_CONFIG_FILE = PROJECT_ROOT / "config.local.toml"
DEFAULT_STEALTH_JS = PACKAGE_DIR / "js" / "stealth.min.js"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 这些字段在配置文件里留空字符串等价于"未设置"
_OPTIONAL_STR_FIELDS = frozenset(
    {
        "book_title",
        "out_dir",
        "cookie_file",
        "cookie_string",
        "stealth_js",
        "user_data_dir",
        "chrome_driver_path",
    }
)


@dataclass(frozen=True, slots=True)
class Settings:
    """一次运行的全部可调参数。"""

    # ---------- 目标与范围 ----------
    ebook_id: str = "450696"
    book_title: str | None = None
    start_page: int = 1
    end_page: int = 200

    # ---------- 路径 ----------
    book_root: str = "book"
    out_dir: str | None = None
    cookie_file: str | None = "cookie.txt"
    cookie_string: str | None = None
    stealth_js: str | None = None
    log_dir: str = "logs"

    # ---------- 浏览器 ----------
    headless: bool = False
    page_load_timeout: float = 30.0
    element_timeout: float = 10.0
    user_data_dir: str | None = None
    chrome_driver_path: str | None = None
    block_resources: bool = False
    blocked_urls: tuple[str, ...] = ()

    # ---------- 抓取策略 ----------
    opportunistic: bool = True
    max_direct: int = 15
    use_search_jump: bool = True
    sleep_min: float = 1.5
    sleep_max: float = 3.5
    page_settle: float = 0.3
    open_settle: float = 2.0
    open_timeout: float = 30.0
    nav_timeout: float = 20.0
    search_timeout: float = 12.0
    parse_retries: int = 4
    capture_retries: int = 3
    image_timeout: float = 30.0
    max_turns: int = 500

    # ---------- 输出 ----------
    save_json: bool = True
    dedupe_page_overlap: bool = True

    # ---------- 日志 ----------
    log_level: str = "INFO"

    # ------------------------------------------------------------------ 校验

    def __post_init__(self) -> None:
        if not str(self.ebook_id).strip():
            raise ConfigError("ebook_id 不能为空")
        if self.start_page < 1:
            raise ConfigError(f"start_page 必须 >= 1，当前 {self.start_page}")
        if self.end_page < self.start_page:
            raise ConfigError(f"end_page({self.end_page}) 不能小于 start_page({self.start_page})")
        if self.sleep_min < 0 or self.sleep_max < self.sleep_min:
            raise ConfigError(
                f"随机等待区间非法：sleep_min={self.sleep_min}, sleep_max={self.sleep_max}"
            )
        if self.max_direct < 0:
            raise ConfigError(f"max_direct 必须 >= 0，当前 {self.max_direct}")
        for name in ("parse_retries", "capture_retries", "max_turns"):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} 必须 >= 1")
        if self.log_level.upper() not in LOG_LEVELS:
            raise ConfigError(f"log_level 必须是 {LOG_LEVELS} 之一，当前 {self.log_level!r}")

    # -------------------------------------------------------------- 路径解析

    def resolve_path(self, value: str | Path) -> Path:
        """相对路径按项目根目录解析；绝对路径原样返回。"""
        path = Path(value).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    @property
    def sleep_range(self) -> tuple[float, float]:
        return (self.sleep_min, self.sleep_max)

    @property
    def cookie_path(self) -> Path | None:
        return self.resolve_path(self.cookie_file) if self.cookie_file else None

    @property
    def stealth_js_path(self) -> Path:
        return self.resolve_path(self.stealth_js) if self.stealth_js else DEFAULT_STEALTH_JS

    @property
    def log_path(self) -> Path:
        return self.resolve_path(self.log_dir)

    @property
    def book_root_path(self) -> Path:
        return self.resolve_path(self.book_root)

    @property
    def user_data_path(self) -> Path | None:
        return self.resolve_path(self.user_data_dir) if self.user_data_dir else None

    # -------------------------------------------------------------- 输出目录

    def existing_book_dirs(self) -> list[Path]:
        """``book_root`` 下已存在的、属于当前 ebook_id 的目录。"""
        root = self.book_root_path
        if not root.is_dir():
            return []
        suffix = f"_{self.ebook_id}"
        return sorted(p for p in root.iterdir() if p.is_dir() and p.name.endswith(suffix))

    def book_dir(self, book_title: str | None = None) -> Path:
        """书稿输出目录。

        优先级：``out_dir``（显式指定）> 已存在的 ``book_root/*_<ebook_id>``
        > ``book_root/<书名>_<ebook_id>``。

        第二级优先级是给离线命令用的：``--rebuild-only`` 不打开浏览器、
        拿不到书名，也能准确命中已有目录。
        """
        if self.out_dir:
            return self.resolve_path(self.out_dir)
        root = self.book_root_path
        title = book_title or self.book_title
        if title:
            return root / naming.book_dir_name(title, self.ebook_id)
        existing = self.existing_book_dirs()
        if existing:
            if len(existing) > 1:
                logging.getLogger(__name__).warning(
                    "book_root 下存在多个匹配目录，使用 %s（可用 out_dir 显式指定）",
                    existing[0].name,
                )
            return existing[0]
        # 与旧实现保持一致的最后兜底名
        return root / naming.book_dir_name(f"ebook_{self.ebook_id}", self.ebook_id)

    # -------------------------------------------------------------- 覆盖/描述

    def with_overrides(self, **kwargs: Any) -> Settings:
        """返回覆盖了部分字段的新配置（None 表示不改）。"""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        unknown = sorted(set(clean) - {f.name for f in fields(self)})
        if unknown:
            raise ConfigError(f"未知配置项: {', '.join(unknown)}")
        return replace(self, **clean)

    def describe(self) -> list[tuple[str, str]]:
        """供 --dry-run 打印的生效配置。"""
        return [
            ("电子书 ID", str(self.ebook_id)),
            ("书名", self.book_title or "(自动读取)"),
            ("抓取范围", f"{self.start_page} - {self.end_page}"),
            ("输出目录", str(self.book_dir())),
            ("cookie 文件", str(self.cookie_path) if self.cookie_path else "(未使用)"),
            ("stealth 脚本", str(self.stealth_js_path)),
            ("无头模式", "是" if self.headless else "否"),
            ("顺手缓存", "开" if self.opportunistic else "关"),
            ("搜索跳页", f"开（阈值 {self.max_direct}）" if self.use_search_jump else "关"),
            ("翻页随机等待", f"{self.sleep_min} - {self.sleep_max} 秒"),
            ("去分页重复段", "开" if self.dedupe_page_overlap else "关"),
            ("输出 JSON", "是" if self.save_json else "否"),
            ("日志级别", self.log_level.upper()),
            ("日志目录", str(self.log_path)),
        ]


# --------------------------------------------------------------------- 装配


def load_toml(path: Path) -> dict[str, Any]:
    """读取 TOML 配置；文件不存在返回空 dict。"""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件解析失败 {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"配置文件读取失败 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是键值表: {path}")
    return data


def merge_layers(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """按顺序合并配置层，后面的覆盖前面的；None 视为"未设置"。"""
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if value is None:
                continue
            merged[key] = value
    return merged


def settings_from_mapping(data: Mapping[str, Any], base: Settings | None = None) -> Settings:
    """把 dict 转成 :class:`Settings`，未知键报错、空字符串归一为 None。"""
    base = base or Settings()
    known = {f.name for f in fields(Settings)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(
            "未知配置项: " + ", ".join(unknown) + "\n可用配置项: " + ", ".join(sorted(known))
        )
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if key in _OPTIONAL_STR_FIELDS:
            text = "" if value is None else str(value).strip()
            normalized[key] = text or None
        elif key == "blocked_urls":
            normalized[key] = tuple(str(v) for v in (value or ()))
        else:
            normalized[key] = value
    return replace(base, **normalized)


def load_settings(
    *,
    config_file: Path | None = None,
    local_file: Path | None = None,
    use_local: bool = True,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """按 默认值 ◀ config.toml ◀ config.local.toml ◀ overrides 装配配置。"""
    layers: list[Mapping[str, Any]] = [load_toml(config_file or DEFAULT_CONFIG_FILE)]
    local_path = local_file if local_file is not None else LOCAL_CONFIG_FILE
    if use_local and local_path is not None and local_path.is_file():
        layers.append(load_toml(local_path))
    if overrides:
        layers.append(overrides)
    return settings_from_mapping(merge_layers(*layers))
