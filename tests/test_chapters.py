"""章节聚合测试。

两类断言：

1. **合成用例**：分组、编号、标题去重、Markdown 渲染等规则；
2. **金标准回归**（标记 ``golden``）：以 ``book/*_450696/pages`` 这份真实抓取快照为输入，
   对照 ``tests/fixtures/legacy_output_manifest.json``（重构前实现的输出指纹）：

   * ``dedupe_overlap=False`` 时**逐字重现**旧实现的 53 个章节（sha256 比对）；
   * ``dedupe_overlap=True`` 时**恰好**只删掉"上一页末段 == 下一页首段"的重复段，
     且与独立统计、旧版行数逐章对得上。
"""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from douban_reader import naming
from douban_reader.chapters import build_chapters, chapter_entries, render_markdown
from douban_reader.models import Block, Chapter, Page, Paragraph

# --------------------------------------------------------------------- 合成用例


def make_page(number: int, title: str, *texts: str, intentional: bool = True) -> Page:
    return Page(
        page=number,
        title=title,
        paragraphs=[Paragraph(type="text", text=t) for t in texts],
        intentional=intentional,
    )


def test_groups_consecutive_pages_by_title() -> None:
    pages = [
        make_page(1, "甲", "a1", "a2"),
        make_page(2, "甲", "a3"),
        make_page(3, "乙", "b1"),
    ]
    chapters = build_chapters(pages)
    assert [(c.title, c.start_page, c.end_page) for c in chapters] == [
        ("甲", 1, 2),
        ("乙", 3, 3),
    ]
    assert [b.text for b in chapters[0].blocks] == ["a1", "a2", "a3"]


def test_same_title_not_consecutive_starts_new_chapter() -> None:
    chapters = build_chapters(
        [make_page(1, "甲", "a"), make_page(2, "乙", "b"), make_page(3, "甲", "c")]
    )
    assert [c.title for c in chapters] == ["甲", "乙", "甲"]
    assert [c.index for c in chapters] == [1, 2, 3]


def test_empty_chapter_still_consumes_index() -> None:
    """与旧实现一致：没有正文块的章节也占一个编号，故首个有内容的章节从 002 开始。"""
    pages = [Page(page=1, title="封面", paragraphs=[], intentional=True), make_page(2, "正文", "x")]
    chapters = build_chapters(pages)
    assert [c.index for c in chapters] == [1, 2]
    assert [c.has_content for c in chapters] == [False, True]
    assert naming.chapter_stem(2, "正文", 2, 2) == "002_正文_p2-2"


def test_non_intentional_pages_are_ignored() -> None:
    pages = [make_page(1, "甲", "a"), make_page(2, "甲", "缓存页", intentional=False)]
    chapters = build_chapters(pages)
    assert [b.text for b in chapters[0].blocks] == ["a"]
    assert chapters[0].end_page == 1


def test_title_paragraph_matching_chapter_title_is_skipped() -> None:
    page = Page(
        page=1,
        title="小偶像",
        paragraphs=[
            Paragraph(type="title", text="小偶像"),
            Paragraph(type="title", text="第一节"),
            Paragraph(type="text", text="正文"),
        ],
        intentional=True,
    )
    blocks = build_chapters([page])[0].blocks
    assert [(b.type, b.text) for b in blocks] == [("heading", "第一节"), ("text", "正文")]


def test_missing_title_falls_back_to_default() -> None:
    assert build_chapters([Page(page=1, title="", intentional=True)])[0].title == "未命名章节"


def test_image_block_keeps_legend_and_local_path() -> None:
    page = Page(
        page=7,
        title="图",
        paragraphs=[
            Paragraph(
                type="image",
                src="https://example.com/a.jpg",
                legend="图注",
                path="../image/p7_0001.jpg",
            )
        ],
        intentional=True,
    )
    block = build_chapters([page])[0].blocks[0]
    assert block.to_dict() == {"type": "image", "path": "../image/p7_0001.jpg", "legend": "图注"}


