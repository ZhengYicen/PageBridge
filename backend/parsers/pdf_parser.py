"""PDF 解析器 — 使用 RapidOCR 识别扫描版/文字版 PDF，输出连续正文"""

from __future__ import annotations

import gc
import html as html_mod
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import fitz  # PyMuPDF — 文字层检测 + 页面渲染
import numpy as np
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger("ai-reader.parse")

# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

_DEFAULT_DPI = 200
_RETRY_DPI = 260
_PDF_POINTS_PER_INCH = 72

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

_PUNCTUATION_ONLY_RE = re.compile(
    r"^[\s\-–—•· ﻿.,;:!?\"'‘'""()[\]{}<>«»‥…・《》、，。；：？！【】「」『』〔〕]+$",
    re.UNICODE,
)

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

# ── Unicode 范围 ────────────────────────────────────────
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK 统一汉字
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x3040, 0x30FF),   # 日文假名
    (0xAC00, 0xD7AF),   # 韩文
]


def _is_meaningful_char(c: str) -> bool:
    """判断字符是否为有意义的可读字符（非空格、非乱码）。"""
    if c.isalnum():
        return True
    for lo, hi in _CJK_RANGES:
        if lo <= ord(c) <= hi:
            return True
    return False


# ═══════════════════════════════════════════════════════════
# PdfParser
# ═══════════════════════════════════════════════════════════

