"""落盘与缓存（:class:`BookStore`）。

目录约定（``root = <book_root>/<书名>_<ebook_id>``）::

    pages/   每页一个 pN.json —— 唯一事实源，带 intentional 标记
    chapter/ 由 intentional 页合成的 Markdown（派生，可随时重建）
    json/    同上的 JSON 结构（派生，可用 save_json 关闭）
    image/   插图（派生，重建需要联网）

三条规矩
--------
1. **派生目录只清理自己生成的文件**。旧实现 ``_rebuild`` 会删除 ``chapter/``
   与 ``json/`` 下的所有文件，往里面放一个笔记文件就会被静默删掉；
   现在按 :func:`douban_reader.naming.is_managed_derived` 精确匹配。
2. **原子写**：先写同目录临时文件再 ``os.replace``，中断不会留下半个 JSON。
3. **统一 UTF-8 + LF**：输出字节与运行平台无关（``image/`` 之外的相对路径
   一律使用 ``/``，旧实现在 Windows 上会往 JSON 里写入反斜杠）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import naming
from .chapters import chapter_entries, render_markdown
from .errors import StorageError
from .models import Chapter, Page

logger = logging.getLogger(__name__)

_IMAGE_EXT = re.compile(r"\.(jpe?g|png|gif|webp)", re.IGNORECASE)
_DEFAULT_IMAGE_EXT = ".jpg"

#: 插图相对 ``chapter/`` 的目录前缀（image/ 与 chapter/ 始终同级）
IMAGE_LINK_PREFIX = f"../{naming.IMAGE_DIR}/"


# --------------------------------------------------------------------- 原子写


def atomic_write_text(path: Path, text: str) -> None:
    """原子写入 UTF-8 / LF 文本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    """原子写入 JSON（``ensure_ascii=False`` + 2 空格缩进 + 末尾换行）。"""
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------- 结果


@dataclass(slots=True)
class WrittenBook:
    """一次聚合产出的文件清单。"""

    txt: list[Path] = field(default_factory=list)
    json: list[Path] = field(default_factory=list)
    index: Path | None = None
    removed: int = 0

    def as_posix(self) -> dict[str, Any]:
        return {
            "txt": [p.as_posix() for p in self.txt],
            "json": [p.as_posix() for p in self.json],
            "index": self.index.as_posix() if self.index else None,
        }


# --------------------------------------------------------------------- 存储