def test_render_markdown_layout() -> None:
    chapter = Chapter(
        index=1,
        title="甲",
        start_page=1,
        end_page=1,
        blocks=[
            Block(type="text", text="第一段"),
            Block(type="heading", text="小标题"),
            Block(type="image", path="../image/p1_0001.jpg", legend="带]括号\\的图注"),
        ],
    )
    assert render_markdown(chapter) == (
        "第一段\n\n## 小标题\n\n![带\\]括号\\\\的图注](../image/p1_0001.jpg)\n"
    )


class TestDedupe:
    """跨页重复段只应去掉"每页第一个可用块"里的那一处。"""

    def test_removes_leading_repeat(self) -> None:
        pages = [make_page(1, "甲", "第一段", "第二段"), make_page(2, "甲", "第二段", "第三段")]
        deduped = build_chapters(pages, dedupe_overlap=True)[0]
        legacy = build_chapters(pages, dedupe_overlap=False)[0]
        assert [b.text for b in deduped.blocks] == ["第一段", "第二段", "第三段"]
        assert [b.text for b in legacy.blocks] == ["第一段", "第二段", "第二段", "第三段"]

    def test_keeps_repeat_inside_page(self) -> None:
        """页内刻意重复的句子不能被删（规则只作用于页首）。"""
        pages = [make_page(1, "甲", "重复", "重复")]
        blocks = build_chapters(pages, dedupe_overlap=True)[0].blocks
        assert [b.text for b in blocks] == ["重复", "重复"]

    def test_does_not_dedupe_across_chapter_boundary(self) -> None:
        pages = [make_page(1, "甲", "末句"), make_page(2, "乙", "末句")]
        chapters = build_chapters(pages, dedupe_overlap=True)
        assert [b.text for b in chapters[1].blocks] == ["末句"]


# --------------------------------------------------------------------- 金标准回归


def _content_lines(text: str) -> list[str]:
    """取出所有非空行（分页重复表现为"整块同文"，比较非空行即可）。"""
    return [line for line in _normalize(text).split("\n") if line.strip()]


def _normalize(text: str) -> str:
    """换行归一为 LF：旧实现写出的文件是 CRLF，新实现统一 LF。"""
    return text.replace("\r\n", "\n")


def _collapse_repeats(lines: list[str]) -> list[str]:
    """把相邻重复行折叠掉（插图行不参与，与实现里的 same_text_as 语义一致）。"""
    collapsed: list[str] = []
    for line in lines:
        if collapsed and line == collapsed[-1] and not line.startswith("!"):
            continue
        collapsed.append(line)
    return collapsed


def _candidate_texts(page: Page) -> list[str]:
    """按聚合规则列出该页会进入正文的文本（忽略插图与"与章节名同名"的标题）。"""
    chapter_title = (page.title or "未命名章节").strip()
    texts: list[str] = []
    for paragraph in page.paragraphs:
        if paragraph.type == "image":
            continue
        if paragraph.type == "title" and paragraph.text.strip() == chapter_title:
            continue
        texts.append(paragraph.text.strip())
    return texts


def _count_page_overlaps(pages: list[Page]) -> int:
    """独立于实现地数一遍"上一页末段 == 下一页首段"（同章节内）。"""
    ordered = [p for p in sorted(pages, key=lambda p: p.page) if p.intentional]
    overlaps = 0
    for previous, current in pairwise(ordered):
        if previous.title.strip() != current.title.strip():
            continue
        left, right = _candidate_texts(previous), _candidate_texts(current)
        if left and right and left[-1] == right[0]:
            overlaps += 1
    return overlaps


def _pairs(chapters: list[Chapter]) -> list[tuple[Chapter, str]]:
    return [
        (c, naming.chapter_stem(c.index, c.title, c.start_page, c.end_page))
        for c in chapters
        if c.has_content
    ]


@pytest.mark.golden
def test_golden_chapter_files_map_one_to_one(
    snapshot_dir: Path, snapshot_pages: list[Page]
) -> None:
    chapters = build_chapters(snapshot_pages, dedupe_overlap=True)
    produced = {stem for _, stem in _pairs(chapters)}
    on_disk = {path.stem for path in (snapshot_dir / "chapter").glob("*.txt")}
    assert produced == on_disk, "章节文件与 pages/ 快照不是一一对应"


