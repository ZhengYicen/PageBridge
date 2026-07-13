"""PDF 解析器 — 使用 RapidOCR 识别扫描版/文字版 PDF，输出连续正文"""

from __future__ import annotations

import gc
import html as html_mod
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import fitz  # PyMuPDF — 仅用于页面渲染
import numpy as np
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger("ai-reader.parse")

# ── 英文常见前后缀（用于断词恢复保守判断） ──────────────
_COMMON_PREFIXES = frozenset({
    "anti", "auto", "bi", "co", "counter", "de", "dis", "down", "extra",
    "fore", "hyper", "il", "im", "in", "inter", "ir", "macro", "mal",
    "micro", "mid", "mis", "mono", "multi", "non", "out", "over",
    "poly", "post", "pre", "pro", "re", "semi", "sub", "super", "tele",
    "trans", "tri", "ultra", "un", "under", "up",
})

_COMMON_SUFFIXES = frozenset({
    "able", "age", "al", "ance", "ant", "ary", "dom", "ed", "en", "ence",
    "er", "est", "ful", "hood", "ial", "ian", "ible", "ic", "ical", "ing",
    "ion", "ious", "ish", "ist", "ity", "ive", "ization", "ize", "less",
    "like", "ling", "logy", "ly", "ment", "ness", "or", "ous", "ry",
    "ship", "sion", "some", "tail", "tion", "ture", "ty", "ward", "wise", "y",
})

# ── 纯符号噪声模式（不含任何字母数字） ────────────────
_PUNCTUATION_ONLY_RE = re.compile(
    r"^[\s\-–—•· ﻿.,;:!?\"'‘’“”()\[\]{}<>«»‥…・《》、，。；：？！【】「」『』〔〕]+$",
    re.UNICODE,
)

# ── 标题模式（用于章节检测） ──────────────────────────
_CHAPTER_PATTERNS = [
    re.compile(r"^(chapter|ch\.?)\s+\d+[\.:]?\s*$", re.IGNORECASE),
    re.compile(
        r"^(chapter|ch\.?)\s+"
        r"(one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety|hundred)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(part|pt\.?)\s+\d+[\.:]?\s*$", re.IGNORECASE),
    re.compile(
        r"^(introduction|preface|prologue|epilogue|appendix|index|"
        r"bibliography|foreword|acknowledgements?|about the author|"
        r"afterword|conclusion|summary|notes|references?)\b",
        re.IGNORECASE,
    ),
]


# ═══════════════════════════════════════════════════════════
# PdfParser
# ═══════════════════════════════════════════════════════════