class PdfParser:
    """
    PDF 解析器 — 使用 RapidOCR 进行 OCR 识别，PyMuPDF 用于文字层检测和页面渲染。

    逐页解析流程:
        _try_native_text()  →  如果可靠则直接用
        ↓ 否则
        渲染 → OCR → 标准化 → 排序 → 同行合并
        → 如果质量差则用更高中 DPI 重试 → 保留更好结果

    整书组装流程:
        _clean_header_footer → _fix_hyphenation → _reconstruct_paragraphs
        → _merge_cross_page_paragraphs → _detect_chapters → _build_output
    """

    def __init__(self, **kwargs):
        self._ocr_engine: Optional[RapidOCR] = None

    # ═══════════════════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════════════════

    def parse(self, file_path: str, **kwargs) -> list[dict]:
        """
        兼容旧接口：同步解析整本 PDF。

        注意: 新代码应优先使用 parse_single_page() + assemble() 组合，
        以支持逐页保存、进度推送和内存控制。
        """
        doc = fitz.open(file_path)
        try:
            all_pages = []
            for page_num in range(len(doc)):
                result = self.parse_single_page(doc[page_num], page_num + 1, doc=doc)
                all_pages.append(result)
        finally:
            doc.close()

        gc.collect()
        return self.assemble(all_pages)

    def parse_single_page(
        self,
        page: fitz.Page,
        page_number: int,
        doc: fitz.Document | None = None,
    ) -> dict:
        """
        解析单页 PDF，返回统一的逐页结果。

        优先尝试文字层提取，不满足条件时使用 RapidOCR。
        如果 200 DPI 质量差，自动用 260 DPI 重试并保留更好结果。

        所有坐标统一为 200 DPI 基准。

        返回:
            {
                "page_number": int,
                "width": float,
                "height": float,
                "parse_method": "native_text" | "rapidocr",
                "lines": [LineBlock, ...],
                "raw_text": str,
                "confidence": float,
                "status": "completed" | "failed",
                "error_message": str,
            }
        """
        base_info = {
            "page_number": page_number,
            "width": page.rect.width * _DEFAULT_DPI / _PDF_POINTS_PER_INCH,
            "height": page.rect.height * _DEFAULT_DPI / _PDF_POINTS_PER_INCH,
        }

        # ── 文字层优先 ───────────────────────────────────
        native_result = self._try_native_text(page, base_info)
        if native_result is not None:
            return native_result

        # ── RapidOCR 200 DPI ─────────────────────────────
        result_200 = self._ocr_page_at_dpi(page, page_number, _DEFAULT_DPI)
        if result_200 is None:
            return {**base_info, "parse_method": "rapidocr", "lines": [],
                    "raw_text": "", "confidence": 0.0,
                    "status": "failed", "error_message": "OCR 无结果"}

        # ── 判断是否需要 260 DPI 重试 ───────────────────
        if self._needs_retry(result_200):
            logger.info(
                "[PDF] 第 %d 页 200DPI 质量不足 (conf=%.3f, meaningful=%.3f)，尝试 260DPI",
                page_number,
                result_200["confidence"],
                self._meaningful_ratio(result_200["raw_text"]),
            )
            result_260 = self._ocr_page_at_dpi(page, page_number, _RETRY_DPI)
            if result_260 is not None:
                score_200 = self._quality_score(result_200)
                score_260 = self._quality_score(result_260)
                logger.info(
                    "[PDF] 第 %d 页 quality: 200DPI=%.3f  260DPI=%.3f",
                    page_number, score_200, score_260,
                )
                if score_260 > score_200:
                    # 将 260 DPI 坐标缩放回 200 DPI 基准
                    result_260["lines"] = self._scale_lines(
                        result_260["lines"], _DEFAULT_DPI / _RETRY_DPI
                    )
                    return result_260

        return result_200

    def assemble(self, all_pages: list[dict]) -> list[dict]:
        """
        组装：从逐页结果重建全书章节。

        all_pages: parse_single_page() 返回的列表，按页码排序。

        返回与旧接口兼容的 list[dict]。
        """
        # 只取 status=completed 的页
        valid_pages = [p for p in all_pages if p.get("status") == "completed" and p.get("lines")]
        if not valid_pages:
            logger.warning("[PDF] 无有效页面可组装")
            return []

        # 重建 page_dims
        self._page_dims: dict[int, tuple[int, int]] = {}
        for p in all_pages:
            self._page_dims[p["page_number"]] = (int(p["width"]), int(p["height"]))

        # 收集所有行
        all_lines = []
        for p in valid_pages:
            for line in p["lines"]:
                all_lines.append(line)

        if not all_lines:
            return []

        # 运行后处理管道
        lines = self._clean_header_footer(all_lines)
        lines = self._fix_hyphenation(lines)
        paragraphs = self._reconstruct_paragraphs(lines)
        paragraphs = self._merge_cross_page_paragraphs(paragraphs)
        chapters = self._detect_chapters(paragraphs)
        return self._build_output(chapters)

    # ═══════════════════════════════════════════════════════
    # 文字层提取
    # ═══════════════════════════════════════════════════════

    def _try_native_text(self, page: fitz.Page, base_info: dict) -> dict | None:
        """
        尝试从 PDF 文字层提取。如果文字质量可靠则返回结果，否则返回 None。
        """
        try:
            text = page.get_text("text").strip()
        except Exception:
            return None

        if not text:
            return None

        non_space = [c for c in text if not c.isspace()]
        non_space_count = len(non_space)
        meaningful_count = sum(1 for c in non_space if _is_meaningful_char(c))
        meaningful_ratio = meaningful_count / max(non_space_count, 1)
        replacement_ratio = text.count("�") / max(non_space_count, 1)  # U+FFFD

        # ── 普通页面 ──────────────────────────────────────
        if non_space_count >= 40:
            if meaningful_ratio >= 0.55 and replacement_ratio <= 0.02:
                blocks = page.get_text("dict")["blocks"]
                lines = self._extract_native_lines(
                    blocks, base_info["page_number"],
                    base_info["width"], base_info["height"],
                )
                if lines:
                    raw_text = " ".join(l["text"] for l in lines)
                    return {
                        **base_info,
                        "parse_method": "native_text",
                        "lines": lines,
                        "raw_text": raw_text,
                        "confidence": 1.0,
                        "status": "completed",
                        "error_message": "",
                    }

        # ── 短文本页面（标题页等）────────────────────────
        if 10 <= non_space_count < 40:
            if meaningful_ratio >= 0.8 and replacement_ratio == 0:
                blocks = page.get_text("dict")["blocks"]
                lines = self._extract_native_lines(
                    blocks, base_info["page_number"],
                    base_info["width"], base_info["height"],
                )
                if lines:
                    raw_text = " ".join(l["text"] for l in lines)
                    return {
                        **base_info,
                        "parse_method": "native_text",
                        "lines": lines,
                        "raw_text": raw_text,
                        "confidence": 1.0,
                        "status": "completed",
                        "error_message": "",
                    }

        return None

    def _extract_native_lines(
        self,
        blocks: list[dict],
        page_number: int,
        page_w: float,
        page_h: float,
    ) -> list[dict]:
        """
        从 PyMuPDF text blocks 提取行数据，坐标转换为 200 DPI 基准。

        返回 LineBlock 列表。
        """
        scale = _DEFAULT_DPI / _PDF_POINTS_PER_INCH
        lines: list[dict] = []

        for block in blocks:
            if block.get("type") != 0:  # text block
                continue
            for line in block.get("lines", []):
                text_parts = []
                for span in line.get("spans", []):
                    text_parts.append(span.get("text", ""))

                text = "".join(text_parts).strip()
                if not text or len(text) < 2:
                    continue

                bbox = line["bbox"]
                scaled_bbox = {
                    "x1": bbox[0] * scale,
                    "y1": bbox[1] * scale,
                    "x2": bbox[2] * scale,
                    "y2": bbox[3] * scale,
                }

                lines.append({
                    "text": text,
                    "bbox": scaled_bbox,
                    "confidence": 1.0,
                    "page_number": page_number,
                })

        # 同行合并（有些 PDF 文字层的 span/line 可能也需要合并）
        return self._merge_lines_in_page(lines)

    # ═══════════════════════════════════════════════════════
    # OCR 单页
    # ═══════════════════════════════════════════════════════

    def _ocr_page_at_dpi(
        self,
        page: fitz.Page,
        page_number: int,
        dpi: int,
    ) -> dict | None:
        """
        以指定 DPI 渲染页面并 OCR，返回标准化 + 排序 + 同行合并后的结果。

        返回:
            {
                "page_number": int,
                "width": float,
                "height": float,
                "parse_method": "rapidocr",
                "lines": [LineBlock, ...],
                "raw_text": str,
                "confidence": float,
                "status": "completed",
                "error_message": str,
            }
            或 None（OCR 完全失败）。
        """
        scale = dpi / _PDF_POINTS_PER_INCH
        matrix = fitz.Matrix(scale, scale)

        try:
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )
        except Exception as exc:
            logger.warning("[PDF] 第 %d 页 %dDPI 渲染失败: %s", page_number, dpi, exc)
            return None
        finally:
            # 在 except 分支中 pix 可能未定义，安全起见
            pass

        try:
            result = self._run_ocr(img)
        except Exception as exc:
            logger.warning("[PDF] 第 %d 页 %dDPI OCR 失败: %s", page_number, dpi, exc)
            # Cleanup pixmap reference
            del img, pix
            return None
        finally:
            del img
            if 'pix' in dir():
                del pix

        if result is None:
            return None

        blocks = self._normalize_ocr_results(result, page_number)
        if not blocks:
            return {
                "page_number": page_number,
                "width": page.rect.width * _DEFAULT_DPI / _PDF_POINTS_PER_INCH,
                "height": page.rect.height * _DEFAULT_DPI / _PDF_POINTS_PER_INCH,
                "parse_method": "rapidocr",
                "lines": [],
                "raw_text": "",
                "confidence": 0.0,
                "status": "completed",
                "error_message": "",
            }

        blocks = self._sort_reading_order_for_page(blocks, page_number)
        lines = self._merge_lines_in_page(blocks)

        raw_text = " ".join(l["text"] for l in lines)
        confidence = self._weighted_confidence(lines)

        return {
            "page_number": page_number,
            "width": page.rect.width * _DEFAULT_DPI / _PDF_POINTS_PER_INCH,
            "height": page.rect.height * _DEFAULT_DPI / _PDF_POINTS_PER_INCH,
            "parse_method": "rapidocr",
            "lines": lines,
            "raw_text": raw_text,
            "confidence": confidence,
            "status": "completed",
            "error_message": "",
        }

    def _run_ocr(self, img: np.ndarray) -> list | None:
        """运行 RapidOCR，复用一个实例。"""
        if self._ocr_engine is None:
            logger.info("[PDF] 初始化 RapidOCR ...")
            self._ocr_engine = RapidOCR()
        try:
            result, _ = self._ocr_engine(img)
            return result
        except Exception:
            return None

    def _normalize_ocr_results(
        self, raw: list, page_number: int
    ) -> list[dict]:
        """同旧版 _normalize_ocr_results，但输入为单页数据。"""
        blocks = []
        for entry in raw:
            if len(entry) < 3:
                continue
            pts, text, conf = entry[0], str(entry[1]), float(entry[2])

            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]

            text = text.strip()
            if self._is_garbage(text):
                continue

            blocks.append({
                "bbox": {
                    "x1": min(xs),
                    "y1": min(ys),
                    "x2": max(xs),
                    "y2": max(ys),
                },
                "text": text,
                "confidence": conf,
                "page_number": page_number,
            })
        return blocks

    def _sort_reading_order_for_page(
        self, blocks: list[dict], page_number: int
    ) -> list[dict]:
        """单页内按阅读顺序排序（同旧版 _sort_page_blocks）。"""
        if len(blocks) <= 3:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        x_centers = [(b["bbox"]["x1"] + b["bbox"]["x2"]) / 2 for b in blocks]
        sorted_centers = sorted(x_centers)
        page_width = max(b["bbox"]["x2"] for b in blocks)

        if len(sorted_centers) < 2:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        gaps = [sorted_centers[i + 1] - sorted_centers[i] for i in range(len(sorted_centers) - 1)]
        max_gap = max(gaps)
        is_multi_column = max_gap > page_width * 0.15

        if not is_multi_column:
            return sorted(blocks, key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))

        max_gap_idx = gaps.index(max_gap)
        split_x = (sorted_centers[max_gap_idx] + sorted_centers[max_gap_idx + 1]) / 2

        left = [b for b in blocks if (b["bbox"]["x1"] + b["bbox"]["x2"]) / 2 < split_x]
        right = [b for b in blocks if (b["bbox"]["x1"] + b["bbox"]["x2"]) / 2 >= split_x]

        left.sort(key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))
        right.sort(key=lambda b: (b["bbox"]["y1"], b["bbox"]["x1"]))
        return left + right

    def _merge_lines_in_page(self, blocks: list[dict]) -> list[dict]:
        """单页内同行合并（同旧版 _merge_same_line_blocks 的单页逻辑）。"""
        if not blocks:
            return []

        heights = [b["bbox"]["y2"] - b["bbox"]["y1"] for b in blocks]
        median_height = float(np.median(heights)) if heights else 16.0
        y_tolerance = median_height * 0.4

        sorted_by_y = sorted(blocks, key=lambda b: b["bbox"]["y1"])
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

        lines = []
        page_num = blocks[0]["page_number"]
        for group in y_groups:
            if not group:
                continue
            group.sort(key=lambda b: b["bbox"]["x1"])

            texts = []
            total_conf = 0.0
            conf_len = 0
            merged_bbox = {
                "x1": min(b["bbox"]["x1"] for b in group),
                "y1": min(b["bbox"]["y1"] for b in group),
                "x2": max(b["bbox"]["x2"] for b in group),
                "y2": max(b["bbox"]["y2"] for b in group),
            }

            for i, block in enumerate(group):
                bt = block["text"]
                ch = self._estimate_char_width(block["bbox"]["y2"] - block["bbox"]["y1"])
                if i > 0:
                    prev = group[i - 1]
                    gap = block["bbox"]["x1"] - prev["bbox"]["x2"]
                    if self._needs_space(prev["text"], bt, gap, ch):
                        texts.append(" " + bt)
                    else:
                        texts.append(bt)
                else:
                    texts.append(bt)
                total_conf += block["confidence"] * len(bt)
                conf_len += len(bt)

            avg_conf = total_conf / conf_len if conf_len > 0 else 0.0
            lines.append({
                "text": "".join(texts),
                "bbox": merged_bbox,
                "confidence": round(avg_conf, 4),
                "page_number": page_num,
            })

        return lines

    # ═══════════════════════════════════════════════════════
    # 质量判断 & DPI 重试
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _meaningful_ratio(text: str) -> float:
        """有效字符占总非空字符的比例。"""
        non_space = [c for c in text if not c.isspace()]
        if not non_space:
            return 0.0
        meaningful = sum(1 for c in non_space if _is_meaningful_char(c))
        return meaningful / len(non_space)

    @staticmethod
    def _weighted_confidence(lines: list[dict]) -> float:
        """按文本长度加权的平均置信度。"""
        total_len = sum(len(l["text"]) for l in lines)
        if total_len == 0:
            return 0.0
        return sum(l["confidence"] * len(l["text"]) for l in lines) / total_len

    def _needs_retry(self, result: dict) -> bool:
        """
        判断 200 DPI 结果是否需要 260 DPI 重试。
        """
        lines = result.get("lines", [])
        raw_text = result.get("raw_text", "")

        non_space = [c for c in raw_text if not c.isspace()]
        non_space_count = len(non_space)

        # 完全没有识别到文字，且页面不像是空白页 → 重试
        if non_space_count == 0 and len(lines) == 0:
            return True

        if non_space_count == 0:
            return False

        meaningful = self._meaningful_ratio(raw_text)
        wc = self._weighted_confidence(lines)

        # meaningful_ratio < 0.5 → 乱码过多
        if meaningful < 0.5:
            return True

        # weighted_confidence < 0.65 → 整体识别质量低
        if wc < 0.65:
            return True

        # 有效文字很少且置信度也不高
        if non_space_count < 80 and wc < 0.75:
            return True

        return False

    def _quality_score(self, result: dict) -> float:
        """
        综合质量评分，用于比较 200 DPI 和 260 DPI 结果。

        分数越高越好。
        """
        lines = result.get("lines", [])
        raw_text = result.get("raw_text", "")

        wc = self._weighted_confidence(lines)    # 0~1
        mr = self._meaningful_ratio(raw_text)     # 0~1
        # 文本长度归一化（500 字符为满分）
        text_len_score = min(len(raw_text) / 500.0, 1.0)

        # 文字块计数加分（有一些 OCR 块说明至少检测到了文字）
        block_bonus = min(len(lines) / 30.0, 0.3) if len(lines) > 0 else 0

        return wc * 0.35 + mr * 0.30 + text_len_score * 0.25 + block_bonus

    @staticmethod
    def _scale_lines(lines: list[dict], scale: float) -> list[dict]:
        """将坐标按比例缩放。"""
        for line in lines:
            b = line["bbox"]
            b["x1"] *= scale
            b["y1"] *= scale
            b["x2"] *= scale
            b["y2"] *= scale
        return lines

    # ═══════════════════════════════════════════════════════
    # 以下方法与原版保持完全一致
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _is_garbage(text: str) -> bool:
        if not text or not text.strip():
            return True
        if _PUNCTUATION_ONLY_RE.match(text):
            return True
        return False

    @staticmethod
    def _estimate_char_width(bbox_height: float) -> float:
        return max(bbox_height * 0.5, 4.0)

    @staticmethod
    def _needs_space(prev_text: str, next_text: str, gap: float, char_width: float) -> bool:
        if not prev_text or not next_text:
            return False
        if prev_text.endswith("-"):
            return False
        if next_text[0] in ",.:;!?)]}%>":
            return False
        if gap < char_width * 0.5:
            return False
        return gap > char_width * 0.3

    # ── 6. 页眉 / 页脚 / 页码清理 ────────────────────────

    def _clean_header_footer(self, lines: list[dict]) -> list[dict]:
        """跨页统计删除页眉、页脚和页码。（原版逻辑不变）"""
        if len(lines) < 5:
            return lines

        by_page: dict[int, list[dict]] = defaultdict(list)
        for line in lines:
            by_page[line["page_number"]].append(line)

        page_nums = sorted(by_page)
        if len(page_nums) < 3:
            return lines

        pos_text_pages: dict[tuple, set[int]] = defaultdict(set)

        for pn in page_nums:
            page_lines = by_page[pn]
            h = self._page_dims.get(pn, (None, 1))[1] or 1

            for line in page_lines:
                y_center = (line["bbox"]["y1"] + line["bbox"]["y2"]) / 2
                rel_y = round(y_center / h, 1)
                if rel_y > 0.15 and rel_y < 0.88:
                    continue
                norm_text = line["text"].strip().lower()
                if not norm_text:
                    continue
                pos_text_pages[(rel_y, norm_text)].add(pn)

        total_pages = len(page_nums)
        min_pages = max(2, int(total_pages * 0.45)) if total_pages <= 5 else 3
        to_remove: set[int] = set()

        for (rel_y, norm_text), pages_set in pos_text_pages.items():
            if len(pages_set) >= min_pages and len(pages_set) / total_pages >= 0.40:
                for idx, line in enumerate(lines):
                    ln = line["page_number"]
                    ly = (line["bbox"]["y1"] + line["bbox"]["y2"]) / 2
                    h_line = self._page_dims.get(ln, (None, 1))[1] or 1
                    if (round(ly / h_line, 1) == rel_y
                            and line["text"].strip().lower() == norm_text):
                        to_remove.add(idx)

        for idx, line in enumerate(lines):
            pn = line["page_number"]
            h = self._page_dims.get(pn, (None, 1))[1] or 1
            rel_y = round(((line["bbox"]["y1"] + line["bbox"]["y2"]) / 2) / h, 1)
            text_stripped = line["text"].strip()
            if text_stripped.isdigit() and (rel_y < 0.10 or rel_y > 0.88):
                if len(text_stripped) <= 5:
                    to_remove.add(idx)

        cleaned = [line for idx, line in enumerate(lines) if idx not in to_remove]
        if len(lines) - len(cleaned):
            logger.info("[PDF] 移除 %d 个页眉/页脚/页码", len(lines) - len(cleaned))
        return cleaned

    # ── 7. 行尾断词恢复 ──────────────────────────────────

    def _fix_hyphenation(self, lines: list[dict]) -> list[dict]:
        if len(lines) < 2:
            return lines
        result = list(lines)
        i = 0
        merges = 0
        while i < len(result) - 1:
            cur, nxt = result[i], result[i + 1]
            ct = cur["text"].rstrip()
            nt = nxt["text"].lstrip()
            if (ct.endswith("-") and nt and nt[0].islower() and len(nt) >= 2
                    and (nxt["page_number"] == cur["page_number"]
                         or nxt["page_number"] == cur["page_number"] + 1)):
                p1, p2 = ct[:-1].strip(), nt
                if self._should_merge_hyphen(p1, p2):
                    result[i]["text"] = p1 + p2
                    result[i]["bbox"]["x2"] = max(result[i]["bbox"]["x2"], nxt["bbox"]["x2"])
                    result[i]["bbox"]["y2"] = max(result[i]["bbox"]["y2"], nxt["bbox"]["y2"])
                    l1, l2 = len(p1), len(p2)
                    if l1 + l2 > 0:
                        result[i]["confidence"] = round(
                            (result[i]["confidence"] * l1 + nxt["confidence"] * l2) / (l1 + l2), 4)
                    del result[i + 1]
                    merges += 1
                    continue
            i += 1
        if merges:
            logger.info("[PDF] 恢复 %d 处行尾断词", merges)
        return result

    @staticmethod
    def _should_merge_hyphen(part1: str, part2: str) -> bool:
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

    # ── 8. 自然段重建 ────────────────────────────────────

    def _reconstruct_paragraphs(self, lines: list[dict]) -> list[dict]:
        if not lines:
            return []
        paragraphs = []
        current_lines = [lines[0]]
        for i in range(1, len(lines)):
            if self._is_paragraph_break(lines[i - 1], lines[i], lines):
                paragraphs.append(current_lines)
                current_lines = [lines[i]]
            else:
                current_lines.append(lines[i])
        if current_lines:
            paragraphs.append(current_lines)
        result = [self._merge_para_lines(pl) for pl in paragraphs]
        logger.info("[PDF] 段落重建: %d 段", len(result))
        return result

    def _is_paragraph_break(self, prev: dict, curr: dict, all_lines: list[dict]) -> bool:
        pn = curr["page_number"]
        page_w = self._page_dims.get(pn, (None, None))[0]
        prev_h = prev["bbox"]["y2"] - prev["bbox"]["y1"]
        curr_h = curr["bbox"]["y2"] - curr["bbox"]["y1"]
        gap = curr["bbox"]["y1"] - prev["bbox"]["y2"]
        pt = prev["text"].strip()
        ct = curr["text"].strip()

        if not ct:
            return True
        if prev_h > 0 and gap > prev_h * 1.5:
            return True
        page_x1s = [l["bbox"]["x1"] for l in all_lines if l["page_number"] == pn]
        normal_left = float(np.median(page_x1s)) if page_x1s else curr["bbox"]["x1"]
        if curr["bbox"]["x1"] - normal_left > 15:
            return True
        if prev_h > 0 and curr_h > 0:
            if abs(curr["bbox"]["x1"] - prev["bbox"]["x1"]) > max(prev_h, curr_h) * 2:
                return True
        if page_w and page_w > 0:
            if (prev["bbox"]["x2"] - prev["bbox"]["x1"]) < page_w * 0.55 and pt.endswith((".", "!", "?")):
                return True
        if prev_h > 0 and curr_h > 0:
            if max(prev_h, curr_h) / min(prev_h, curr_h) > 1.30:
                return True
        if self._is_centered_text(curr, page_w):
            return True
        return False

    @staticmethod
    def _is_centered_text(line: dict, page_w: float | None) -> bool:
        if page_w is None or page_w <= 0:
            return False
        lcx = (line["bbox"]["x1"] + line["bbox"]["x2"]) / 2
        lw = line["bbox"]["x2"] - line["bbox"]["x1"]
        if (abs(lcx - page_w / 2) < page_w * 0.08
                and line["bbox"]["x1"] > page_w * 0.15
                and lw < page_w * 0.55):
            return True
        return False

    def _merge_para_lines(self, para_lines: list[dict]) -> dict:
        text_parts = []
        for i, line in enumerate(para_lines):
            t = line["text"].strip()
            if not t:
                continue
            text_parts.append((" " + t) if (i > 0 and text_parts) else t)
        merged = self._clean_text("".join(text_parts))

        bbox = {
            "x1": min(l["bbox"]["x1"] for l in para_lines),
            "y1": min(l["bbox"]["y1"] for l in para_lines),
            "x2": max(l["bbox"]["x2"] for l in para_lines),
            "y2": max(l["bbox"]["y2"] for l in para_lines),
        }
        pns = [l["page_number"] for l in para_lines]
        start, end = min(pns), max(pns)
        total_len = sum(len(l["text"]) for l in para_lines)
        avg_conf = (sum(l["confidence"] * len(l["text"]) for l in para_lines) / total_len
                    if total_len > 0 else 0.0)

        return {
            "text": merged,
            "html": f"<p>{html_mod.escape(merged)}</p>",
            "page_number": start,
            "page_end": end,
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
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r'\s+([,.:;!?)\]}>%])', r'\1', text)
        text = re.sub(r'([\[({¿¡])\s+', r'\1', text)
        return text.strip()

    # ── 9. 跨页段落合并 ──────────────────────────────────

    def _merge_cross_page_paragraphs(self, paragraphs: list[dict]) -> list[dict]:
        if len(paragraphs) < 2:
            return paragraphs
        result = list(paragraphs)
        i = 0
        merges = 0
        while i < len(result) - 1:
            cur, nxt = result[i], result[i + 1]
            if cur["page_end"] == nxt["page_number"] or cur["page_end"] + 1 == nxt["page_number"]:
                ct = cur["text"].rstrip()
                nt = nxt["text"].lstrip()
                if (ct and nt and ct[-1] not in ".!?"
                        and nt[0].islower()
                        and not nxt.get("is_centered", False)):
                    merged_text = ct + " " + nt
                    cur["text"] = merged_text
                    cur["html"] = f"<p>{html_mod.escape(merged_text)}</p>"
                    cur["page_end"] = nxt["page_end"]
                    try:
                        cb = json.loads(cur["bbox"])
                        nb = json.loads(nxt["bbox"])
                        cur["bbox"] = json.dumps({
                            "x1": min(cb["x1"], nb["x1"]),
                            "y1": min(cb["y1"], nb["y1"]),
                            "x2": max(cb["x2"], nb["x2"]),
                            "y2": max(cb["y2"], nb["y2"]),
                        }, ensure_ascii=False)
                    except (json.JSONDecodeError, KeyError):
                        pass
                    l1, l2 = len(ct), len(nt)
                    if l1 + l2 > 0:
                        cur["confidence"] = round(
                            (cur["confidence"] * l1 + nxt["confidence"] * l2) / (l1 + l2), 4)
                    del result[i + 1]
                    merges += 1
                    continue
            i += 1
        if merges:
            logger.info("[PDF] 合并 %d 处跨页段落", merges)
        return result

    # ── 10. 章节标题识别 ──────────────────────────────────

    def _detect_chapters(self, paragraphs: list[dict]) -> list[dict[str, Any]]:
        if not paragraphs:
            return [{"title": "全文", "chapter_order": 0, "paragraphs": []}]

        def _camel_split(t: str) -> str:
            return re.sub(r"([a-z])([A-Z][a-z])", r"\1 \2", t)

        chapter_indices = []
        for i, para in enumerate(paragraphs):
            text = para["text"].strip()
            if not text:
                continue
            is_match = any(pat.match(text) for pat in _CHAPTER_PATTERNS)
            if not is_match:
                st = _camel_split(text)
                if st != text:
                    is_match = any(pat.match(st) for pat in _CHAPTER_PATTERNS)
            if is_match:
                chapter_indices.append(i)
                continue
            if text[-1] in ".!?":
                continue
            is_centered = para.get("is_centered", False)
            is_short = len(text) < 80
            has_gap = False
            if i > 0:
                try:
                    prev_bottom = json.loads(paragraphs[i - 1]["bbox"]).get("y2", 0)
                    curr_top = json.loads(para["bbox"]).get("y1", 0)
                    if prev_bottom > 0 and (curr_top - prev_bottom) > 30:
                        has_gap = True
                except (json.JSONDecodeError, KeyError):
                    pass
            if sum([is_centered, is_short, has_gap]) >= 2 and is_short:
                chapter_indices.append(i)

        if not chapter_indices:
            return [{"title": "全文", "chapter_order": 0,
                     "paragraphs": [p for p in paragraphs if p["text"].strip()]}]

        chapters = []
        for idx, start in enumerate(chapter_indices):
            end = (chapter_indices[idx + 1] if idx + 1 < len(chapter_indices)
                   else len(paragraphs))
            chapters.append({
                "title": paragraphs[start]["text"].strip(),
                "chapter_order": idx,
                "paragraphs": [p for p in paragraphs[start + 1:end] if p["text"].strip()],
            })
        return chapters

    # ── 11. 输出构建 ──────────────────────────────────────

    @staticmethod
    def _build_output(chapters: list[dict]) -> list[dict]:
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
