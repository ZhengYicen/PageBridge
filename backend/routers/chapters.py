from fastapi import APIRouter, Depends, HTTPException

from backend.auth import current_user, require_chapter_owner
from backend.database import get_connection, rows_to_list
from backend.worker import job_manager

router = APIRouter(prefix="/api", tags=["chapters"])


@router.get("/chapters/{chapter_id}/paragraphs")
async def get_paragraphs(chapter_id: str, user: dict = Depends(current_user)):
    conn = get_connection()
    try:
        chapter = require_chapter_owner(conn, chapter_id, user)
        paragraphs = rows_to_list(conn.execute(
            "SELECT * FROM paragraphs WHERE chapter_id=? ORDER BY paragraph_order",
            (chapter_id,),
        ).fetchall())
    finally:
        conn.close()
    return {"chapter": chapter, "paragraphs": paragraphs}


@router.post("/chapters/{chapter_id}/translate")
async def translate_chapter(chapter_id: str, user: dict = Depends(current_user)):
    """开始翻译章节。"""
    conn = get_connection()
    try:
        chapter = require_chapter_owner(conn, chapter_id, user)
    finally:
        conn.close()

    if chapter["translate_status"] == "completed":
        raise HTTPException(400, "该章节已经翻译完成")

    try:
        job_id = await job_manager.start_translate(chapter_id, owner_id=user["id"])
        return {"job_id": job_id, "chapter_id": chapter_id, "status": "queued"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
