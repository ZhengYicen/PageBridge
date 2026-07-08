"""章节和段落路由"""

from fastapi import APIRouter, HTTPException

from backend.database import get_connection, row_to_dict, rows_to_list
from backend.worker import job_manager

router = APIRouter(prefix="/api", tags=["chapters"])


@router.get("/chapters/{chapter_id}/paragraphs")
async def get_paragraphs(chapter_id: str):
    """获取章节的所有段落（原文 + 译文）"""
    conn = get_connection()
    chapter = row_to_dict(conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
    if not chapter:
        conn.close()
        raise HTTPException(404, "章节不存在")

    paragraphs = rows_to_list(conn.execute(
        "SELECT * FROM paragraphs WHERE chapter_id=? ORDER BY paragraph_order",
        (chapter_id,),
    ).fetchall())
    conn.close()

    return {"chapter": chapter, "paragraphs": paragraphs}


@router.post("/chapters/{chapter_id}/translate")
async def translate_chapter(chapter_id: str):
    """启动章节翻译（后台任务）"""
    conn = get_connection()
    chapter = row_to_dict(conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
    conn.close()

    if not chapter:
        raise HTTPException(404, "章节不存在")

    if chapter["translate_status"] == "completed":
        raise HTTPException(400, "该章节已经翻译完成")

    try:
        job_id = await job_manager.start_translate(chapter_id)
        return {"job_id": job_id, "chapter_id": chapter_id, "status": "started"}
    except ValueError as e:
        raise HTTPException(400, str(e))
