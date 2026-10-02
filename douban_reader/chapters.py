"""章节聚合与 Markdown 渲染（纯函数，零 I/O）。

这是整个项目里最值得单测的一块：输入是 ``list[Page]``，输出是 ``list[Chapter]``，
完全离线。``tests/test_chapters.py`` 直接吃 ``book/<书名>_<id>/pages/*.json``
这份真实快照做回归，重构是否等价由 diff 说话。

关于"去分页重复段"
------------------
豆瓣阅读器分页时会把上一页的末段重新渲染到下一页开头（实测 200 页里有 88 处），
直接拼接会让正文出现成对重复段落。``dedupe_overlap=True`` 的规则很窄：
**每页第一个可用块若与上一页末块同文，就判为分页重叠并丢弃**。
只在页首生效，因此不会误伤正文中刻意的重复语句。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from . import naming
from .models import Block, Chapter, Page, Paragraph

logger = logging.getLogger(__name__)

#: 页面没有标题时使用的章节名（与旧实现一致）
DEFAULT_CHAPTER_TITLE = "未命名章节"


def build_chapters(pages: Iterable[Page], *, dedupe_overlap: bool = True) -> list[Chapter]:
    """把正式页（``intentional=True``）按"标题连续"分组为章节。

    * 只处理正式页；顺手缓存的页不参与合成；
    * 按页码排序后，标题一变就开新章节（同名但不连续 = 两个章节）；
    * ``index`` 从 1 开始连续编号，**空章节也占号**，因此文件名前缀与原实现一致。
    """
    ordered = sorted((p for p in pages if p.intentional), key=lambda p: p.page)
    chapters: list[Chapter] = []
    current: Chapter | None = None

    for page in ordered:
        title = (page.title or DEFAULT_CHAPTER_TITLE).strip()
        if current is None or current.title != title:
            current = Chapter(
                index=len(chapters) + 1,
                title=title,
                start_page=page.page,
                end_page=page.page,
            )
            chapters.append(current)
        else:
            current.end_page = page.page

        _extend_blocks(current, page, dedupe_overlap=dedupe_overlap)

    skipped = [c.index for c in chapters if not c.has_content]
    if skipped:
        logger.debug("以下章节没有正文块，不产出文件：%s", skipped)
    return chapters


def _extend_blocks(chapter: Chapter, page: Page, *, dedupe_overlap: bool) -> None:
    """把一页的段落转成章节块并追加。

    ``first_of_page`` 保证去重判定只看每一页的第一个可用块，
    因此比较对象必然是**上一页**的末块。
    """
    first_of_page = True
    for paragraph in page.paragraphs:
        block = _to_block(paragraph, chapter.title)
        if block is None:
            continue
        if (
            first_of_page
            and dedupe_overlap
            and chapter.blocks
            and block.same_text_as(chapter.blocks[-1])
        ):
            logger.debug("去掉分页重叠段（p%d）：%s", page.page, block.text[:30])
            first_of_page = False
            continue
        first_of_page = False
        chapter.blocks.append(block)


def _to_block(paragraph: Paragraph, chapter_title: str) -> Block | None:
    """段落 → 章节块；返回 None 表示该段不需要进入正文。"""
    if paragraph.type == "image":
        return Block(
            type="image",
            path=paragraph.path or paragraph.src,
            legend=paragraph.legend,
            download_failed=paragraph.download_failed,
        )
    if paragraph.type == "title":
        # 与章节名完全相同的大标题不再重复输出（章节名已经体现在文件名与索引里）
        if paragraph.text.strip() == chapter_title:
            return None
        return Block(type="heading", text=paragraph.text)
    return Block(type="text", text=paragraph.text)


def render_markdown(chapter: Chapter) -> str:
    """章节 → Markdown 文本（原样保留旧输出的排版：段间空行、末尾单个换行）。"""
    lines: list[str] = []
    for block in chapter.blocks:
        if block.type == "text":
            lines.append(block.text)
            lines.append("")
        elif block.type == "heading":
            lines.append(f"## {block.text}")
            lines.append("")
        elif block.type == "image":
            legend = (block.legend or "").replace("\\", "\\\\").replace("]", "\\]")
            lines.append(f"![{legend}]({block.path or ''})")
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def chapter_entries(chapters: Iterable[Chapter]) -> list[dict[str, object]]:
    """生成 ``_index.json`` 里的章节清单（跳过空章节）。"""
    return [
        {
            "file": naming.chapter_stem(c.index, c.title, c.start_page, c.end_page),
            "title": c.title,
            "start_page": c.start_page,
            "end_page": c.end_page,
            "block_count": len(c.blocks),
        }
        for c in chapters
        if c.has_content
    ]
