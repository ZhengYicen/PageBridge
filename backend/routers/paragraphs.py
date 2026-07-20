"""段落路由 — 阅读页段落接口 + 翻译状态轮询"""

import json

from fastapi import APIRouter, HTTPException, Query

from backend.database import get_connection, row_to_dict, rows_to_list

router = APIRouter(prefix="/api", tags=["paragraphs"])


@router.get("/paragraphs/{chapter_id}/translations")
async def get_paragraph_translations(chapter_id: str):
    """
    获取章节所有段落的翻译状态（轮询用）。
    返回每个段落的 id、translation、status，前端渐进式显示。
    """
    conn = get_connection()
    chapter = row_to_dict(conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
    if not chapter:
        conn.close()
        raise HTTPException(404, "章节不存在")

    paragraphs = rows_to_list(conn.execute(
        "SELECT id, paragraph_order, source_text, source_html, "
        "       translation, status, error_message, updated_at "
        "FROM paragraphs WHERE chapter_id=? ORDER BY paragraph_order",
        (chapter_id,),
    ).fetchall())
    conn.close()

    return {
        "chapter_id": chapter_id,
        "chapter_title": chapter["title"],
        "translate_status": chapter["translate_status"],
        "total": len(paragraphs),
        "completed": sum(1 for p in paragraphs if p["status"] == "completed"),
        "paragraphs": paragraphs,
    }


@router.get("/books/{book_id}/sections/{section_id}/paragraphs")
async def get_section_paragraphs(
    book_id: str,
    section_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    获取某个 section 的段落（分页），含 source_fragments。

    section_id 可以是章节 ID，或者是虚拟 section ID。
    前端通过 /books/{book_id}/read 获取 sections 列表。
    """
    conn = get_connection()

    # 处理虚拟 "全文" section
    if section_id == "__full__":
        # 获取本书所有段落（串联所有章节）
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM paragraphs p "
            "JOIN chapters c ON p.chapter_id=c.id WHERE c.book_id=?",
            (book_id,),
        ).fetchone()["cnt"]

        paras = rows_to_list(conn.execute(
            "SELECT p.id, p.paragraph_order, p.source_text, p.source_html, "
            "p.translation, p.status, p.error_message, p.page_number, "
            "p.page_start, p.page_end, p.updated_at "
            "FROM paragraphs p "
            "JOIN chapters c ON p.chapter_id=c.id "
            "WHERE c.book_id=? "
            "ORDER BY c.chapter_order, p.paragraph_order "
            "LIMIT ? OFFSET ?",
            (book_id, limit, offset),
        ).fetchall())
    else:
        # 验证 section 存在
        section = row_to_dict(conn.execute(
            "SELECT id, title FROM chapters WHERE id=? AND book_id=?",
            (section_id, book_id),
        ).fetchone())
        if not section:
            conn.close()
            raise HTTPException(404, "Section 不存在")

        # 获取总段落数
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM paragraphs WHERE chapter_id=?",
            (section_id,),
        ).fetchone()["cnt"]

        # 获取分页段落
        paras = rows_to_list(conn.execute(
            "SELECT id, paragraph_order, source_text, source_html, "
            "translation, status, error_message, page_number, "
            "page_start, page_end, updated_at "
            "FROM paragraphs WHERE chapter_id=? "
            "ORDER BY paragraph_order LIMIT ? OFFSET ?",
            (section_id, limit, offset),
        ).fetchall())

    # 为每个段落加载 source_fragments
    result_paras = []
    for p in paras:
        frags = rows_to_list(conn.execute(
            "SELECT pdf_page_index, pdf_page_number, bbox, bbox_normalized, "
            "original_page_width, original_page_height, fragment_order, "
            "source_text, confidence "
            "FROM paragraph_source_fragments "
            "WHERE paragraph_id=? ORDER BY fragment_order",
            (p["id"],),
        ).fetchall())
        result_paras.append({
            "id": p["id"],
            "paragraph_order": p["paragraph_order"],
            "source_text": p["source_text"],
            "source_html": p["source_html"] or "",
            "translation": p["translation"] or "",
            "status": p["status"],
            "error_message": p["error_message"] or "",
            "page_number": p["page_number"] or 0,
            "page_start": p["page_start"] or p["page_number"] or 0,
            "page_end": p["page_end"] or p["page_number"] or 0,
            "source_fragments": frags,
        })

    conn.close()

    return {
        "section_id": section_id,
        "paragraphs": result_paras,
        "total": total,
        "offset": offset,
        "limit": limit,
    }
