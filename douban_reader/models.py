"""领域数据模型。

数据流：::

    DOM ──parse_page.js──▶ Page ──chapters.build_chapters──▶ Chapter ──storage──▶ txt/json

三个模型都只做数据承载与纯转换，不含 I/O、不依赖 Selenium，
因此 :mod:`douban_reader.chapters` 与 :mod:`douban_reader.storage` 可以完全离线单测。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

ParagraphKind = Literal["text", "title", "image"]
BlockKind = Literal["text", "heading", "image"]

PARAGRAPH_KINDS: tuple[str, ...] = ("text", "title", "image")


@dataclass(slots=True)
class Paragraph:
    """页面里的一个段落块。

    ``type`` 的取值来自 DOM 解析：``title`` 是标题样式段、``text`` 是正文、
    ``image`` 是插图（``src`` 为远端地址，``path`` 为下载后的本地相对路径）。
    """

    type: ParagraphKind
    text: str = ""
    src: str = ""
    legend: str = ""
    path: str = ""
    download_failed: bool = False

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> Paragraph | None:
        """容错解析：字段缺失或类型不对时尽力降级，**空段落直接丢弃**。

        旧实现直接 ``item["type"]`` / ``item["src"]`` 取键，
        页面结构一变就会 KeyError 直接冒到最外层；
        这里改为静默跳过坏数据，并保持与 ``parse_page.js`` 一致的过滤规则：
        没有文字的正文段、既无地址又无图注的插图片都不进入结果。
        """
        if not isinstance(raw, Mapping):
            return None
        kind = str(raw.get("type") or "").strip()
        if kind not in PARAGRAPH_KINDS:
            return None

        text = raw.get("text")
        paragraph = cls(
            type=kind,  # type: ignore[arg-type]
            text="" if text is None else str(text),
            src=str(raw.get("src") or ""),
            legend=str(raw.get("legend") or ""),
            path=str(raw.get("path") or ""),
            download_failed=bool(raw.get("download_failed")),
        )
        if paragraph.type == "image":
            return paragraph if (paragraph.src or paragraph.legend or paragraph.path) else None
        return paragraph if paragraph.text.strip() else None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 pages/ 里的 JSON 结构。"""
        if self.type == "image":
            out: dict[str, Any] = {"type": "image", "src": self.src, "legend": self.legend}
            local = self.path or self.src
            if local:
                out["path"] = local
            if self.download_failed:
                out["download_failed"] = True
            return out
        return {"type": self.type, "text": self.text}


@dataclass(slots=True)
class Page:
    """一页内容。

    ``intentional`` 是断点续抓的核心语义：

    * ``True``  —— 落在本次 ``[start, end]`` 范围内，正式抓取，参与章节合成；
    * ``False`` —— 翻页路上"顺手"缓存的页，只作缓存，不参与章节合成。
    """

    page: int
    title: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    intentional: bool = False

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any], *, intentional: bool | None = None) -> Page:
        """从 dict 构造（来源既可能是 JS 返回值，也可能是 pages/ 里的缓存文件）。"""
        items = raw.get("paragraphs") or []
        paragraphs: list[Paragraph] = []
        if isinstance(items, list):
            for item in items:
                paragraph = Paragraph.from_raw(item)
                if paragraph is not None:
                    paragraphs.append(paragraph)
        return cls(
            page=int(raw.get("page") or 0),
            title=str(raw.get("title") or "").strip(),
            paragraphs=paragraphs,
            intentional=bool(raw.get("intentional")) if intentional is None else bool(intentional),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 pages/pN.json 的内容。"""
        return {
            "page": self.page,
            "title": self.title,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "intentional": bool(self.intentional),
        }


@dataclass(slots=True)
class Block:
    """章节正文里的一个块：正文段、小标题或插图。"""

    type: BlockKind
    text: str = ""
    path: str = ""
    legend: str = ""
    download_failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        if self.type == "image":
            out: dict[str, Any] = {"type": "image", "path": self.path, "legend": self.legend}
            if self.download_failed:
                out["download_failed"] = True
            return out
        return {"type": self.type, "text": self.text}

    def same_text_as(self, other: Block) -> bool:
        """判断两个块是否为"同一段正文"（插图不参与比较）。"""
        if self.type == "image" or other.type == "image":
            return False
        mine = self.text.strip()
        return bool(mine) and mine == other.text.strip()


@dataclass(slots=True)
class Chapter:
    """由连续同标题的正式页合成的章节。"""

    index: int  # 1 起的序号，直接决定文件名前缀；空章节也占号（与原实现一致）
    title: str
    start_page: int
    end_page: int
    blocks: list[Block] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "blocks": [b.to_dict() for b in self.blocks],
        }
