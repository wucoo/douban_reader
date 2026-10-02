"""日志装配。

约定
----
* 控制台：人类可读，级别由 ``log_level`` 决定；
* 文件：``logs/run-<时间戳>.log``，**恒为 DEBUG**，UTF-8，便于事后排查；
* 文本一律不用 emoji / 特殊箭头 —— Windows 下 stdout 被重定向到文件或管道时
  会退化成 GBK 编码，非 GBK 字符会直接抛 UnicodeEncodeError。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"
_NOISY_LOGGERS = ("urllib3", "selenium", "websocket", "trio")


def configure_console_encoding() -> None:
    """把标准输出/错误统一成 UTF-8。

    Windows 下当 stdout 不是控制台（重定向到文件、被管道接走）时，
    Python 用系统区域编码（简体中文环境为 GBK）编码，遇到 GBK 之外的字符会崩。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 取决于运行环境
            pass


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    *,
    prefix: str = "run",
) -> Path | None:
    """装配根 logger，返回日志文件路径（未启用文件日志时返回 None）。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(_normalize_level(level))
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(console)

    log_path: Path | None = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(file_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return log_path


def _normalize_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """取一个带包名前缀的 logger。"""
    return logging.getLogger(name)
