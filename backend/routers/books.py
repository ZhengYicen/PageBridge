"""书籍和章节路由"""

import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.database import get_connection, row_to_dict, rows_to_list, source_hash
from backend.parsers import get_parser
from backend.config import STORAGE_DIR

logger = logging.getLogger("ai-reader.parse")

router = APIRouter(prefix="/api", tags=["books"])


@router.get("/books")
async def list_books():
    """获取所有书籍"""
    conn = get_connection()
    rows = rows_to_list(conn.execute(
        "SELECT * FROM books ORDER BY created_at DESC"
    ).fetchall())
    conn.close()
    return {"books": rows}


@router.get("/books/{book_id}")
async def get_book(book_id: str):
    """获取书籍详情（含章节列表）"""
    conn = get_connection()
    book = row_to_dict(conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    if not book:
        conn.close()
        raise HTTPException(404, "书籍不存在")

    chapters = rows_to_list(conn.execute(
        "SELECT * FROM chapters WHERE book_id=? ORDER BY chapter_order",
        (book_id,),
    ).fetchall())
    conn.close()

    book["chapters"] = chapters
    return book


@router.post("/books/{book_id}/parse")
async def parse_book(book_id: str):
    """解析书籍：提取章节和段落（重新解析时清理旧数据）"""
    conn = get_connection()
    book = row_to_dict(conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    conn.close()

    if not book:
        raise HTTPException(404, "书籍不存在")

    file_path = book["file_path"]
    if not Path(file_path).exists():
        raise HTTPException(400, "原文件不存在")

    # ── 清理旧数据 ────────────────────────────────────
    conn = get_connection()
    # 获取旧章节 ID 列表，用于清理翻译缓存
    old_chapters = conn.execute(
        "SELECT id FROM chapters WHERE book_id=?", (book_id,)
    ).fetchall()
    old_chapter_ids = [r["id"] for r in old_chapters]

    if old_chapter_ids:
        # 删除旧段落的翻译缓存
        placeholders = ",".join("?" * len(old_chapter_ids))
        # 删除关联的 translations
        para_ids = conn.execute(
            f"SELECT id FROM paragraphs WHERE chapter_id IN ({placeholders})",
            old_chapter_ids,
        ).fetchall()
        if para_ids:
            pid_placeholders = ",".join("?" * len(para_ids))
            pids = [r["id"] for r in para_ids]
            conn.execute(
                f"DELETE FROM translations WHERE paragraph_id IN ({pid_placeholders})",
                pids,
            )
        # 删除旧段落
        conn.execute(
            f"DELETE FROM paragraphs WHERE chapter_id IN ({placeholders})",
            old_chapter_ids,
        )
        # 删除旧 job
        conn.execute(
            f"DELETE FROM jobs WHERE chapter_id IN ({placeholders})",
            old_chapter_ids,
        )
        # 删除旧章节
        conn.execute(
            f"DELETE FROM chapters WHERE id IN ({placeholders})",
            old_chapter_ids,
        )

    # ── 开始解析 ──────────────────────────────────────
    conn.execute("UPDATE books SET parse_status='parsing' WHERE id=?", (book_id,))
    conn.commit()
    conn.close()

    try:
        parser = get_parser(file_path)
        logger.info("📖 开始解析: %s", Path(file_path).name)
        # 传入 book_id 以支持图片提取
        chapters_data = parser.parse(file_path, book_id=book_id)
        logger.info("📖 解析完成，共 %d 章", len(chapters_data))

        conn = get_connection()
        chapter_count = 0
        paragraph_count = 0
        image_count = 0

        for ch in chapters_data:
            chapter_id = str(uuid.uuid4())
            title = ch["title"]
            paragraphs = ch.get("paragraphs", [])
            logger.info("  └─ 写入第 %d 章: %s (%d 段)", ch["chapter_order"] + 1, title, len(paragraphs))

            conn.execute(
                "INSERT INTO chapters (id, book_id, title, chapter_order, paragraph_count) VALUES (?,?,?,?,?)",
                (chapter_id, book_id, title, ch["chapter_order"], len(paragraphs)),
            )

            for para in paragraphs:
                para_id = str(uuid.uuid4())
                para_type = para.get("type", "paragraph")
                is_image = para_type == "image"

                conn.execute(
                    "INSERT INTO paragraphs "
                    "(id, chapter_id, paragraph_order, source_text, source_html, page_number, source_bbox, status) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        para_id,
                        chapter_id,
                        para.get("paragraph_order", 0),
                        para.get("text", ""),
                        para.get("html", ""),
                        para.get("page_number", 0),
                        para.get("bbox", ""),
                        "image" if is_image else "pending",
                    ),
                )

                if is_image:
                    image_count += 1
                else:
                    paragraph_count += 1

            chapter_count += 1

        # 更新书籍信息
        conn.execute(
            "UPDATE books SET parse_status='completed', total_chapters=? WHERE id=?",
            (chapter_count, book_id),
        )
        conn.commit()
        conn.close()

        logger.info("📖 写入完成: %d 章, %d 文本段, %d 图片", chapter_count, paragraph_count, image_count)

        return {
            "book_id": book_id,
            "chapters": chapter_count,
            "paragraphs": paragraph_count,
            "images": image_count,
            "status": "completed",
        }

    except Exception as e:
        conn = get_connection()
        conn.execute("UPDATE books SET parse_status='failed' WHERE id=?", (book_id,))
        conn.commit()
        conn.close()
        logger.error("解析失败: %s", e, exc_info=True)
        raise HTTPException(500, f"解析失败: {str(e)}")


@router.get("/books/{book_id}/assets/{asset_path:path}")
async def serve_book_asset(book_id: str, asset_path: str):
    """
    提供书籍的静态资源（图片等）。
    带路径穿越防护。
    """
    base_dir = (STORAGE_DIR / "books" / book_id / "assets").resolve()
    requested = (base_dir / asset_path).resolve()

    # 路径穿越防护：确保解析后的路径在 base_dir 内
    if not str(requested).startswith(str(base_dir)):
        raise HTTPException(403, "Forbidden")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(404, "资源不存在")

    return FileResponse(requested)
