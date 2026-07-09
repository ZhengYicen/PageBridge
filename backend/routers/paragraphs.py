"""段落路由 — 翻译状态轮询"""

from fastapi import APIRouter, HTTPException

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
