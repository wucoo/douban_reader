"""共享夹具。

**全部测试都是离线的**：不启动浏览器、不联网、不创建 Chrome。
金标准回归直接吃 ``book/<书名>_<ebook_id>/pages/*.json`` 这份真实抓取快照
（数据缺失时自动跳过，保证换机器也能跑通其余测试）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from douban_reader.models import Page
from douban_reader.settings import PROJECT_ROOT
from douban_reader.storage import BookStore

BOOK_ROOT = PROJECT_ROOT / "book"
EBOOK_ID = "450696"
LEGACY_MANIFEST = Path(__file__).parent / "fixtures" / "legacy_output_manifest.json"


def find_snapshot() -> Path | None:
    """找到 book/ 下属于测试用 ebook_id 的抓取快照目录。"""
    if not BOOK_ROOT.is_dir():
        return None
    suffix = f"_{EBOOK_ID}"
    hits = sorted(
        path
        for path in BOOK_ROOT.iterdir()
        if path.is_dir() and path.name.endswith(suffix) and (path / "pages").is_dir()
    )
    return hits[0] if hits else None


@pytest.fixture(scope="session")
def snapshot_dir() -> Path:
    """真实抓取快照目录（缺失则跳过测试）。"""
    path = find_snapshot()
    if path is None:
        pytest.skip(f"缺少 book/*_{EBOOK_ID}/pages 抓取快照，跳过金标准回归")
    return path


@pytest.fixture(scope="session")
def snapshot_pages(snapshot_dir: Path) -> list[Page]:
    """快照里的全部页对象（按页码排序）。"""
    pages = BookStore(snapshot_dir).load_pages()
    return [pages[number] for number in sorted(pages)]


@pytest.fixture(scope="session")
def legacy_manifest() -> dict[str, Any]:
    """重构前实现的输出指纹（sha256 + 行数，不含书稿正文）。"""
    return json.loads(LEGACY_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture
def book_root(tmp_path: Path) -> Path:
    """空的书稿目录（用于落盘测试）。"""
    root = tmp_path / "book" / f"测试书_{EBOOK_ID}"
    root.mkdir(parents=True)
    return root
