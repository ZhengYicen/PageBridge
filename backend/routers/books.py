"""书籍和章节路由"""

import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.database import get_connection, row_to_dict, rows_to_list
from backend.parsers import get_parser

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
    """解析书籍：提取章节和段落"""
    conn = get_connection()
    book = row_to_dict(conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    conn.close()

    if not book:
        raise HTTPException(404, "书籍不存在")

    file_path = book["file_path"]
    if not Path(file_path).exists():
        raise HTTPException(400, "原文件不存在")

    # 标记解析中
    conn = get_connection()
    conn.execute("UPDATE books SET parse_status='parsing' WHERE id=?", (book_id,))
    conn.commit()
    conn.close()

    try:
        parser = get_parser(file_path)
        logger.info("📖 开始解析: %s", Path(file_path).name)
        chapters_data = parser.parse(file_path)
        logger.info("📖 解析完成，共 %d 章", len(chapters_data))

        conn = get_connection()
        chapter_count = 0
        paragraph_count = 0

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
                conn.execute(
                    "INSERT INTO paragraphs (id, chapter_id, paragraph_order, source_text, source_html, page_number, source_bbox) VALUES (?,?,?,?,?,?,?)",
                    (
                        para_id,
                        chapter_id,
                        para.get("paragraph_order", 0),
                        para.get("text", ""),
                        para.get("html", ""),
                        para.get("page_number", 0),
                        para.get("bbox", ""),
                    ),
                )
                paragraph_count += 1

            chapter_count += 1

        # 更新书籍信息
        conn.execute(
            "UPDATE books SET parse_status='completed', total_chapters=? WHERE id=?",
            (chapter_count, book_id),
        )
        conn.commit()
        conn.close()

        return {
            "book_id": book_id,
            "chapters": chapter_count,
            "paragraphs": paragraph_count,
            "status": "completed",
        }

    except Exception as e:
        conn = get_connection()
        conn.execute("UPDATE books SET parse_status='failed' WHERE id=?", (book_id,))
        conn.commit()
        conn.close()
        raise HTTPException(500, f"解析失败: {str(e)}")
