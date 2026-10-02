"""文件与目录命名规范（唯一事实来源）。

把"名字怎么起"集中在一处，好处是 :mod:`douban_reader.storage` 与
:mod:`douban_reader.chapters` 不会各自发明一套拼法，
`--rebuild-only` 也能只靠命名规则认出"哪些文件是自己生成的"。
"""

from __future__ import annotations

import re

# Windows 文件名非法字符 + 换行/制表符
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_PAGE_FILE = re.compile(r"p(\d+)\.json")
# chapter/ 与 json/ 中的托管文件：001_章节名_p1-4.txt / .json
_MANAGED_DERIVED = re.compile(r"^\d{3,}_.+_p\d+-\d+\.(txt|json)$")

INDEX_FILE = "_index.json"
PAGES_DIR = "pages"
CHAPTER_DIR = "chapter"
JSON_DIR = "json"
IMAGE_DIR = "image"


def safe_name(name: str, max_len: int = 60) -> str:
    """把任意标题转换成可安全用作文件名的字符串。"""
    cleaned = _INVALID_CHARS.sub("_", name or "").strip().strip(". ")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned or "untitled"


def book_dir_name(title: str, ebook_id: str) -> str:
    """书稿目录名：``<书名>_<ebook_id>``。"""
    return f"{safe_name(title, max_len=40)}_{ebook_id}"


def page_file_name(page: int) -> str:
    """单页缓存文件名。"""
    return f"p{int(page)}.json"


def parse_page_file_name(name: str) -> int | None:
    """从 ``p12.json`` 解析出页码；不匹配返回 None。"""
    match = _PAGE_FILE.fullmatch(name)
    return int(match.group(1)) if match else None


def image_file_name(page: int, seq: int, ext: str) -> str:
    """插图文件名：``p12_0001.jpg``。"""
    return f"p{int(page)}_{seq:04d}{ext}"


def chapter_stem(index: int, title: str, start_page: int, end_page: int) -> str:
    """章节文件名主干（不含扩展名）：``003_示例章节_p3-6``。"""
    return f"{index:03d}_{safe_name(title)}_p{start_page}-{end_page}"


def is_managed_derived(name: str) -> bool:
    """判断是否为 chapter/ 或 json/ 中由本工具生成、可安全重建的文件。"""
    return name == INDEX_FILE or bool(_MANAGED_DERIVED.match(name))
