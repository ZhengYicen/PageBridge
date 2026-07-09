"""EPUB 解析器 — 使用 ebooklib + BeautifulSoup"""
import html
import logging
from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

logger = logging.getLogger("ai-reader.parse.epub")


class EpubParser:
    """解析 EPUB 文件，返回结构化章节和段落"""

    def parse(self, file_path: str) -> list[dict]:
        """
        返回章节列表，每章结构：
        {
            "title": str,
            "chapter_order": int,
            "paragraphs": [
                {"text": str, "html": str, "page_number": 0}
            ]
        }
        """
        logger.info("开始解析 EPUB: %s", file_path)
        book = epub.read_epub(file_path)
        chapters = []
        toc_map = self._build_toc_map(book)
        order = 0

        items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        logger.info("EPUB 共 %d 个文档项", len(items))

        for idx, item in enumerate(items):
            item_id = item.get_id()
            # 通过 toc_map 获取标题，如果没有则用文件名
            title = toc_map.get(item_id, f"Chapter {order + 1}")
            content = item.get_content()

            soup = BeautifulSoup(content, "html.parser")

            # 移除 script/style
            for tag in soup(["script", "style", "nav"]):
                tag.decompose()

            # 提取纯文本段落
            paragraphs = self._extract_paragraphs(soup)

            if not paragraphs:
                logger.debug("  跳过空文档: %s", title)
                continue

            logger.info("  ├─ [%d/%d] %s (%d 段)", idx + 1, len(items), title, len(paragraphs))
            chapters.append({
                "title": title,
                "chapter_order": order,
                "paragraphs": paragraphs,
            })
            order += 1

        return chapters

    def _build_toc_map(self, book) -> dict[str, str]:
        """构建 item_id → 章节标题 的映射"""
        toc_map = {}
        toc = book.toc

        def walk(items):
            for item in items:
                if isinstance(item, tuple):
                    link, children = item[0], item[1:]
                    if hasattr(link, "href") and link.href:
                        # href 格式如 "text/chapter1.xhtml"
                        ref = link.href
                        title = link.title or ""
                        # 通过 ref 找到对应的 item_id
                        for doc_item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                            if doc_item.get_name() in ref or ref in doc_item.get_name():
                                toc_map[doc_item.get_id()] = title
                                break
                    walk(children)
                elif isinstance(item, epub.Link):
                    ref = item.href
                    title = item.title
                    for doc_item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                        if doc_item.get_name() in ref or ref in doc_item.get_name():
                            toc_map[doc_item.get_id()] = title
                            break

        walk(toc)
        return toc_map

    def _extract_paragraphs(self, soup: BeautifulSoup) -> list[dict]:
        """从 HTML 中提取段落"""
        paragraphs = []
        # 优先按 <p> 标签切分
        for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]):
            text = tag.get_text(strip=True)
            if not text:
                continue
            # 过滤过短的内容（可能是导航元素）
            if len(text) < 5:
                continue
            paragraphs.append({
                "text": text,
                "html": str(tag),
                "page_number": 0,
            })

        # 如果没有 <p> 标签，按 <div> 或 <section> 内的文本块切分
        if not paragraphs:
            body = soup.find("body") or soup
            raw_text = body.get_text("\n", strip=True)
            for line in raw_text.split("\n"):
                line = line.strip()
                if len(line) >= 10:
                    paragraphs.append({
                        "text": line,
                        "html": html.escape(line),
                        "page_number": 0,
                    })

        return paragraphs
