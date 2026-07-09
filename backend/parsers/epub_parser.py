"""EPUB 解析器 — 使用 ebooklib + BeautifulSoup，基于 spine/TOC 重构

不再粗暴遍历 ITEM_DOCUMENT，而是：
  1. 按 spine 顺序读取文档，提取语义 block stream（含 type/level/locator）
  2. 扁平化 TOC，保留 href + fragment
  3. 将 TOC entry 定位到 block stream 中的精确位置
  4. 以 TOC cut points 切割章节，支持 anchor 级别的精细切割
  5. TOC 异常时 fallback 到 heading 切割
  6. 提取并保存 EPUB 中的图片资源，生成 type=image block
"""

import html
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urldefrag

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("ai-reader.parse.epub")

# 这些标签的内容会被丢弃
REMOVE_TAGS = frozenset({"script", "style", "nav", "noscript"})

# 图片 MIME 类型前缀
IMAGE_MIME_PREFIXES = ("image/",)

# 存储根目录（由 config 定义）
STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage"


class EpubParser:
    """解析 EPUB 文件，基于 spine 顺序提取 block stream，再按 TOC 切割章节"""

    def __init__(self):
        # 图片映射：EPUB 内部路径 → Web 可访问 URL
        self._image_map: dict[str, str] = {}
        # book_id（用于图片路径构建）
        self._book_id: str | None = None

    # ── 公开接口 ──────────────────────────────────────

    def parse(self, file_path: str, book_id: str = None) -> list[dict]:
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
                    "type": "heading" | "paragraph" | "list_item" | "quote" | "image",
                    "level": int | None,
                    "locator": {"file": str, "file_order": int, "tag": str, "id": str | None},
                    "page_number": 0,
                    "paragraph_order": int,
                }
            ]
        }
        """
        self._book_id = book_id
        logger.info("开始解析 EPUB: %s", file_path)
        book = epub.read_epub(file_path)

        # 0. 提取图片资源（如果提供了 book_id）
        self._image_map = {}
        if book_id:
            self._extract_and_map_images(book, book_id)

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

    # ── 图片提取 ─────────────────────────────────────

    def _extract_and_map_images(self, book, book_id: str):
        """
        从 EPUB 中提取图片资源，保存到 storage/books/{book_id}/assets/，
        并构建 {EPUB内部路径 → Web URL} 映射。
        """
        asset_dir = STORAGE_DIR / "books" / book_id / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)

        used_names: set[str] = set()

        # 收集所有图片 item（ITEM_IMAGE + ITEM_SVG）
        image_items = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_IMAGE:
                image_items.append(item)
            elif item.get_type() == getattr(ebooklib, "ITEM_SVG", -1):
                image_items.append(item)
            elif "svg" in (getattr(item, "media_type", None) or "").lower():
                image_items.append(item)

        if not image_items:
            logger.info("EPUB 中未发现图片资源")
            return

        logger.info("EPUB 图片资源: %d 个", len(image_items))

        for item in image_items:
            original_name = item.get_name()
            basename = Path(original_name).name
            # 安全化文件名
            safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', basename)
            if not safe_name:
                safe_name = f"img_{uuid.uuid4().hex[:8]}"
            # 处理重名
            if safe_name in used_names:
                stem = Path(safe_name).stem
                ext = Path(safe_name).suffix
                counter = 1
                while f"{stem}_{counter}{ext}" in used_names:
                    counter += 1
                safe_name = f"{stem}_{counter}{ext}"
            used_names.add(safe_name)

            # 保存文件
            file_path = asset_dir / safe_name
            try:
                file_path.write_bytes(item.get_content())
            except Exception as e:
                logger.warning("图片保存失败: %s (%s)", original_name, e)
                continue

            # 构建 Web URL
            web_url = f"/api/books/{book_id}/assets/{safe_name}"
            self._image_map[original_name] = web_url

        logger.info("图片已保存: %d 个 → %s", len(self._image_map), asset_dir)

    def _resolve_image_src(self, img_src: str, item_name: str) -> str:
        """
        将 EPUB 内部的 img src 解析为可访问的 Web URL。
        处理相对路径（相对于 XHTML 文件位置）。
        """
        if not img_src:
            return ""

        # 1. 直接匹配
        if img_src in self._image_map:
            return self._image_map[img_src]

        # 2. 相对于 XHTML 目录解析
        item_dir = Path(item_name).parent
        resolved = (item_dir / img_src).as_posix()
        if resolved in self._image_map:
            return self._image_map[resolved]

        # 3. basename 匹配（最宽松）
        img_basename = Path(img_src).name
        for orig_name, web_url in self._image_map.items():
            if Path(orig_name).name == img_basename:
                return web_url

        # 未找到匹配
        return img_src

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
        图片标签（img/figure）→ 生成 image block
        容器标签（div/section/article）→ 递归进入
        """
        for child in container.children:
            if not isinstance(child, Tag):
                continue

            name = child.name.lower()

            # ── 跳过 ──
            if name in REMOVE_TAGS:
                continue

            # ── 独立图片 ──
            if name == "img":
                self._add_image_block(child, item_name, blocks, counter)
                continue

            # ── figure 标签 ──
            if name == "figure":
                # 提取内部所有 img
                imgs = child.find_all("img")
                if imgs:
                    for img in imgs:
                        self._add_image_block(img, item_name, blocks, counter)
                else:
                    # 没有 img 的 figure 当作 div 处理
                    self._extract_blocks_recursive(child, item_name, blocks, counter)
                continue

            # ── 标题 h1-h6 ──
            if name.startswith("h") and len(name) == 2 and name[1].isdigit():
                level = int(name[1])
                text = self._clean_text(self._extract_text(child))
                if text:
                    blocks.append(self._make_block(
                        text, str(child), "heading", level, item_name, counter[0], child,
                    ))
                    counter[0] += 1
                continue

            # ── 段落 ──
            if name == "p":
                self._extract_paragraph(child, item_name, blocks, counter)
                continue

            # ── 引用 ──
            if name == "blockquote":
                text = self._clean_text(self._extract_text(child))
                if text:
                    blocks.append(self._make_block(
                        text, str(child), "quote", None, item_name, counter[0], child,
                    ))
                    counter[0] += 1
                continue

            # ── 列表项 ──
            if name == "li":
                text = self._clean_text(self._extract_text(child))
                if text:
                    blocks.append(self._make_block(
                        text, str(child), "list_item", None, item_name, counter[0], child,
                    ))
                    counter[0] += 1
                continue

            # ── 容器标签，递归 ──
            if name in (
                "div", "section", "article", "main",
                "header", "footer",
                "ol", "ul", "dl",
                "aside",
            ):
                self._extract_blocks_recursive(child, item_name, blocks, counter)

    def _extract_paragraph(self, child: Tag, item_name: str, blocks: list[dict], counter: list[int]):
        """
        处理 <p> 标签：可能包含文本、图片、或两者都有。
        """
        imgs = child.find_all("img")
        text = self._clean_text(self._extract_text(child))

        # 有文字：生成 paragraph block
        if text:
            blocks.append(self._make_block(
                text, str(child), "paragraph", None, item_name, counter[0], child,
            ))
            counter[0] += 1

        # 有图片：额外生成 image block（无论是否有文字）
        for img in imgs:
            self._add_image_block(img, item_name, blocks, counter)

        # 既无文字也无图片：跳过
        if not text and not imgs:
            pass

    def _add_image_block(self, img_tag: Tag, item_name: str, blocks: list[dict], counter: list[int]):
        """将 <img> 标签生成为 image block"""
        src = img_tag.get("src", "")
        alt = img_tag.get("alt", "")

        # 解析图片 URL
        resolved_src = self._resolve_image_src(src, item_name)

        # 更新 html 中的 src
        if resolved_src != src and self._image_map:
            img_html = str(img_tag).replace(f'src="{src}"', f'src="{resolved_src}"')
            img_html = img_html.replace(f"src='{src}'", f"src='{resolved_src}'")
        else:
            img_html = str(img_tag)

        block = self._make_block(
            alt or "",
            img_html,
            "image",
            None,
            item_name,
            counter[0],
            img_tag,
        )
        # 额外记录图片信息
        block["_image_src"] = src
        block["_image_url"] = resolved_src
        blocks.append(block)
        counter[0] += 1

    # ── 文本提取 ─────────────────────────────────────

    def _extract_text(self, tag: Tag) -> str:
        """
        提取文本，保留 inline 连续性。
        使用空字符串作为连接符，避免 inline span 之间被插入多余空格。
        """
        return tag.get_text("", strip=False)

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
        text = text.replace("\xad", "")      # 移除软连字符 \xad
        # 将各种 Unicode 连字符标准化为普通连字符
        text = text.replace("‐", "-")   # 短连字符
        text = text.replace("‑", "-")   # 非断连字符
        text = re.sub(r"\s+", " ", text)     # 合并连续空白

        # 断词修复：situa- tion → situation
        # 仅匹配：小写字母 + 连字符 + 空白 + 小写字母
        # 不匹配：post-Soviet（连字符后大写）
        # 不匹配：all-Persia（连字符后大写）
        text = re.sub(r"([a-z])-\s+([a-z])", r"\1\2", text)

        # 修复 span 导致的空格：diff icult → difficult（小写+空格+小写且合并后成词）
        # 注意：这可能会合并正常两个词 "big apple" → "bigapple" 😰
        # 所以不能简单删除所有空格。上面那个正则已经足够处理连字符断行了。
        # 对于 span 无连字符的情况，get_text("") 已经解决。

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
        """将内部 block 转为输出 paragraph dict（剥离 _tag，添加 paragraph_order）"""
        return [
            {
                "text": b["text"],
                "html": b["html"],
                "type": b["type"],
                "level": b["level"],
                "locator": b["locator"],
                "page_number": b.get("page_number", 0),
                "paragraph_order": i,
            }
            for i, b in enumerate(blocks)
        ]
