"""落盘测试：原子写、断点续抓语义、派生目录清理、章节产出。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from douban_reader import naming
from douban_reader.chapters import build_chapters
from douban_reader.models import Page, Paragraph
from douban_reader.storage import BookStore, atomic_write_json, atomic_write_text


def make_page(
    number: int, *, title: str = "甲", text: str = "正文", intentional: bool = True
) -> Page:
    return Page(
        page=number,
        title=title,
        paragraphs=[Paragraph(type="text", text=text)],
        intentional=intentional,
    )


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: bytes = b"image-bytes") -> None:
        self.status_code = status_code
        self._payload = payload

    def iter_content(self, chunk_size: int = 8192) -> Any:
        yield self._payload


class FakeSession:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.requested: list[str] = []

    def get(self, url: str, timeout: float | None = None, stream: bool = False) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(self.status_code)


# --------------------------------------------------------------------- 原子写


def test_atomic_write_text_uses_lf_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "out" / "a.txt"
    atomic_write_text(target, "第一行\n第二行\n")
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw.decode("utf-8") == "第一行\n第二行\n"
    assert list(target.parent.iterdir()) == [target], "不应留下临时文件"


def test_atomic_write_json_is_utf8_and_ends_with_newline(tmp_path: Path) -> None:
    target = tmp_path / "a.json"
    atomic_write_json(target, {"标题": "小偶像"})
    text = target.read_text(encoding="utf-8")
    assert "小偶像" in text, "中文不应被转义"
    assert text.endswith("\n")
    assert json.loads(text) == {"标题": "小偶像"}


# --------------------------------------------------------------------- 页缓存


def test_save_and_load_page_roundtrip(book_root: Path) -> None:
    store = BookStore(book_root)
    page = make_page(3, title="小偶像")
    assert store.save_page(page, intentional=True) is True

    loaded = store.load_pages()
    assert sorted(loaded) == [3]
    assert loaded[3].intentional is True
    assert loaded[3].paragraphs[0].text == "正文"


def test_save_page_does_not_downgrade_intentional(book_root: Path) -> None:
    """正式页不会被后来"顺手缓存"的版本覆盖。"""
    store = BookStore(book_root)
    store.save_page(make_page(3, text="正式内容"), intentional=True)

    assert store.save_page(make_page(3, text="缓存内容"), intentional=False) is False
    loaded = store.load_pages()[3]
    assert loaded.intentional is True
    assert loaded.paragraphs[0].text == "正式内容"


def test_save_page_can_promote_cache_to_intentional(book_root: Path) -> None:
    store = BookStore(book_root)
    store.save_page(make_page(3, text="缓存内容"), intentional=False)
    store.save_page(make_page(3, text="正式内容"), intentional=True)
    loaded = store.load_pages()[3]
    assert loaded.intentional is True
    assert loaded.paragraphs[0].text == "正式内容"


def test_load_pages_skips_broken_files(book_root: Path) -> None:
    store = BookStore(book_root)
    store.ensure_dirs()
    store.save_page(make_page(1), intentional=True)
    (store.pages_dir / "p2.json").write_text("{ 坏掉的 json", encoding="utf-8")
    (store.pages_dir / "note.txt").write_text("无关文件", encoding="utf-8")

    pages = store.load_pages()
    assert sorted(pages) == [1], "坏文件应跳过，且不影响其它页"


# --------------------------------------------------------------------- 派生目录


def test_clear_derived_only_removes_managed_files(book_root: Path) -> None:
    store = BookStore(book_root)
    store.ensure_dirs()
    managed_txt = store.chapter_dir / "003_小偶像_p3-6.txt"
    managed_json = store.json_dir / "003_小偶像_p3-6.json"
    index = store.json_dir / naming.INDEX_FILE
    for path in (managed_txt, managed_json, index):
        path.write_text("x", encoding="utf-8")
    stray_chapter = store.chapter_dir / "我的笔记.txt"
    stray_json = store.json_dir / "原始数据.json"
    for path in (stray_chapter, stray_json):
        path.write_text("别删我", encoding="utf-8")

    removed = store.clear_derived()

    assert removed == 3
    assert not managed_txt.exists() and not managed_json.exists() and not index.exists()
    assert stray_chapter.exists() and stray_json.exists(), "非本工具生成的文件必须保留"


# --------------------------------------------------------------------- 插图


def test_attach_images_sets_relative_path(book_root: Path) -> None:
    store = BookStore(book_root)
    page = Page(
        page=7,
        title="图",
        paragraphs=[Paragraph(type="image", src="https://example.com/a.PNG", legend="图注")],
        intentional=True,
    )
    session = FakeSession()
    store.attach_images(page, session)

    paragraph = page.paragraphs[0]
    assert paragraph.path == "../image/p7_0001.png", "扩展名应取自 URL 并转小写"
    assert paragraph.download_failed is False
    assert (store.image_dir / "p7_0001.png").read_bytes() == b"image-bytes"
    assert session.requested == ["https://example.com/a.PNG"]


def test_attach_images_reuses_existing_file(book_root: Path) -> None:
    store = BookStore(book_root)
    store.ensure_dirs()
    existing = store.image_dir / "p7_0001.jpg"
    existing.write_bytes(b"already-here")
    page = Page(page=7, paragraphs=[Paragraph(type="image", src="https://example.com/a.jpg")])

    session = FakeSession()
    store.attach_images(page, session)

    assert session.requested == [], "本地已有非空文件时不应重复下载"
    assert existing.read_bytes() == b"already-here"


def test_attach_images_marks_failure(book_root: Path) -> None:
    store = BookStore(book_root)
    page = Page(page=7, paragraphs=[Paragraph(type="image", src="https://example.com/a.jpg")])

    store.attach_images(page, FakeSession(status_code=404))

    paragraph = page.paragraphs[0]
    assert paragraph.download_failed is True
    assert paragraph.path == "https://example.com/a.jpg", "下载失败应退回远端地址"
    assert paragraph.to_dict()["download_failed"] is True


# --------------------------------------------------------------------- 章节产出


def test_write_book_produces_txt_json_and_index(book_root: Path) -> None:
    store = BookStore(book_root)
    chapters = build_chapters([make_page(1, title="甲"), make_page(2, title="乙")])

    written = store.write_book(
        chapters,
        ebook_id="450696",
        book_title="测试书",
        cached_pages=2,
        intentional_pages=2,
        covered_intentional=[1, 2],
        covered_cache_only=[],
    )

    assert [p.name for p in written.txt] == ["001_甲_p1-1.txt", "002_乙_p2-2.txt"]
    assert [p.name for p in written.json] == ["001_甲_p1-1.json", "002_乙_p2-2.json"]
    assert written.index is not None and written.index.name == naming.INDEX_FILE

    index = json.loads(written.index.read_text(encoding="utf-8"))
    assert set(index) == {
        "ebook_id",
        "book_title",
        "root_dir",
        "generated_at",
        "cached_pages",
        "intentional_pages",
        "covered_intentional",
        "covered_cache_only",
        "chapters",
    }
    assert index["ebook_id"] == "450696"
    assert [c["file"] for c in index["chapters"]] == ["001_甲_p1-1", "002_乙_p2-2"]
    assert index["root_dir"] == book_root.as_posix(), "JSON 内路径统一用正斜杠"


def test_write_book_skips_chapters_without_blocks(book_root: Path) -> None:
    store = BookStore(book_root)
    chapters = build_chapters(
        [
            Page(page=1, title="封面", paragraphs=[], intentional=True),
            make_page(2, title="正文"),
        ]
    )
    written = store.write_book(
        chapters,
        ebook_id="1",
        book_title="书",
        cached_pages=2,
        intentional_pages=2,
        covered_intentional=[1, 2],
        covered_cache_only=[],
    )
    # 空章节占编号但不产出文件，因此文件名从 002 开始
    assert [p.name for p in written.txt] == ["002_正文_p2-2.txt"]


def test_write_book_without_json(book_root: Path) -> None:
    store = BookStore(book_root, save_json=False)
    chapters = build_chapters([make_page(1)])
    written = store.write_book(
        chapters,
        ebook_id="1",
        book_title="书",
        cached_pages=1,
        intentional_pages=1,
        covered_intentional=[1],
        covered_cache_only=[],
    )
    assert written.json == [] and written.index is None
    assert not store.json_dir.exists()
