"""
段落迁移脚本：从 book_pages.lines_json 重新组装段落，生成包含 source_fragments
的新段落结构，并通过文本相似度匹配迁移已有翻译。

使用方法:
    python -m backend.scripts.migrate_paragraphs <book_id>

不重新运行 OCR，只重跑 assemble 流水线。
原有 translations 表记录和 paragraphs.translation 都会被尽力匹配迁移。
"""

import json
import logging
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from difflib import SequenceMatcher

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.database import get_connection, source_hash
from backend.parsers.pdf_parser import PdfParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate")

# 文本匹配阈值
EXACT_MATCH_THRESHOLD = 0.99
GOOD_MATCH_THRESHOLD = 0.75


def normalize_text(text: str) -> str:
    """标准化文本用于比较：去空格、去连字符。"""
    t = text.lower().strip()
    t = re.sub(r'\s+', ' ', t)
    t = t.replace('-', '')
    t = t.replace('‐', '').replace('‑', '')  # 连字符变体
    t = re.sub(r'[^\w\s]', '', t)
    return t.strip()


def text_similarity(a: str, b: str) -> float:
    """计算两个文本的相似度（基于最长公共子序列的 ratio）。"""
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def load_page_results(book_id: str) -> list[dict]:
    """从 book_pages 加载所有已完成页面的解析结果。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM book_pages WHERE book_id=? AND status='completed' "
            "ORDER BY page_number",
            (book_id,),
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            try:
                lines = json.loads(r.get("lines_json", "[]"))
            except json.JSONDecodeError:
                lines = []
            results.append({
                "page_number": r["page_number"],
                "width": r["width"],
                "height": r["height"],
                "parse_method": r["parse_method"],
                "lines": lines,
                "raw_text": r.get("raw_text", ""),
                "confidence": r.get("confidence", 0.0),
                "status": r.get("status", "completed"),
                "error_message": r.get("error_message", ""),
            })
        return results
    finally:
        conn.close()


def load_old_paragraphs(book_id: str) -> list[dict]:
    """加载旧段落及其翻译，按 page_number 索引。"""
    conn = get_connection()
    try:
        old_chapters = conn.execute(
            "SELECT id FROM chapters WHERE book_id=?", (book_id,)
        ).fetchall()
        old_chapter_ids = [r["id"] for r in old_chapters]

        if not old_chapter_ids:
            return []

        placeholders = ",".join("?" * len(old_chapter_ids))

        rows = conn.execute(
            f"SELECT id, chapter_id, paragraph_order, source_text, page_number, "
            f"translation, status, error_message "
            f"FROM paragraphs WHERE chapter_id IN ({placeholders}) "
            f"ORDER BY page_number, paragraph_order",
            old_chapter_ids,
        ).fetchall()

        paragraphs = []
        for r in rows:
            rd = dict(r)
            paragraphs.append(rd)

        logger.info("  加载 %d 个旧段落", len(paragraphs))
        return paragraphs
    finally:
        conn.close()


def match_translation(
    new_para: dict,
    old_paras_by_page: dict[int, list[dict]],
) -> tuple[str, str, float]:
    """
    为新段落匹配最佳旧段落的翻译。

    匹配策略：
    1. 精确文本匹配（normalize 后 ratio >= 0.99）
    2. 同页内最佳相似度匹配（>= 0.75）
    3. 跨页最佳相似度匹配（>= 0.75）

    返回 (translation, status, similarity)
    """
    new_text = new_para.get("text", "")
    if not new_text:
        return "", "pending", 0.0

    candidates = []

    # 收集候选段落：同页优先
    pg = new_para.get("page_number", 0)
    for page_num in range(max(1, pg - 1), pg + 2):
        old_list = old_paras_by_page.get(page_num, [])
        candidates.extend(old_list)

    # 如果候选太少，扩大搜索范围
    if len(candidates) < 5:
        for page_num in range(max(1, pg - 3), pg + 4):
            old_list = old_paras_by_page.get(page_num, [])
            for o in old_list:
                if o not in candidates:
                    candidates.append(o)

    if not candidates:
        return "", "pending", 0.0

    best_sim = 0.0
    best_match = None

    for old_para in candidates:
        old_text = old_para.get("source_text", "")
        if not old_text:
            continue
        sim = text_similarity(new_text, old_text)
        if sim > best_sim:
            best_sim = sim
            best_match = old_para

    if best_match is None or best_sim < GOOD_MATCH_THRESHOLD:
        return "", "pending", 0.0

    translation = best_match.get("translation", "") or ""
    if translation:
        return translation, "completed", best_sim
    else:
        return "", "pending", best_sim


def migrate_book(book_id: str):
    """为指定书籍重新组装段落并迁移翻译。"""
    logger.info("=" * 60)
    logger.info("开始迁移书籍: %s", book_id)

    # 1. 加载旧段落（含翻译）
    logger.info("[1/5] 加载旧段落和翻译...")
    old_paragraphs = load_old_paragraphs(book_id)

    # 按 page_number 索引
    old_by_page: dict[int, list[dict]] = defaultdict(list)
    total_with_translation = 0
    for p in old_paragraphs:
        pg = p.get("page_number", 0)
        old_by_page[pg].append(p)
        if p.get("translation"):
            total_with_translation += 1

    logger.info("  其中 %d 个段落有翻译", total_with_translation)

    # 2. 加载页面数据
    logger.info("[2/5] 从 book_pages 加载页面数据...")
    all_page_data = load_page_results(book_id)
    logger.info("  已加载 %d 页", len(all_page_data))

    if not all_page_data:
        logger.error("没有页面数据")
        return

    # 3. 重新组装
    logger.info("[3/5] 重新组装 paragraph + source_fragments...")
    parser = PdfParser()
    chapters_data = parser.assemble(all_page_data)

    if not chapters_data:
        logger.error("组装失败")
        return

    total_paras = sum(len(ch["paragraphs"]) for ch in chapters_data)
    logger.info("  组装完成: %d 章, %d 段", len(chapters_data), total_paras)

    # 4. 翻译匹配
    logger.info("[4/5] 翻译匹配...")
    match_stats = {"exact": 0, "good": 0, "unmatched": 0}
    translation_errors = []

    for ch_data in chapters_data:
        for para in ch_data.get("paragraphs", []):
            translation, status, sim = match_translation(para, old_by_page)
            para["_translation"] = translation
            para["_status"] = status
            para["_similarity"] = sim

            if status == "completed" and sim >= EXACT_MATCH_THRESHOLD:
                match_stats["exact"] += 1
            elif status == "completed":
                match_stats["good"] += 1
            else:
                match_stats["unmatched"] += 1

    logger.info("  匹配结果: exact=%d  good=%d  unmatched=%d",
                match_stats["exact"], match_stats["good"], match_stats["unmatched"])

    # 5. 写数据库
    logger.info("[5/5] 写入数据库...")
    conn = get_connection()

    # 清理旧数据（保留 book_pages）
    _clean_book_parse_data(conn, book_id)

    chapter_count = 0
    paragraph_count = 0
    written_with_translation = 0

    for ch_data in chapters_data:
        chapter_id = str(uuid.uuid4())
        title = ch_data["title"]
        paragraphs = ch_data.get("paragraphs", [])

        conn.execute(
            "INSERT INTO chapters (id, book_id, title, chapter_order, paragraph_count) "
            "VALUES (?,?,?,?,?)",
            (chapter_id, book_id, title, ch_data["chapter_order"], len(paragraphs)),
        )

        for para in paragraphs:
            para_id = str(uuid.uuid4())
            para_text = para.get("text", "")
            translation = para.get("_translation", "")
            para_status = para.get("_status", "pending")

            conn.execute(
                "INSERT INTO paragraphs "
                "(id, chapter_id, paragraph_order, source_text, source_html, "
                "page_number, page_start, page_end, source_bbox, status, translation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    para_id, chapter_id,
                    para.get("paragraph_order", 0),
                    para_text,
                    para.get("html", ""),
                    para.get("page_number", 0),
                    para.get("page_number", 0),
                    para.get("page_end", para.get("page_number", 0)),
                    "",
                    para_status,
                    translation,
                ),
            )

            if translation:
                written_with_translation += 1

            # 保存 source_fragments
            fragments = para.get("source_fragments", [])
            for frag in fragments:
                conn.execute(
                    "INSERT INTO paragraph_source_fragments "
                    "(paragraph_id, pdf_page_index, pdf_page_number, bbox, bbox_normalized, "
                    "original_page_width, original_page_height, fragment_order, source_text, "
                    "confidence, parse_method) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        para_id,
                        frag.get("pdf_page_index", 0),
                        frag.get("pdf_page_number", 0),
                        frag.get("bbox", "{}"),
                        frag.get("bbox_normalized", "{}"),
                        frag.get("original_page_width", 0),
                        frag.get("original_page_height", 0),
                        frag.get("fragment_order", 0),
                        frag.get("source_text", ""),
                        frag.get("confidence", 0.0),
                        frag.get("parse_method", ""),
                    ),
                )

            paragraph_count += 1
        chapter_count += 1

    # 更新书籍状态
    conn.execute(
        "UPDATE books SET parse_status='completed', current_stage='completed', "
        "total_chapters=? WHERE id=?",
        (chapter_count, book_id),
    )
    conn.commit()
    conn.close()

    logger.info("=" * 60)
    logger.info("迁移完成!")
    logger.info("  章节: %d", chapter_count)
    logger.info("  段落: %d", paragraph_count)
    logger.info("  翻译迁移: %d / %d (%.1f%%)",
                written_with_translation, paragraph_count,
                written_with_translation / paragraph_count * 100 if paragraph_count > 0 else 0)
    logger.info("  未匹配: %d", match_stats["unmatched"])
    logger.info("=" * 60)


def _clean_book_parse_data(conn, book_id: str):
    """清理书籍的解析数据（不删除 book_pages）。"""
    old_chapters = conn.execute(
        "SELECT id FROM chapters WHERE book_id=?", (book_id,)
    ).fetchall()
    old_chapter_ids = [r["id"] for r in old_chapters]

    if old_chapter_ids:
        placeholders = ",".join("?" * len(old_chapter_ids))
        para_ids = conn.execute(
            f"SELECT id FROM paragraphs WHERE chapter_id IN ({placeholders})",
            old_chapter_ids,
        ).fetchall()
        if para_ids:
            pid_placeholders = ",".join("?" * len(para_ids))
            pids = [r["id"] for r in para_ids]
            conn.execute(
                f"DELETE FROM paragraph_source_fragments WHERE paragraph_id IN ({pid_placeholders})", pids
            )
            conn.execute(
                f"DELETE FROM translations WHERE paragraph_id IN ({pid_placeholders})", pids
            )
        conn.execute(
            f"DELETE FROM paragraphs WHERE chapter_id IN ({placeholders})", old_chapter_ids
        )
        conn.execute(
            f"DELETE FROM jobs WHERE chapter_id IN ({placeholders})", old_chapter_ids
        )
        conn.execute(
            f"DELETE FROM chapters WHERE id IN ({placeholders})", old_chapter_ids
        )
    conn.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("用法: python -m backend.scripts.migrate_paragraphs <book_id>")
        sys.exit(1)

    book_id = sys.argv[1]
    start = time.time()
    migrate_book(book_id)
    elapsed = time.time() - start
    logger.info("总耗时: %.1f 秒", elapsed)
