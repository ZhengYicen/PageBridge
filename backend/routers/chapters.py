from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth import current_user, require_chapter_owner
from backend.config import DAILY_TRANSLATION_CHARS, MONTHLY_TRANSLATION_CHARS
from backend.database import get_connection, rows_to_list
from backend.worker import job_manager

router = APIRouter(prefix="/api", tags=["chapters"])


class TranslateBody(BaseModel):
    confirmed: bool = Field(default=False, description="用户必须显式确认翻译")


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


@router.get("/chapters/{chapter_id}/translation-estimate")
async def translation_estimate(chapter_id: str, user: dict = Depends(current_user)):
    """估算翻译该章节所需的字符数，并返回当前用户额度使用情况。
    估算包含待翻译段落字符数 + 已 queued/running 任务的预留字符数。"""
    conn = get_connection()
    try:
        require_chapter_owner(conn, chapter_id, user)

        # 待翻译段落字符数（pending + failed）
        pending_chars = conn.execute(
            "SELECT COALESCE(SUM(length(source_text)),0) n FROM paragraphs "
            "WHERE chapter_id=? AND status IN ('pending','failed')",
            (chapter_id,),
        ).fetchone()["n"]

        # 用户额度使用情况（已完成的翻译）
        daily_used = conn.execute(
            "SELECT COALESCE(SUM(characters),0) n FROM translation_usage "
            "WHERE user_id=? AND created_at>=datetime('now','-1 day')",
            (user["id"],),
        ).fetchone()["n"]

        monthly_used = conn.execute(
            "SELECT COALESCE(SUM(characters),0) n FROM translation_usage "
            "WHERE user_id=? AND created_at>=datetime('now','start of month')",
            (user["id"],),
        ).fetchone()["n"]

        # 已经在 queued/running 任务中预留的字符数
        reserved = conn.execute(
            "SELECT COALESCE(SUM(reserved_characters),0) n FROM jobs "
            "WHERE owner_id=? AND job_type='translate' AND status IN ('queued','running')",
            (user["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()

    daily_remaining = DAILY_TRANSLATION_CHARS - daily_used - reserved
    monthly_remaining = MONTHLY_TRANSLATION_CHARS - monthly_used - reserved

    return {
        "characters": pending_chars,
        "daily_used": daily_used,
        "daily_limit": DAILY_TRANSLATION_CHARS,
        "daily_remaining": max(0, daily_remaining),
        "monthly_used": monthly_used,
        "monthly_limit": MONTHLY_TRANSLATION_CHARS,
        "monthly_remaining": max(0, monthly_remaining),
        "reserved_characters": reserved,
        "allowed": pending_chars > 0
                    and daily_remaining >= pending_chars
                    and monthly_remaining >= pending_chars,
    }


@router.post("/chapters/{chapter_id}/translate")
async def translate_chapter(
    chapter_id: str,
    body: TranslateBody = TranslateBody(),
    user: dict = Depends(current_user),
):
    """开始翻译章节。需要用户显式确认（confirmed=true）。"""
    if not body.confirmed:
        raise HTTPException(400, "请先确认字符估算后再翻译（confirmed=true）")

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


@router.post("/chapters/{chapter_id}/pre-translate")
async def pre_translate_chapter(chapter_id: str, user: dict = Depends(current_user)):
    """自动预翻译已关闭。请通过 translation-estimate + 手动确认翻译。"""
    conn = get_connection()
    try:
        require_chapter_owner(conn, chapter_id, user)
    finally:
        conn.close()
    raise HTTPException(410, "自动预翻译已关闭，请确认字符数后手动翻译")


@router.get("/translation/usage")
async def translation_usage(user: dict = Depends(current_user)):
    """当前用户的翻译额度使用情况。"""
    conn = get_connection()
    try:
        daily = conn.execute(
            "SELECT COALESCE(SUM(characters),0) n FROM translation_usage "
            "WHERE user_id=? AND created_at>=datetime('now','-1 day')",
            (user["id"],),
        ).fetchone()["n"]
        monthly = conn.execute(
            "SELECT COALESCE(SUM(characters),0) n FROM translation_usage "
            "WHERE user_id=? AND created_at>=datetime('now','start of month')",
            (user["id"],),
        ).fetchone()["n"]
        reserved = conn.execute(
            "SELECT COALESCE(SUM(reserved_characters),0) n FROM jobs "
            "WHERE owner_id=? AND job_type='translate' AND status IN ('queued','running')",
            (user["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()

    return {
        "daily_used": daily,
        "daily_limit": DAILY_TRANSLATION_CHARS,
        "monthly_used": monthly,
        "monthly_limit": MONTHLY_TRANSLATION_CHARS,
        "reserved_characters": reserved,
    }
