"""PDF 解析器 — 优先调用 MinerU API，fallback 到 PyMuPDF"""

import json
import re
from pathlib import Path
from typing import Optional
import httpx
import fitz  # PyMuPDF

from backend.config import MINERU_URL


class PdfParser:
    """
    PDF 解析器
    - 优先调用 MinerU Docker 服务（处理扫描版/复杂排版）
    - 如果 MinerU 不可用，回退到 PyMuPDF 提取可复制文本
    """

    def __init__(self, mineru_url: str = MINERU_URL):
        self.mineru_url = mineru_url

    def parse(self, file_path: str) -> list[dict]:
        """
        解析 PDF，返回章节列表
        每章结构：{"title": str, "chapter_order": int, "paragraphs": list[dict]}
        每段结构：{"text": str, "html": str, "page_number": int, "bbox": str}
        """
        # 先尝试 MinerU
        try:
            result = self._parse_via_mineru(file_path)
            if result:
                return result
        except Exception as e:
            print(f"[PDF] MinerU 解析失败: {e}, fallback 到 PyMuPDF")

        # fallback
        return self._parse_via_pymupdf(file_path)

    # ── MinerU 方案 ──────────────────────────────────

    def _parse_via_mineru(self, file_path: str) -> Optional[list[dict]]:
        """调用 MinerU API 解析 PDF"""
        url = f"{self.mineru_url}/file_parse"
        with open(file_path, "rb") as f:
            resp = httpx.post(
                url,
                files={"files": f},
                data={
                    "return_md": "true",
                    "return_content_list": "true",
                    "return_middle_json": "false",
                },
                timeout=600,
            )
        resp.raise_for_status()
        data = resp.json()

        # MinerU 返回格式: {"results": {"filename.pdf": {"md_content": ..., "content_list": [...]}}}
        results = data.get("results", {})
        if not results:
            return None

        filename = Path(file_path).name
        file_result = results.get(filename) or list(results.values())[0]

        # 优先用 content_list（结构化），没有则用 md_content 解析
        content_list = file_result.get("content_list")
        if content_list:
            return self._content_list_to_chapters(content_list)

        md_content = file_result.get("md_content", "")
        if md_content:
            return self._md_to_chapters(md_content)

        return None

    def _content_list_to_chapters(self, content_list: list[dict]) -> list[dict]:
        """将 MinerU content_list 转为结构化章节"""
        chapters = []
        current_chapter = None
        order = 0

        for item in content_list:
            item_type = item.get("type", "")
            text = item.get("text", "")
            page = item.get("page_number", 0) or item.get("page_idx", 0)
            bbox = item.get("bbox") or item.get("poly") or ""

            if item_type == "title":
                # 遇到标题 → 新章节
                if current_chapter and current_chapter["paragraphs"]:
                    chapters.append(current_chapter)
                current_chapter = {
                    "title": text.strip(),
                    "chapter_order": order,
                    "paragraphs": [],
                }
                order += 1
            elif current_chapter is not None:
                if text.strip():
                    current_chapter["paragraphs"].append({
                        "text": text.strip(),
                        "html": f"<p>{text.strip()}</p>",
                        "page_number": page,
                        "bbox": str(bbox) if bbox else "",
                    })
            else:
                # 正文在第一个标题前出现 → 作为前言章节
                current_chapter = {
                    "title": "前言",
                    "chapter_order": 0,
                    "paragraphs": [],
                }
                order = 1
                if text.strip():
                    current_chapter["paragraphs"].append({
                        "text": text.strip(),
                        "html": f"<p>{text.strip()}</p>",
                        "page_number": page,
                        "bbox": str(bbox) if bbox else "",
                    })

        if current_chapter and current_chapter["paragraphs"]:
            chapters.append(current_chapter)

        return chapters

    def _md_to_chapters(self, md: str) -> list[dict]:
        """将 Markdown 文本按标题层级拆分成章节和段落"""
        chapters = []
        lines = md.split("\n")
        current_chapter = None
        order = 0

        for line in lines:
            stripped = line.strip()

            # 识别标题（## 或 #）
            title_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
            if title_match:
                if current_chapter and current_chapter["paragraphs"]:
                    chapters.append(current_chapter)
                current_chapter = {
                    "title": title_match.group(2).strip(),
                    "chapter_order": order,
                    "paragraphs": [],
                }
                order += 1
                continue

            if not stripped:
                continue

            if current_chapter is None:
                current_chapter = {
                    "title": "前言",
                    "chapter_order": 0,
                    "paragraphs": [],
                }
                order = 1

            if len(stripped) >= 5:
                current_chapter["paragraphs"].append({
                    "text": stripped,
                    "html": f"<p>{stripped}</p>",
                    "page_number": 0,
                    "bbox": "",
                })

        if current_chapter and current_chapter["paragraphs"]:
            chapters.append(current_chapter)

        return chapters

    # ── PyMuPDF fallback ─────────────────────────────

    def _parse_via_pymupdf(self, file_path: str) -> list[dict]:
        """PyMuPDF 兜底方案 — 提取可复制文本"""
        doc = fitz.open(file_path)
        chapters = []
        current_chapter = {
            "title": "全文",
            "chapter_order": 0,
            "paragraphs": [],
        }

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if block.get("type") != 0:  # 0=text, 1=image
                    continue

                text = ""
                bbox = block.get("bbox", ())
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text += span.get("text", "")

                text = text.strip()
                if not text or len(text) < 5:
                    continue

                # 启发式：大号粗体文字可能是标题
                first_span = block.get("lines", [{}])[0].get("spans", [{}])[0]
                font_size = first_span.get("size", 0)
                is_bold = "Bold" in first_span.get("font", "")

                if (is_bold and font_size > 14) or font_size > 16:
                    if current_chapter["paragraphs"]:
                        chapters.append(current_chapter)
                    current_chapter = {
                        "title": text[:60],
                        "chapter_order": len(chapters) + 1,
                        "paragraphs": [],
                    }
                else:
                    current_chapter["paragraphs"].append({
                        "text": text,
                        "html": f"<p>{text}</p>",
                        "page_number": page_num + 1,
                        "bbox": str(list(bbox)) if bbox else "",
                    })

        if current_chapter["paragraphs"]:
            chapters.append(current_chapter)

        doc.close()
        return chapters
