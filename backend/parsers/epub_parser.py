"""EPUB 解析器 — 使用 ebooklib + BeautifulSoup，基于 spine/TOC 重构

不再粗暴遍历 ITEM_DOCUMENT，而是：
  1. 按 spine 顺序读取文档，提取语义 block stream（含 type/level/locator）
  2. 扁平化 TOC，保留 href + fragment
  3. 将 TOC entry 定位到 block stream 中的精确位置
  4. 以 TOC cut points 切割章节，支持 anchor 级别的精细切割
  5. TOC 异常时 fallback 到 heading 切割
"""

import html
import logging
import re
from pathlib import Path
from urllib.parse import urldefrag

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("ai-reader.parse.epub")

# 这些标签的内容会被丢弃
REMOVE_TAGS = frozenset({"script", "style", "nav", "noscript"})


class EpubParser:
    """解析 EPUB 文件，基于 spine 顺序提取 block stream，再按 TOC 切割章节"""

    # ── 公开接口 ──────────────────────────────────────

    def parse(self, file_path: str) -> list[dict]:
        """
        返回章节列表，保持对外兼容但增强字段：
        {
            "title": str,
            "chapter_order": int,
            "href": str,
            "paragraphs": [
                {
                    "text": str,
                    "html": str,
                    "type": "heading" | "paragraph" | "list_item" | "quote",
                    "level": int | None,
                    "locator": {"file": str, "file_order": int, "tag": str, "id": str | None},
                    "page_number": 0,
                }
            ]
        }
        """
        logger.info("开始解析 EPUB: %s", file_path)
        book = epub.read_epub(file_path)

        # 1. spine 顺序的文档列表
        spine_items = self._get_spine_items(book)
        logger.info("spine 文档数: %d", len(spine_items))

        # 2. 构建连续 block stream + 记录每个文件的起始 block index
        #    同时每文件构建 fragment_map: anchor_id → global_block_index
        blocks: list[dict] = []
        file_block_index: dict[str, int] = {}
        fragment_map: dict[str, int] = {}  # anchor_id → global_block_index
        for item, _ in spine_items:
            name = item.get_name()
            file_block_index[name] = len(blocks)
            content = item.get_content()
            soup = BeautifulSoup(content, "html.parser")
            item_blocks, file_frag_map = self._extract_blocks(soup, name)
            # 将文件内 fragment 偏移量转为全局 block index
            file_start = file_block_index[name]
            for aid, local_bi in file_frag_map.items():
                fragment_map[aid] = file_start + local_bi
            blocks.extend(item_blocks)
        logger.info("总 block 数: %d", len(blocks))

        # 3. 扁平化 TOC
        toc = self._flatten_toc(book)
        logger.info("TOC entries: %d", len(toc))

        # 4. 将 TOC entry 定位到 block stream
        #    只使用顶级（depth=0）条目作为章节边界；
        #    子条目（depth>=1，如 Index 下的 A/B/C）保留在父章节内，
        #    不额外生成独立章节。
        cuts = []
        for toc_idx, entry in enumerate(toc):
            if entry.get("depth", 0) > 0:
                logger.debug("跳过 TOC 子条目 (depth=%d): %s", entry["depth"], entry["title"])
                continue
            cut = self._locate_toc_entry(entry, blocks, file_block_index, fragment_map)
            if cut is not None:
                cut["toc_index"] = toc_idx
                cuts.append(cut)

        logger.info("成功定位的 cuts (top-level): %d", len(cuts))
        for c in cuts:
            logger.info("  cut [%2d] title=%-40s href=%-40s block=%d",
                        c["toc_index"], c["title"][:38], c["href"][:38], c["block_index"])

        # 5. 去重排序
        cuts = self._dedupe_cuts(cuts)
        logger.info("去重后 cuts: %d", len(cuts))

        # 6. 切割章节
        if len(cuts) >= 2:
            chapters = self._slice_by_toc(blocks, cuts)
        else:
            logger.warning("有效 cuts < 2 (%d 个), fallback 到 heading 切割", len(cuts))
            chapters = self._slice_by_headings(blocks)

        # 7. 移除内部 _tag 引用，保证 JSON 序列化安全
        for ch in chapters:
            for p in ch.get("paragraphs", []):
                p.pop("_tag", None)

        logger.info("最终章节数: %d", len(chapters))
        for ch in chapters:
            logger.info("  章 [%2d] %-50s %d 段",
                        ch.get("chapter_order", 0),
                        (ch.get("title") or "?")[:48],
                        len(ch.get("paragraphs", [])))

        return chapters

    # ── Spine ─────────────────────────────────────────

    def _get_spine_items(self, book) -> list[tuple]:
        """
        从 book.spine 获取按阅读顺序排列的文档列表。
        跳过 linear="no" 的辅助文档（如版权页等）。
        """
        if not book.spine:
            logger.warning("book.spine 为空, fallback 到 ITEM_DOCUMENT 枚举")
            return [(item, "yes") for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)]

        items: list[tuple] = []
        for item_id, linear in book.spine:
            item = book.get_item_with_id(item_id)
            if item is None:
                logger.warning("spine item_id=%s 无法找到对应 item, 跳过", item_id)
                continue
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            if linear == "no":
                logger.debug("跳过 linear=no 文档: %s", item.get_name())
                continue
            items.append((item, linear))

        if not items:
            logger.warning("spine 中无有效文档, fallback 到 ITEM_DOCUMENT")
            return [(item, "yes") for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)]

        return items

    # ── TOC 扁平化 ────────────────────────────────────

    def _flatten_toc(self, book) -> list[dict]:
        """
        将 book.toc 递归展开为有序列表，每项含：
        { "title": str, "href": str, "file": str, "fragment": str, "depth": int }
        depth=0 为顶级章节，depth>=1 为子条目（如 Index 下的字母分区）。
        """
        flat: list[dict] = []

        def walk(entries, depth=0):
            for entry in entries:
                # 处理嵌套元组: (parent_link, child1, child2, ...)
                if isinstance(entry, tuple) and len(entry) > 0:
                    first = entry[0]
                    if hasattr(first, "href"):
                        self._append_toc_entry(first, flat, depth)
                    # 子项可能又是元组或 Link，增加深度
                    for child in entry[1:]:
                        if isinstance(child, (list, tuple)):
                            walk(child, depth + 1)
                        elif hasattr(child, "href"):
                            self._append_toc_entry(child, flat, depth + 1)
                # 扁平 Link / Section
                elif hasattr(entry, "href"):
                    self._append_toc_entry(entry, flat, depth)

        walk(book.toc)
        return flat

    def _append_toc_entry(self, link, flat: list[dict], depth: int = 0):
        """从 Link/Section 对象提取 TOC entry"""
        href = link.href or ""
        file_part, fragment = urldefrag(href)
        flat.append({
            "title": (getattr(link, "title", None) or "").strip(),
            "href": href,
            "file": file_part,
            "fragment": fragment,
            "depth": depth,
        })

    # ── Block Stream 提取 ────────────────────────────

    def _extract_blocks(self, soup: BeautifulSoup, item_name: str) -> tuple[list[dict], dict[str, int]]:
        """
        从单个 XHTML 文档中提取语义 block 列表。
        返回 (blocks, fragment_map) ，其中 fragment_map 是 anchor_id → file_local_block_index。
        """
        # 先移除干扰标签
        for tag_name in REMOVE_TAGS:
            for el in soup.find_all(tag_name):
                el.decompose()

        blocks: list[dict] = []
        body = soup.find("body") or soup
        counter = [0]  # 可变计数器，记录 file 内顺序
        self._extract_blocks_recursive(body, item_name, blocks, counter)

        # 构建 fragment_map：对于 soup 中所有带 id 的 <a> 标签，
        # 找到其在 blocks 中对应的 block_index（可能因 text 过滤而找不到 → 用下一个 block）
        fragment_map: dict[str, int] = {}

        # 先收集所有 anchor id 及其所在的 block-level parent
        anchor_parents: list[tuple[str, Tag]] = []
        for a_tag in soup.find_all("a", id=True):
            aid = a_tag["id"]
            # 找到最近的语义 block-level 祖先
            for parent in a_tag.parents:
                if parent.name in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li"):
                    anchor_parents.append((aid, parent))
                    break

        # 将 anchor 映射到 block index
        for aid, parent in anchor_parents:
            found = False
            for bi, block in enumerate(blocks):
                if block["_tag"] is parent:
                    fragment_map[aid] = bi
                    found = True
                    break
            if not found:
                # parent block 被过滤了（文本过短），找下一个有效的 block
                # 由于 blocks 按 DOM 顺序排列，parent 之后第一个 sibling 或后续元素
                # 对应的 block 就是下一个块
                next_el = parent.find_next_sibling()
                while next_el is not None and next_el.name not in (
                    "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li",
                    "div", "section", "article",
                ):
                    next_el = next_el.find_next_sibling()
                if next_el is not None:
                    for bi, block in enumerate(blocks):
                        if block["_tag"] is next_el:
                            fragment_map[aid] = bi
                            found = True
                            break
            if not found:
                # 极端 fallback: 用 file 内第一个 block
                if blocks:
                    fragment_map[aid] = 0

        return blocks, fragment_map

    def _extract_blocks_recursive(
        self,
        container: Tag,
        item_name: str,
        blocks: list[dict],
        counter: list[int],
    ):
        """
        递归遍历容器子树，提取语义块。
        叶子语义标签（p/h1-h6/blockquote/li）→ 生成 block
        容器标签（div/section/article）→ 递归进入
        """
        for child in container.children:
            if not isinstance(child, Tag):
                continue

            name = child.name.lower()

            # ── 跳过 ──
            if name in REMOVE_TAGS:
                continue

            # ── 标题 h1-h6 ──
            if name.startswith("h") and len(name) == 2 and name[1].isdigit():
                level = int(name[1])
                text = self._clean_text(child.get_text(" ", strip=True))
                if text:  # 保留所有非空标题（含 TOC 锚点）
                    blocks.append(self._make_block(
                        text, str(child), "heading", level, item_name, counter[0], child,
                    ))
                    counter[0] += 1

            # ── 段落 ──
            elif name == "p":
                text = self._clean_text(child.get_text(" ", strip=True))
                if text:  # 保留所有非空段落（含 TOC 锚点）
                    blocks.append(self._make_block(
                        text, str(child), "paragraph", None, item_name, counter[0], child,
                    ))
                    counter[0] += 1

            # ── 引用 ──
            elif name == "blockquote":
                text = self._clean_text(child.get_text(" ", strip=True))
                if text:  # 保留所有非空引用
                    blocks.append(self._make_block(
                        text, str(child), "quote", None, item_name, counter[0], child,
                    ))
                    counter[0] += 1

            # ── 列表项 ──
            elif name == "li":
                text = self._clean_text(child.get_text(" ", strip=True))
                if text:  # 保留所有非空列表项
                    blocks.append(self._make_block(
                        text, str(child), "list_item", None, item_name, counter[0], child,
                    ))
                    counter[0] += 1

            # ── 容器标签，递归 ──
            elif name in (
                "div", "section", "article", "main",
                "header", "footer",
                "ol", "ul", "dl",
                "aside",
            ):
                self._extract_blocks_recursive(child, item_name, blocks, counter)

    # ── Block 工厂 ────────────────────────────────────

    def _make_block(
        self,
        text: str,
        html_str: str,
        block_type: str,
        level: int | None,
        item_name: str,
        file_order: int,
        tag: Tag,
    ) -> dict:
        return {
            "text": text,
            "html": html_str,
            "type": block_type,
            "level": level,
            "locator": {
                "file": item_name,
                "file_order": file_order,
                "tag": tag.name,
                "id": tag.get("id"),
            },
            "page_number": 0,
            "_tag": tag,  # 内部定位用，输出前移除
        }

    # ── 文本清洗 ──────────────────────────────────────

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = html.unescape(text)
        text = text.replace("\xa0", " ")     # 不间断空格 → 普通空格
        text = text.replace("\xad", "")      # 移除软连字符
        text = re.sub(r"\s+", " ", text)     # 合并连续空白
        text = re.sub(r"\s+([,.;:!?%)\]}>])", r"\1", text)  # 标点前多余空格
        text = text.strip()
        return text

    # ── TOC 定位 ─────────────────────────────────────

    def _locate_toc_entry(
        self,
        entry: dict,
        blocks: list[dict],
        file_block_index: dict[str, int],
        fragment_map: dict[str, int] | None = None,
    ) -> dict | None:
        """
        将 TOC entry 定位到 block stream 中的位置。
        查找顺序：
          1. 直接在 file_blocks 中搜索 fragment（_find_fragment_in_blocks）
          2. 通过 fragment_map 查找（覆盖被过滤的锚点段落）
          3. fallback 到文件起始
        返回 {"title", "href", "block_index", "depth"} 或 None
        """
        file_part = entry["file"]
        fragment = entry["fragment"]

        if not file_part:
            logger.warning("TOC entry 无 href: title=%s", entry["title"])
            return None

        matched_file = self._match_file(file_part, file_block_index)
        if matched_file is None:
            logger.warning("TOC href 文件无法匹配: file=%s title=%s", file_part, entry["title"])
            return None

        file_start = file_block_index[matched_file]

        result = {
            "title": entry["title"],
            "href": entry["href"],
            "depth": entry.get("depth", 0),
        }

        if fragment:
            # 方法 1: 在 file_blocks 中搜索 fragment
            file_end = self._get_file_end(matched_file, file_block_index, len(blocks))
            file_blocks = blocks[file_start:file_end]
            target_idx = self._find_fragment_in_blocks(fragment, file_blocks, file_start)
            if target_idx is not None:
                result["block_index"] = target_idx
                return result

            # 方法 2: 用 fragment_map 定位
            if fragment_map and fragment in fragment_map:
                global_bi = fragment_map[fragment]
                logger.debug("fragment #%s 通过 fragment_map 定位到 block %d | title=%s",
                             fragment, global_bi, entry["title"])
                result["block_index"] = global_bi
                return result

            logger.warning(
                "fragment #%s 在 %s 中未找到, fallback 到文件起始 | title=%s",
                fragment, matched_file, entry["title"],
            )

        result["block_index"] = file_start
        return result

    def _get_file_end(
        self,
        file_name: str,
        file_block_index: dict[str, int],
        total_blocks: int,
    ) -> int:
        """返回给定文件之后的第一个 block index（或 total_blocks）"""
        names = list(file_block_index.keys())
        try:
            idx = names.index(file_name)
            if idx + 1 < len(names):
                return file_block_index[names[idx + 1]]
            return total_blocks
        except ValueError:
            return total_blocks

    def _find_fragment_in_blocks(
        self,
        fragment: str,
        file_blocks: list[dict],
        file_start: int,
    ) -> int | None:
        """
        在文件对应的 blocks 中搜索包含 fragment id/name 的 block。
        BeautifulSoup Tag 存于 _tag 字段。
        """
        for i, block in enumerate(file_blocks):
            tag: Tag | None = block.get("_tag")
            if tag is None:
                continue
            # 检查 block 标签本身
            if tag.get("id") == fragment or tag.get("name") == fragment:
                return file_start + i
            # 检查 block 内部是否包含目标元素
            found = tag.find(id=fragment)
            if found is None:
                found = tag.find(name=fragment)
            if found is not None:
                return file_start + i
        return None

    def _match_file(
        self,
        file_part: str,
        file_block_index: dict[str, int],
    ) -> str | None:
        """
        将 TOC href 中的文件路径与 spine item 名称匹配。
        匹配策略：完全相等 → basename 相等 → 后缀匹配
        """
        keys = list(file_block_index.keys())
        file_part_posix = Path(file_part).as_posix()

        for k in keys:
            k_posix = Path(k).as_posix()

            # 1. 规范化后完全相等
            if k_posix == file_part_posix:
                return k

            # 2. basename 相等
            if Path(k).name == Path(file_part).name:
                return k

            # 3. 后缀匹配：一个以另一个结尾
            if k_posix.endswith(file_part_posix) or file_part_posix.endswith(k_posix):
                return k

        # 4. 不区分大小写的 basename 匹配
        file_base_lower = Path(file_part).name.lower()
        for k in keys:
            if Path(k).name.lower() == file_base_lower:
                return k

        return None

    # ── Cuts 去重 ─────────────────────────────────────

    def _dedupe_cuts(self, cuts: list[dict]) -> list[dict]:
        """按 block_index 排序并去重（保留首次出现，或同 index 时保留 depth 更小的）"""
        valid = [
            c for c in cuts
            if c is not None and c.get("block_index") is not None
        ]
        valid.sort(key=lambda c: c["block_index"])
        seen: dict[int, dict] = {}  # block_index → best cut
        for c in valid:
            bi = c["block_index"]
            if bi not in seen:
                seen[bi] = c
            else:
                # 同 index 时保留 depth 更小的（parent 优先）
                if c.get("depth", 99) < seen[bi].get("depth", 99):
                    seen[bi] = c
        result = [seen[bi] for bi in sorted(seen)]
        return result

    # ── 章节切割 ─────────────────────────────────────

    def _slice_by_toc(self, blocks: list[dict], cuts: list[dict]) -> list[dict]:
        """基于 TOC cut points 将 block stream 切割为章节列表"""
        chapters: list[dict] = []

        # ── 正文前的内容（Front Matter）──
        if cuts[0]["block_index"] > 0:
            front_paras = self._blocks_to_paragraphs(blocks[: cuts[0]["block_index"]])
            if front_paras:
                chapters.append({
                    "title": "Front Matter",
                    "chapter_order": 0,
                    "href": "",
                    "paragraphs": front_paras,
                })

        # ── 按 TOC entries 切割 ──
        for i in range(len(cuts)):
            start = cuts[i]["block_index"]
            end = cuts[i + 1]["block_index"] if i + 1 < len(cuts) else len(blocks)
            chapter_blocks = blocks[start:end]
            paras = self._blocks_to_paragraphs(chapter_blocks)
            chapters.append({
                "title": cuts[i]["title"],
                "chapter_order": len(chapters),
                "href": cuts[i].get("href", ""),
                "paragraphs": paras,
            })

        return chapters

    def _slice_by_headings(self, blocks: list[dict]) -> list[dict]:
        """
        Fallback：基于 h1/h2 标题切割章节。
        h1/h2 且文本长度 < 120 → 视为章节边界。
        """
        chapters: list[dict] = []

        # 找出所有的 h1/h2 短标题作为边界
        cut_indices: list[int] = []
        cut_titles: list[str] = []
        for i, b in enumerate(blocks):
            if b["type"] == "heading" and b["level"] in (1, 2) and len(b["text"]) < 120:
                cut_indices.append(i)
                cut_titles.append(b["text"])

        if not cut_indices:
            logger.warning("heading 切割: 未找到 h1/h2, 全书作为一章")
            paras = self._blocks_to_paragraphs(blocks)
            return [{
                "title": "全书",
                "chapter_order": 0,
                "href": "",
                "paragraphs": paras,
            }]

        logger.info("heading 切割: 找到 %d 个章节边界", len(cut_indices))

        # Front Matter
        if cut_indices[0] > 0:
            front_paras = self._blocks_to_paragraphs(blocks[: cut_indices[0]])
            if front_paras:
                chapters.append({
                    "title": "Front Matter",
                    "chapter_order": 0,
                    "href": "",
                    "paragraphs": front_paras,
                })

        for i in range(len(cut_indices)):
            start = cut_indices[i]
            end = cut_indices[i + 1] if i + 1 < len(cut_indices) else len(blocks)
            chapter_blocks = blocks[start:end]
            paras = self._blocks_to_paragraphs(chapter_blocks)
            chapters.append({
                "title": cut_titles[i],
                "chapter_order": len(chapters),
                "href": "",
                "paragraphs": paras,
            })

        return chapters

    # ── Block 输出转换 ────────────────────────────────

    def _blocks_to_paragraphs(self, blocks: list[dict]) -> list[dict]:
        """将内部 block 转为输出 paragraph dict（剥离 _tag）"""
        return [
            {
                "text": b["text"],
                "html": b["html"],
                "type": b["type"],
                "level": b["level"],
                "locator": b["locator"],
                "page_number": b.get("page_number", 0),
            }
            for b in blocks
        ]