class PdfParser:
    """
    PDF 解析器 — 使用 RapidOCR 进行 OCR 识别，PyMuPDF 仅用于页面渲染。

    解析流程:
        渲染 → OCR → 标准化 → 排序 → 同行合并
        → 页眉页脚清理 → 断词恢复 → 段落重建
        → 跨页合并 → 章节识别 → 输出
    """

    def __init__(self, **kwargs):
        # RapidOCR 实例懒初始化（首次 _ocr_all_pages 时创建）
        self._ocr_engine: Optional[RapidOCR] = None
        # 页面尺寸映射 {page_number: (width, height)}
        self._page_dims: dict[int, tuple[int, int]] = {}

    # ── 公开接口 ────────────────────────────────────────

    def parse(self, file_path: str, **kwargs) -> list[dict]:
        """
        解析 PDF 文件，返回章节列表。

        接受 **kwargs 以兼容 books.py 传入的 book_id 等额外参数。

        返回:
            [{
                "title": str,
                "chapter_order": int,
                "paragraphs": [{
                    "text": str,
                    "html": str,
                    "paragraph_order": int,
                    "page_number": int,
                    "page_end": int,
                    "bbox": str,
                    "confidence": float,
                }]
            }]
        """
        pages = self._render_pages(file_path)
        if not pages:
            logger.warning("[PDF] 无页面可渲染: %s", file_path)
            return []

        raw_results = self._ocr_all_pages(pages)
        blocks = self._normalize_ocr_results(raw_results)
        if not blocks:
            logger.warning("[PDF] OCR 未识别出任何文本")
            return []

        blocks = self._sort_reading_order(blocks)
        lines = self._merge_same_line_blocks(blocks)
        if not lines:
            return []

        lines = self._clean_header_footer(lines)
        lines = self._fix_hyphenation(lines)
        paragraphs = self._reconstruct_paragraphs(lines)
        paragraphs = self._merge_cross_page_paragraphs(paragraphs)
        chapters = self._detect_chapters(paragraphs)
        return self._build_output(chapters)

    # ══════════════════════════════════════════════════
    # 1. 页面渲染
    # ══════════════════════════════════════════════════

    def _render_pages(self, file_path: str) -> list[dict]:
        """
        使用 PyMuPDF 将每页渲染为 ~300 DPI 的 numpy 数组 (RGB)。

        返回:
            [{"image": np.ndarray, "page_number": int, "width": int, "height": int}]
        """
        doc = fitz.open(file_path)
        pages = []
        matrix = fitz.Matrix(300 / 72, 300 / 72)

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, 3
                )
                pages.append({
                    "image": img,
                    "page_number": page_num + 1,  # 1-indexed
                    "width": pix.width,
                    "height": pix.height,
                })
                self._page_dims[page_num + 1] = (pix.width, pix.height)
        finally:
            doc.close()

        logger.info("[PDF] 渲染完成: %d 页", len(pages))
        return pages

    # ══════════════════════════════════════════════════
    # 2. OCR 识别（复用实例）
    # ══════════════════════════════════════════════════

    def _ocr_all_pages(self, pages: list[dict]) -> list[list[list]]:
        """
        对每页图片执行 OCR，复用 RapidOCR 实例。
        返回 per-page 列表: [[[bbox, text, confidence], ...], ...]
        """
        if self._ocr_engine is None:
            logger.info("[PDF] 初始化 RapidOCR ...")
            self._ocr_engine = RapidOCR()

        all_results: list[list[list]] = []
        for page_info in pages:
            img = page_info["image"]
            pn = page_info["page_number"]
            try:
                result, _ = self._ocr_engine(img)
                if result is None:
                    all_results.append([])
                else:
                    all_results.append(result)
            except Exception as exc:
                logger.warning("[PDF] 第 %d 页 OCR 失败: %s", pn, exc)
                all_results.append([])
            finally:
                del img
                if pn % 10 == 0:
                    gc.collect()

        return all_results

    # ══════════════════════════════════════════════════
    # 3. OCR 结果标准化
    # ══════════════════════════════════════════════════

    @staticmethod
    def _is_garbage(text: str) -> bool:
        """判断是否为无意义乱码（纯符号/空格等）。"""
        if not text or not text.strip():
            return True
        if _PUNCTUATION_ONLY_RE.match(text):
            return True
        return False

    def _normalize_ocr_results(self, raw_results: list[list[list]]) -> list[dict]:
        """
        将 RapidOCR 原始输出转为统一的标准化块列表。

        每条: {"bbox": {...}, "text": str, "confidence": float, "page_number": int}
        """
        blocks = []
        for page_idx, page_results in enumerate(raw_results):
            page_number = page_idx + 1
            if not page_results:
                continue

            for entry in page_results:
                if len(entry) < 3:
                    continue
                pts, text, conf = entry[0], str(entry[1]), float(entry[2])

                # 四点坐标 → 矩形 bbox
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                bbox_rect = {
                    "x1": min(xs),
                    "y1": min(ys),
                    "x2": max(xs),
                    "y2": max(ys),
                }

                text = text.strip()
                if self._is_garbage(text):
                    continue

                # 保留低置信度结果，仅过滤明显乱码
                blocks.append({
                    "bbox": bbox_rect,
                    "text": text,
                    "confidence": conf,
                    "page_number": page_number,
                })

        logger.info("[PDF] 标准化后共 %d 个文字块", len(blocks))
        return blocks

    # ══════════════════════════════════════════════════
    # 4. 页面阅读顺序排序
    # ══════════════════════════════════════════════════

    def _sort_reading_order(self, blocks: list[dict]) -> list[dict]:
        """
        按阅读顺序排序。
        - 单栏: 从上到下，从左到右
        - 多栏: 按 X 分布检测栏位，先左栏后右栏，每栏内从上到下
        """
        by_page: dict[int, list[dict]] = defaultdict(list)
        for b in blocks:
            by_page[b["page_number"]].append(b)

        result = []
        for page_num in sorted(by_page):
            page_blocks = by_page[page_num]
            sorted_blocks = self._sort_page_blocks(page_blocks, page_num)
            result.extend(sorted_blocks)

        return result

    def _sort_page_blocks(self, blocks: list[dict], page_num: int) -> list[dict]:
        """对单页内的文字块按阅读顺序排序。"""
        if len(blocks) <= 3:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        w = self._page_dims.get(page_num, (None, None))[0]
        if w is None:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        # 检测多栏: 分析 X 坐标分布
        x_centers = [(b["bbox"]["x1"] + b["bbox"]["x2"]) / 2 for b in blocks]
        sorted_centers = sorted(x_centers)
        block_widths = [b["bbox"]["x2"] - b["bbox"]["x1"] for b in blocks]
        actual_width = max(b["bbox"]["x2"] for b in blocks)
        page_width = max(actual_width, float(w))

        if len(sorted_centers) < 2:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        # 计算相邻 X 中心之间的间隙
        gaps = []
        for i in range(len(sorted_centers) - 1):
            gap = sorted_centers[i + 1] - sorted_centers[i]
            gaps.append(gap)

        max_gap = max(gaps)
        is_multi_column = max_gap > page_width * 0.15

        if not is_multi_column:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        # 多栏: 从最大间隙处切分
        max_gap_idx = gaps.index(max_gap)
        split_x = (sorted_centers[max_gap_idx] + sorted_centers[max_gap_idx + 1]) / 2

        left_col = [
            b for b in blocks
            if (b["bbox"]["x1"] + b["bbox"]["x2"]) / 2 < split_x
        ]
        right_col = [
            b for b in blocks
            if (b["bbox"]["x1"] + b["bbox"]["x2"]) / 2 >= split_x
        ]

        left_col.sort(key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))
        right_col.sort(key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        return left_col + right_col

    # ══════════════════════════════════════════════════
    # 5. 同行文字块合并
    # ══════════════════════════════════════════════════

    @staticmethod
    def _estimate_char_width(bbox_height: float) -> float:
        """根据文字框高度估算单个字符平均宽度。"""
        return max(bbox_height * 0.5, 4.0)

    def _merge_same_line_blocks(self, blocks: list[dict]) -> list[dict]:
        """
        将同一视觉行内的文字块合并为一行。

        按 (page, Y 中心) 分组后按 X 排序拼接，
        根据相邻块间距智能决定是否加空格。
        """
        by_page: dict[int, list[dict]] = defaultdict(list)
        for b in blocks:
            by_page[b["page_number"]].append(b)

        lines = []
        for page_num in sorted(by_page):
            page_blocks = by_page[page_num]

            # 中位文字框高度
            heights = [b["bbox"]["y2"] - b["bbox"]["y1"] for b in page_blocks]
            median_height = float(np.median(heights)) if heights else 16.0
            y_tolerance = median_height * 0.4

            # 按 Y 分组（同一视觉行）
            sorted_by_y = sorted(page_blocks, key=lambda b: b["bbox"]["y1"])
            y_groups: list[list[dict]] = []

            for block in sorted_by_y:
                y_center = (block["bbox"]["y1"] + block["bbox"]["y2"]) / 2
                placed = False
                for group in y_groups:
                    ref = (group[0]["bbox"]["y1"] + group[0]["bbox"]["y2"]) / 2
                    if abs(y_center - ref) < y_tolerance:
                        group.append(block)
                        placed = True
                        break
                if not placed:
                    y_groups.append([block])

            # 合并每行
            for group in y_groups:
                if not group:
                    continue
                group.sort(key=lambda b: b["bbox"]["x1"])

                texts = []
                total_conf = 0.0
                conf_cnt = 0
                merged_bbox = {
                    "x1": min(b["bbox"]["x1"] for b in group),
                    "y1": min(b["bbox"]["y1"] for b in group),
                    "x2": max(b["bbox"]["x2"] for b in group),
                    "y2": max(b["bbox"]["y2"] for b in group),
                }

                for i, block in enumerate(group):
                    block_text = block["text"]
                    char_w = self._estimate_char_width(
                        block["bbox"]["y2"] - block["bbox"]["y1"]
                    )

                    if i > 0:
                        prev = group[i - 1]
                        gap = block["bbox"]["x1"] - prev["bbox"]["x2"]
                        if self._needs_space(prev["text"], block_text, gap, char_w):
                            texts.append(" " + block_text)
                        else:
                            texts.append(block_text)
                    else:
                        texts.append(block_text)

                    total_conf += block["confidence"] * len(block_text)
                    conf_cnt += len(block_text)

                avg_conf = total_conf / conf_cnt if conf_cnt > 0 else 0.0
                merged_text = "".join(texts)

                lines.append({
                    "text": merged_text,
                    "bbox": merged_bbox,
                    "confidence": round(avg_conf, 4),
                    "page_number": page_num,
                })

        logger.info("[PDF] 同行合并后共 %d 行", len(lines))
        return lines

    @staticmethod
    def _needs_space(prev_text: str, next_text: str, gap: float, char_width: float) -> bool:
        """判断相邻两个文字块之间是否需要空格。"""
        if not prev_text or not next_text:
            return False

        # 前块以连字符结尾 → 不加空格（可能为断词）
        if prev_text.endswith("-"):
            return False

        # 后块以标点开头 → 不加空格
        if next_text[0] in ",.:;!?)]}%>":
            return False

        # 间距 < 半个字符宽度 → 同一词的碎片，不加空格
        if gap < char_width * 0.5:
            return False

        # 有间距 → 加空格
        return gap > char_width * 0.3

    # ══════════════════════════════════════════════════
    # 6. 页眉 / 页脚 / 页码清理
    # ══════════════════════════════════════════════════

    def _clean_header_footer(self, lines: list[dict]) -> list[dict]:
        """
        跨页统计删除页眉、页脚和页码。

        策略: 相同文本在近似位置出现 ≥3 页且出现率 ≥40%，则删除。
        单独在顶部/底部的纯数字作为页码删除。
        """
        if len(lines) < 5:
            return lines

        by_page: dict[int, list[dict]] = defaultdict(list)
        for line in lines:
            by_page[line["page_number"]].append(line)

        page_nums = sorted(by_page)
        if len(page_nums) < 3:
            return lines

        # (相对_y桶, 归一化文本) → 出现页面集合
        pos_text_pages: dict[tuple, set[int]] = defaultdict(set)

        for pn in page_nums:
            page_lines = by_page[pn]
            h = self._page_dims.get(pn, (None, 1))[1] or 1

            for line in page_lines:
                y_center = (line["bbox"]["y1"] + line["bbox"]["y2"]) / 2
                rel_y = round(y_center / h, 1)  # 0.0 ~ 1.0

                # 只检测顶部 15% 和底部 12%
                if rel_y > 0.15 and rel_y < 0.88:
                    continue

                norm_text = line["text"].strip().lower()
                if not norm_text:
                    continue

                key = (rel_y, norm_text)
                pos_text_pages[key].add(pn)

        # 确定最小出现页数阈值
        total_pages = len(page_nums)
        if total_pages <= 5:
            min_pages = max(2, int(total_pages * 0.45))
        else:
            min_pages = 3

        # 标记要删除的行索引
        to_remove: set[int] = set()

        for (rel_y, norm_text), pages_set in pos_text_pages.items():
            if len(pages_set) >= min_pages and len(pages_set) / total_pages >= 0.40:
                for idx, line in enumerate(lines):
                    ln = line["page_number"]
                    ly = (line["bbox"]["y1"] + line["bbox"]["y2"]) / 2
                    h_line = self._page_dims.get(ln, (None, 1))[1] or 1
                    rel_ly = round(ly / h_line, 1)
                    if rel_ly == rel_y and line["text"].strip().lower() == norm_text:
                        to_remove.add(idx)

        # 单独页码: 纯数字出现在顶部/底部
        for idx, line in enumerate(lines):
            pn = line["page_number"]
            h = self._page_dims.get(pn, (None, 1))[1] or 1
            y_center = (line["bbox"]["y1"] + line["bbox"]["y2"]) / 2
            rel_y = round(y_center / h, 1)
            text_stripped = line["text"].strip()
            if text_stripped.isdigit() and (rel_y < 0.10 or rel_y > 0.88):
                if len(text_stripped) <= 5:
                    to_remove.add(idx)

        cleaned = [line for idx, line in enumerate(lines) if idx not in to_remove]
        removed = len(lines) - len(cleaned)
        if removed:
            logger.info("[PDF] 移除 %d 个页眉/页脚/页码", removed)

        return cleaned

    # ══════════════════════════════════════════════════
    # 7. 行尾断词恢复
    # ══════════════════════════════════════════════════

    def _fix_hyphenation(self, lines: list[dict]) -> list[dict]:
        """
        恢复行尾断词。
        如 "cor-" + "rections" → "corrections"。
        保守策略: 仅当符合语言规则时才合并。
        """
        if len(lines) < 2:
            return lines

        result = list(lines)
        i = 0
        merges = 0

        while i < len(result) - 1:
            current = result[i]
            next_line = result[i + 1]

            cur_text = current["text"].rstrip()
            nxt_text = next_line["text"].lstrip()

            if (
                cur_text.endswith("-")
                and nxt_text
                and nxt_text[0].islower()
                and len(nxt_text) >= 2
            ):
                same_or_adjacent = (
                    next_line["page_number"] == current["page_number"]
                    or next_line["page_number"] == current["page_number"] + 1
                )

                if same_or_adjacent:
                    part1 = cur_text[:-1].strip()
                    part2 = nxt_text

                    if self._should_merge_hyphen(part1, part2):
                        merged_text = part1 + part2
                        result[i]["text"] = merged_text
                        result[i]["bbox"]["x2"] = max(
                            result[i]["bbox"]["x2"], next_line["bbox"]["x2"]
                        )
                        result[i]["bbox"]["y2"] = max(
                            result[i]["bbox"]["y2"], next_line["bbox"]["y2"]
                        )

                        l1, l2 = len(part1), len(part2)
                        total_l = l1 + l2
                        if total_l > 0:
                            result[i]["confidence"] = round(
                                (result[i]["confidence"] * l1
                                 + next_line["confidence"] * l2) / total_l, 4
                            )

                        del result[i + 1]
                        merges += 1
                        continue

            i += 1

        if merges:
            logger.info("[PDF] 恢复 %d 处行尾断词", merges)
        return result

    @staticmethod
    def _should_merge_hyphen(part1: str, part2: str) -> bool:
        """保守判断是否合并断词。"""
        if len(part1) <= 2 or len(part2) <= 2:
            return False
        combined = part1 + part2
        if len(combined) < 5:
            return False
        if part2.lower() in _COMMON_SUFFIXES:
            return True
        if part1.lower() in _COMMON_PREFIXES:
            return True
        if not part1[0].isupper() and not part2[0].isupper():
            if len(combined) >= 6:
                return True
        return False

    # ══════════════════════════════════════════════════
    # 8. 自然段重建
    # ══════════════════════════════════════════════════

    def _reconstruct_paragraphs(self, lines: list[dict]) -> list[dict]:
        """
        将连续行重建为自然段。

        段落分界信号:
        - 行距 > 1.5x 正常行高
        - 下行首行缩进
        - 上行短（< 页宽 55%）且以句号结尾
        - 文字框高度变化 > 30%
        - 文本居中（疑似小标题）
        """
        if not lines:
            return []

        paragraphs = []
        current_lines = [lines[0]]

        for i in range(1, len(lines)):
            prev = lines[i - 1]
            curr = lines[i]
            if self._is_paragraph_break(prev, curr, lines):
                paragraphs.append(current_lines)
                current_lines = [curr]
            else:
                current_lines.append(curr)

        if current_lines:
            paragraphs.append(current_lines)

        result = [self._merge_para_lines(pl) for pl in paragraphs]
        logger.info("[PDF] 段落重建: %d 段", len(result))
        return result

    def _is_paragraph_break(self, prev: dict, curr: dict, all_lines: list[dict]) -> bool:
        """判断 prev 和 curr 之间是否应分段。"""
        pn = curr["page_number"]
        page_w = self._page_dims.get(pn, (None, None))[0]

        prev_h = prev["bbox"]["y2"] - prev["bbox"]["y1"]
        curr_h = curr["bbox"]["y2"] - curr["bbox"]["y1"]
        prev_bottom = prev["bbox"]["y2"]
        curr_top = curr["bbox"]["y1"]
        gap = curr_top - prev_bottom

        prev_text = prev["text"].strip()
        curr_text = curr["text"].strip()

        if not curr_text:
            return True

        # 1. 大行距
        if prev_h > 0 and gap > prev_h * 1.5:
            return True

        # 2. 首行缩进（当前行左边界明显右偏）
        page_x1s = [l["bbox"]["x1"] for l in all_lines if l["page_number"] == pn]
        normal_left = float(np.median(page_x1s)) if page_x1s else curr["bbox"]["x1"]
        left_indent = curr["bbox"]["x1"] - normal_left
        if left_indent > 15:
            return True

        # 2b. 前后行左边界显著不同（如 blockquote、页面换栏）
        if prev_h > 0 and curr_h > 0:
            x_diff = abs(curr["bbox"]["x1"] - prev["bbox"]["x1"])
            if x_diff > max(prev_h, curr_h) * 2:
                return True

        # 3. 短行 + 句号结尾
        if page_w and page_w > 0:
            prev_width = prev["bbox"]["x2"] - prev["bbox"]["x1"]
            if prev_width < page_w * 0.55 and prev_text.endswith((".", "!", "?")):
                return True

        # 4. 字号变化 > 30%
        if prev_h > 0 and curr_h > 0:
            ratio = max(prev_h, curr_h) / min(prev_h, curr_h)
            if ratio > 1.30:
                return True

        # 5. 居中文本
        if self._is_centered_text(curr, page_w):
            return True

        return False

    @staticmethod
    def _is_centered_text(line: dict, page_w: Optional[float]) -> bool:
        """判断一行文本是否居中。

        条件:
        - 中心在页面中心附近 ±8%
        - 左侧起始 > 15% 页宽（排除从左边距开始的长行）
        - 宽度不超过页宽 55%（短文本）
        """
        if page_w is None or page_w <= 0:
            return False
        line_cx = (line["bbox"]["x1"] + line["bbox"]["x2"]) / 2
        line_w = line["bbox"]["x2"] - line["bbox"]["x1"]
        page_center = page_w / 2.0
        if (
            abs(line_cx - page_center) < page_w * 0.08
            and line["bbox"]["x1"] > page_w * 0.15
            and line_w < page_w * 0.55
        ):
            return True
        return False

    def _merge_para_lines(self, para_lines: list[dict]) -> dict:
        """将一组段落行合并为一个段落块。"""
        text_parts = []
        for i, line in enumerate(para_lines):
            t = line["text"].strip()
            if not t:
                continue
            if i > 0 and text_parts:
                text_parts.append(" " + t)
            else:
                text_parts.append(t)

        merged_text = "".join(text_parts)
        merged_text = self._clean_text(merged_text)

        bbox = {
            "x1": min(l["bbox"]["x1"] for l in para_lines),
            "y1": min(l["bbox"]["y1"] for l in para_lines),
            "x2": max(l["bbox"]["x2"] for l in para_lines),
            "y2": max(l["bbox"]["y2"] for l in para_lines),
        }

        page_numbers = [l["page_number"] for l in para_lines]
        start_page = min(page_numbers)
        end_page = max(page_numbers)

        total_len = sum(len(l["text"]) for l in para_lines)
        if total_len > 0:
            avg_conf = sum(
                l["confidence"] * len(l["text"]) for l in para_lines
            ) / total_len
        else:
            avg_conf = 0.0

        return {
            "text": merged_text,
            "html": f"<p>{html_mod.escape(merged_text)}</p>",
            "page_number": start_page,
            "page_end": end_page,
            "bbox": json.dumps(bbox, ensure_ascii=False),
            "confidence": round(avg_conf, 4),
            "source_lines": para_lines,
            "is_centered": self._is_centered_text(
                para_lines[0],
                self._page_dims.get(para_lines[0]["page_number"], (None, None))[0],
            ),
        }

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理段落文本中的 OCR 瑕疵。

        包括: 多余空格、标点前空格、行首行尾空白。
        """
        # 多个连续空格 → 一个
        text = re.sub(r" {2,}", " ", text)
        # 标点前多余空格
        text = re.sub(r'\s+([,.:;!?)\]}>%])', r'\1', text)
        # 左引号/括号后多余空格
        text = re.sub(r'([\[({¿¡])\s+', r'\1', text)
        text = text.strip()
        return text

    # ══════════════════════════════════════════════════
    # 9. 跨页段落合并
    # ══════════════════════════════════════════════════

    def _merge_cross_page_paragraphs(self, paragraphs: list[dict]) -> list[dict]:
        """
        合并跨页段落。
        如果上页最后一段没有明显结束，下页第一段以小写开头，则合并。
        """
        if len(paragraphs) < 2:
            return paragraphs

        result = list(paragraphs)
        i = 0
        merges = 0

        while i < len(result) - 1:
            curr = result[i]
            next_p = result[i + 1]

            if curr["page_end"] == next_p["page_number"] or (
                curr["page_end"] + 1 == next_p["page_number"]
            ):
                curr_text = curr["text"].rstrip()
                next_text = next_p["text"].lstrip()

                if (
                    curr_text
                    and next_text
                    and not curr_text[-1] in ".!?"
                    and next_text[0].islower()
                    and not next_p.get("is_centered", False)
                ):
                    merged_text = curr_text + " " + next_text
                    curr["text"] = merged_text
                    curr["html"] = f"<p>{html_mod.escape(merged_text)}</p>"
                    curr["page_end"] = next_p["page_end"]

                    # 合并 bbox
                    try:
                        curr_box = json.loads(curr["bbox"])
                        next_box = json.loads(next_p["bbox"])
                        merged_box = {
                            "x1": min(curr_box["x1"], next_box["x1"]),
                            "y1": min(curr_box["y1"], next_box["y1"]),
                            "x2": max(curr_box["x2"], next_box["x2"]),
                            "y2": max(curr_box["y2"], next_box["y2"]),
                        }
                        curr["bbox"] = json.dumps(merged_box, ensure_ascii=False)
                    except (json.JSONDecodeError, KeyError):
                        pass

                    # 加权平均置信度
                    l1, l2 = len(curr_text), len(next_text)
                    total = l1 + l2
                    if total > 0:
                        curr["confidence"] = round(
                            (curr["confidence"] * l1
                             + next_p["confidence"] * l2) / total, 4
                        )

                    del result[i + 1]
                    merges += 1
                    continue

            i += 1

        if merges:
            logger.info("[PDF] 合并 %d 处跨页段落", merges)
        return result

    # ══════════════════════════════════════════════════
    # 10. 章节标题识别
    # ══════════════════════════════════════════════════

    def _detect_chapters(self, paragraphs: list[dict]) -> list[dict[str, Any]]:
        """
        保守识别章节标题。

        1. 模式匹配: "Chapter 1", "Introduction", ...
           — 同时尝试 camelCase 分词版本（如 "ChapterOne" → "Chapter One"）
        2. 视觉特征: 居中 + 短文本 + 前间距大
        3. 无法识别时统一放入"全文"
        """
        if not paragraphs:
            return [{"title": "全文", "paragraphs": []}]

        def _camel_split(t: str) -> str:
            """在 lower→Upper 边界插入空格（用于标题 OCR 粘连恢复）。"""
            return re.sub(r"([a-z])([A-Z][a-z])", r"\1 \2", t)

        chapter_indices = []

        for i, para in enumerate(paragraphs):
            text = para["text"].strip()
            if not text:
                continue

            # ── 模式匹配 ─────────────────────────────────
            is_pattern_match = any(pat.match(text) for pat in _CHAPTER_PATTERNS)
            # camelCase 分词后再次尝试
            if not is_pattern_match:
                split_text = _camel_split(text)
                if split_text != text:
                    is_pattern_match = any(
                        pat.match(split_text) for pat in _CHAPTER_PATTERNS
                    )

            if is_pattern_match:
                chapter_indices.append(i)
                continue

            # ── 视觉特征 ─────────────────────────────────
            # 以句号结尾 → 跳过（大概率是普通段落末行）
            if text[-1] in ".!?":
                continue

            is_centered = para.get("is_centered", False)
            is_short = len(text) < 80

            # 检查前面段落的大间距
            has_gap = False
            if i > 0:
                prev = paragraphs[i - 1]
                try:
                    prev_bottom = json.loads(prev["bbox"]).get("y2", 0)
                    curr_top = json.loads(para["bbox"]).get("y1", 0)
                    if prev_bottom > 0 and (curr_top - prev_bottom) > 30:
                        has_gap = True
                except (json.JSONDecodeError, KeyError):
                    pass

            visual_score = sum([is_centered, is_short, has_gap])

            # 视觉评分 ≥2 且短文本 → 章节候选
            if visual_score >= 2 and is_short:
                chapter_indices.append(i)

        if not chapter_indices:
            return [{
                "title": "全文",
                "chapter_order": 0,
                "paragraphs": [p for p in paragraphs if p["text"].strip()],
            }]

        chapters = []
        for idx, start in enumerate(chapter_indices):
            end = (
                chapter_indices[idx + 1]
                if idx + 1 < len(chapter_indices)
                else len(paragraphs)
            )
            title = paragraphs[start]["text"].strip()
            content_paras = [
                p for p in paragraphs[start + 1: end] if p["text"].strip()
            ]
            chapters.append({
                "title": title,
                "chapter_order": idx,
                "paragraphs": content_paras,
            })

        return chapters

    # ══════════════════════════════════════════════════
    # 11. 输出构建
    # ══════════════════════════════════════════════════

    @staticmethod
    def _build_output(chapters: list[dict]) -> list[dict]:
        """
        转换为最终输出格式，与原有接口兼容。

        输出:
            [{
                "title": str,
                "chapter_order": int,
                "paragraphs": [{
                    "text": str,
                    "html": str,
                    "paragraph_order": int,
                    "page_number": int,
                    "page_end": int,
                    "bbox": str,
                    "confidence": float,
                }]
            }]
        """
        output = []
        for ch in chapters:
            chapter_paras = []
            for order, para in enumerate(ch.get("paragraphs", [])):
                chapter_paras.append({
                    "text": para["text"],
                    "html": para["html"],
                    "paragraph_order": order,
                    "page_number": para["page_number"],
                    "page_end": para["page_end"],
                    "bbox": para["bbox"],
                    "confidence": para["confidence"],
                })
            output.append({
                "title": ch["title"],
                "chapter_order": ch["chapter_order"],
                "paragraphs": chapter_paras,
            })
        return output