class BookStore:
    """书稿目录的读写门面：页缓存、插图、章节与索引。"""

    def __init__(
        self,
        root: str | Path,
        *,
        save_json: bool = True,
        image_timeout: float = 30.0,
    ) -> None:
        self.root = Path(root)
        self.save_json = save_json
        self.image_timeout = image_timeout

    # -------------------------------------------------------------- 目录

    @property
    def pages_dir(self) -> Path:
        return self.root / naming.PAGES_DIR

    @property
    def chapter_dir(self) -> Path:
        return self.root / naming.CHAPTER_DIR

    @property
    def json_dir(self) -> Path:
        return self.root / naming.JSON_DIR

    @property
    def image_dir(self) -> Path:
        return self.root / naming.IMAGE_DIR

    def ensure_dirs(self) -> None:
        for directory in (self.pages_dir, self.chapter_dir, self.image_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if self.save_json:
            self.json_dir.mkdir(parents=True, exist_ok=True)

    def page_path(self, page: int) -> Path:
        return self.pages_dir / naming.page_file_name(page)

    # -------------------------------------------------------------- 页缓存

    def load_pages(self) -> dict[int, Page]:
        """读取 pages/ 下全部页缓存；坏文件跳过并告警，不中断整体流程。"""
        pages: dict[int, Page] = {}
        if not self.pages_dir.is_dir():
            return pages
        for path in sorted(self.pages_dir.glob("p*.json")):
            number = naming.parse_page_file_name(path.name)
            if number is None:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("页缓存读取失败，已跳过 %s: %s", path.name, exc)
                continue
            if not isinstance(raw, dict):
                logger.warning("页缓存格式异常，已跳过 %s", path.name)
                continue
            pages[number] = Page.from_raw(raw)
        return pages

    def save_page(self, page: Page, *, intentional: bool) -> bool:
        """写入一页缓存。

        规则：**不把已正式抓取的页降级为"仅缓存"**（旧实现同样如此）。
        入参 ``page.intentional`` 会被就地更新，返回值表示是否真的写了盘。
        """
        path = self.page_path(page.page)
        if path.exists() and not intentional:
            existing = self._read_intentional_flag(path)
            if existing:
                logger.debug("p%d 已是正式页，忽略本次仅缓存写入", page.page)
                return False
        page.intentional = bool(intentional)
        atomic_write_json(path, page.to_dict())
        return True

    @staticmethod
    def _read_intentional_flag(path: Path) -> bool:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(isinstance(raw, dict) and raw.get("intentional"))

    # -------------------------------------------------------------- 插图

    def attach_images(self, page: Page, session: Any) -> Page:
        """下载本页插图并给段落补上本地相对路径（失败则退回远端地址并标记）。"""
        seq = 0
        for paragraph in page.paragraphs:
            if paragraph.type != "image":
                continue
            seq += 1
            ext = _DEFAULT_IMAGE_EXT
            match = _IMAGE_EXT.search(paragraph.src or "")
            if match:
                ext = "." + match.group(1).lower()
            name = naming.image_file_name(page.page, seq, ext)
            local = self.download_image(paragraph.src, self.image_dir / name, session)
            if local is not None:
                paragraph.path = IMAGE_LINK_PREFIX + name
                paragraph.download_failed = False
            else:
                paragraph.path = paragraph.src
                paragraph.download_failed = True
        return page

    def download_image(self, url: str, target: Path, session: Any) -> Path | None:
        """下载单张插图；已存在且非空则直接复用。返回本地路径或 None。"""
        if not url:
            return None
        if target.exists() and target.stat().st_size > 0:
            return target
        if session is None:
            logger.warning("没有可用的 HTTP 会话，跳过插图下载: %s", url)
            return None
        try:
            response = session.get(url, timeout=self.image_timeout, stream=True)
            if response.status_code != 200:
                logger.warning("插图下载失败 HTTP %s: %s", response.status_code, url)
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                for chunk in response.iter_content(8192):
                    handle.write(chunk)
            return target
        except Exception as exc:  # noqa: BLE001 - 网络异常一律降级为"该图失败"
            logger.warning("插图下载异常 %s: %s", url, exc)
            target.unlink(missing_ok=True)
            return None

    # -------------------------------------------------------------- 派生目录

    def clear_derived(self) -> int:
        """清空 chapter/ 与 json/ 中**由本工具生成**的文件，返回删除数量。"""
        removed = 0
        for directory in (self.chapter_dir, self.json_dir):
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.is_file() and naming.is_managed_derived(path.name):
                    try:
                        path.unlink()
                        removed += 1
                    except OSError as exc:
                        logger.warning("删除派生文件失败 %s: %s", path.name, exc)
        return removed

    # -------------------------------------------------------------- 聚合输出

    def write_book(
        self,
        chapters: list[Chapter],
        *,
        ebook_id: str,
        book_title: str,
        cached_pages: int,
        intentional_pages: int,
        covered_intentional: list[int],
        covered_cache_only: list[int],
    ) -> WrittenBook:
        """写章节 txt/json 与 ``_index.json``；返回产出清单。"""
        self.ensure_dirs()
        result = WrittenBook(removed=self.clear_derived())

        for chapter in chapters:
            if not chapter.has_content:
                continue
            stem = naming.chapter_stem(
                chapter.index, chapter.title, chapter.start_page, chapter.end_page
            )
            try:
                txt_path = self.chapter_dir / f"{stem}.txt"
                atomic_write_text(txt_path, render_markdown(chapter))
                result.txt.append(txt_path)
                if self.save_json:
                    json_path = self.json_dir / f"{stem}.json"
                    atomic_write_json(json_path, chapter.to_dict())
                    result.json.append(json_path)
            except OSError as exc:
                raise StorageError(f"章节写入失败 {stem}: {exc}") from exc

        if self.save_json:
            payload = {
                "ebook_id": ebook_id,
                "book_title": book_title,
                "root_dir": self.root.as_posix(),
                "generated_at": _now(),
                "cached_pages": cached_pages,
                "intentional_pages": intentional_pages,
                "covered_intentional": covered_intentional,
                "covered_cache_only": covered_cache_only,
                "chapters": chapter_entries(chapters),
            }
            try:
                result.index = self.json_dir / naming.INDEX_FILE
                atomic_write_json(result.index, payload)
            except OSError as exc:
                raise StorageError(f"索引写入失败 {result.index}: {exc}") from exc

        logger.info(
            "聚合完成：章节 %d 个，txt %d 个，json %d 个，清理派生文件 %d 个",
            sum(1 for c in chapters if c.has_content),
            len(result.txt),
            len(result.json),
            result.removed,
        )
        return result


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