@pytest.mark.golden
def test_golden_legacy_mode_reproduces_old_output(
    snapshot_pages: list[Page], legacy_manifest: dict[str, Any]
) -> None:
    """移植保真度：关闭去重后，章节文本必须与**重构前实现**的输出完全一致。

    比对基准是 ``tests/fixtures/legacy_output_manifest.json``：
    它记录了旧实现对同一份 pages 快照产出的 53 个章节的 sha256 与行数
    （只存指纹，不含书稿正文，因此可以随仓库长期保存）。
    """
    chapters = build_chapters(snapshot_pages, dedupe_overlap=False)
    produced = {stem: render_markdown(chapter) for chapter, stem in _pairs(chapters)}
    expected = legacy_manifest["chapters"]

    assert set(produced) == set(expected), "章节文件清单与旧实现不一致"
    assert len(produced) == legacy_manifest["chapter_count"] == 53

    for stem, text in produced.items():
        normalized = _normalize(text)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        assert digest == expected[stem]["txt_sha256"], f"{stem}: 文本与旧实现不一致"
        assert len(_content_lines(text)) == expected[stem]["line_count"], stem


@pytest.mark.golden
def test_golden_dedupe_removes_exactly_page_overlaps(
    snapshot_dir: Path, snapshot_pages: list[Page], legacy_manifest: dict[str, Any]
) -> None:
    """开启去重后：只少了分页重叠段，且每章数量与独立统计逐一对得上。"""
    with_dedupe = _pairs(build_chapters(snapshot_pages, dedupe_overlap=True))
    without_dedupe = _pairs(build_chapters(snapshot_pages, dedupe_overlap=False))

    assert [stem for _, stem in without_dedupe] == [stem for _, stem in with_dedupe], (
        "去重不应改变章节划分，只应去掉重复段"
    )

    expected = legacy_manifest["chapters"]
    without_map = {stem: chapter for chapter, stem in without_dedupe}
    removed = 0
    for chapter, stem in with_dedupe:
        legacy_lines = _content_lines(render_markdown(without_map[stem]))
        new_lines = _content_lines(render_markdown(chapter))
        assert new_lines == _collapse_repeats(legacy_lines), stem
        # 与旧实现的指纹交叉核对：未去重行数应等于旧版行数
        assert len(legacy_lines) == expected[stem]["line_count"], stem
        removed += len(legacy_lines) - len(new_lines)

    assert removed == legacy_manifest["total_removed_duplicates"] == 88
    assert removed == _count_page_overlaps(snapshot_pages), (
        "去重数量与「跨页尾首相重」的独立统计不符"
    )

    # 落盘结果应当是去重后的版本（也验证 writer 与聚合结果一致）
    for chapter, stem in with_dedupe:
        on_disk = (snapshot_dir / "chapter" / f"{stem}.txt").read_text(encoding="utf-8")
        assert _content_lines(on_disk) == _content_lines(render_markdown(chapter)), stem


@pytest.mark.golden
def test_golden_index_matches_chapters_on_disk(
    snapshot_dir: Path, snapshot_pages: list[Page], legacy_manifest: dict[str, Any]
) -> None:
    """_index.json 与当前聚合结果一致，且 block_count 的减少量正好等于去重掉的段数。"""
    chapters = build_chapters(snapshot_pages, dedupe_overlap=True)
    index = json.loads((snapshot_dir / "json" / naming.INDEX_FILE).read_text(encoding="utf-8"))

    assert index["chapters"] == chapter_entries(chapters)
    assert index["cached_pages"] == len(snapshot_pages) == 200
    assert index["intentional_pages"] == len(snapshot_pages)
    assert index["covered_intentional"] == sorted(p.page for p in snapshot_pages)
    assert "\\" not in index["root_dir"], "JSON 内路径应统一用正斜杠"

    for entry in index["chapters"]:
        legacy_blocks = legacy_manifest["chapters"][entry["file"]]["block_count"]
        assert legacy_blocks - entry["block_count"] >= 0, entry["file"]
