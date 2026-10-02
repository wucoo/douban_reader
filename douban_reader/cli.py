"""命令行入口：``python -m douban_reader`` 或 ``douban-reader``。

退出码（脚本化调用的判断依据）::

    0  成功
    1  未预期异常
    2  cookie 失效，需要重新登录后更新 cookie.txt
    3  部分页未抓到（重跑同一命令即可续抓）
    4  翻页/跳页中断
    5  配置或凭据文件有问题
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from .errors import ConfigError, ExitCode
from .logging_setup import configure_console_encoding, setup_logging
from .scraper import Scraper, ScrapeResult
from .settings import LOG_LEVELS, Settings, load_settings

logger = logging.getLogger("douban_reader.cli")

VERSION = "1.0.0"

_EPILOG = """\
示例：
  python -m douban_reader                     # 按 config.toml 抓取
  python -m douban_reader --dry-run           # 只看生效配置与计划，不启动浏览器
  python -m douban_reader --rebuild-only      # 只用 pages/ 缓存重建章节，不联网
  python -m douban_reader --start 1 --end 20 --headless
  python -m douban_reader --min-delay 3 --max-delay 6   # 放慢节奏，降低风控概率

配置优先级：config.toml < config.local.toml < 命令行参数
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douban-reader",
        description="豆瓣阅读电子书抓取器（Selenium + 断点续抓）",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ebook-id", help="电子书 ID（阅读器地址里的数字）")
    parser.add_argument("--book-title", help="书名，留空则自动从页面标题解析")
    parser.add_argument("--start", type=int, dest="start_page", help="起始页（含）")
    parser.add_argument("--end", type=int, dest="end_page", help="结束页（含）")
    parser.add_argument("--book-root", help="输出父目录，默认 book/")
    parser.add_argument("--out-dir", help="显式指定完整输出目录（优先于 book_root）")
    parser.add_argument("--cookie-file", help="cookie 文件路径（纯文本或 JSON）")
    parser.add_argument("--cookie-string", help="直接传入 cookie 字符串")
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="无头模式（调试 DOM 时建议关闭）",
    )
    parser.add_argument("--max-direct", type=int, help="逐页翻页的距离阈值，超过则用搜索跳页")
    parser.add_argument(
        "--opportunistic",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="翻页路上顺手缓存非目标页",
    )
    parser.add_argument(
        "--use-search-jump",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="允许使用搜索框跳页",
    )
    parser.add_argument(
        "--dedupe-overlap",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="去掉分页造成的跨页重复段",
    )
    parser.add_argument(
        "--save-json",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="输出 json/ 目录",
    )
    parser.add_argument("--min-delay", type=float, dest="sleep_min", help="翻页随机等待下限（秒）")
    parser.add_argument("--max-delay", type=float, dest="sleep_max", help="翻页随机等待上限（秒）")
    parser.add_argument("--log-level", choices=LOG_LEVELS, help="控制台日志级别")
    parser.add_argument("--config", dest="config_file", type=Path, help="配置文件路径")
    parser.add_argument("--no-local-config", action="store_true", help="忽略 config.local.toml")
    parser.add_argument(
        "--rebuild-only", action="store_true", help="只用 pages/ 缓存重建章节（不启动浏览器）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印生效配置与抓取计划")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主函数，返回进程退出码。"""
    configure_console_encoding()
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings(
            config_file=args.config_file,
            use_local=not args.no_local_config,
            overrides=_collect_overrides(args),
        )
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return int(ExitCode.CONFIG)

    log_path = setup_logging(settings.log_level, settings.log_path)
    logger.debug("配置文件：%s", args.config_file or "config.toml")
    if log_path is not None:
        logger.debug("日志文件：%s", log_path)

    if args.dry_run:
        return _dry_run(settings)

    scraper = Scraper(settings)
    result = scraper.rebuild() if args.rebuild_only else scraper.run()
    _print_result(result)
    return int(result.exit_code)


def _collect_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """把命令行参数里"确实给了值"的项转成 Settings 覆盖字典。"""
    not_settings = {"config_file", "no_local_config", "rebuild_only", "dry_run"}
    overrides = {
        key: value
        for key, value in vars(args).items()
        if key not in not_settings and value is not None
    }
    if isinstance(overrides.get("book_title"), str) and not overrides["book_title"].strip():
        overrides.pop("book_title")
    return overrides


def _dry_run(settings: Settings) -> int:
    print("生效配置")
    for label, value in settings.describe():
        print(f"  {label:<14}{value}")

    plan = Scraper(settings).plan()
    todo: list[int] = list(plan["todo"])  # type: ignore[arg-type]
    print("\n抓取计划")
    print(f"  已缓存页数    {plan['cached_pages']}")
    print(f"  待正式抓取    {len(todo)} 页")
    if todo:
        preview = ", ".join(str(p) for p in todo[:20])
        more = "..." if len(todo) > 20 else ""
        print(f"  页码          {preview}{more}")
    print("\n（--dry-run 不启动浏览器、不写文件）")
    return int(ExitCode.OK)


def _print_result(result: ScrapeResult) -> None:
    print()
    print("运行结果")
    for line in result.summary_lines():
        print(f"  {line}")

    if result.written is not None and result.written.txt:
        print("  章节文件")
        for path in result.written.txt:
            print(f"    -> {path}")

    if result.error:
        print("\n提示：", file=sys.stderr)
        code = result.exit_code
        if code is ExitCode.COOKIE_EXPIRED:
            print(
                "  cookie 已失效：请在浏览器里重新登录豆瓣阅读，"
                "复制新的 cookie 覆盖 cookie.txt 后重跑。",
                file=sys.stderr,
            )
        elif code is ExitCode.PARTIAL:
            print("  重跑同一命令即可继续补抓未完成的页。", file=sys.stderr)
        elif code is ExitCode.NAVIGATION:
            print(
                "  导航中断：可加 --headless false 观察页面，或用 --no-search-jump 关闭搜索跳页。",
                file=sys.stderr,
            )
    elif result.incomplete_pages:
        print(
            f"\n提示：范围内还有 {len(result.incomplete_pages)} 页未抓完，重跑同一命令即可续抓。",
            file=sys.stderr,
        )
