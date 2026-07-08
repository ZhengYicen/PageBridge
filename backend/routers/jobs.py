"""任务管理路由"""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.database import get_connection, row_to_dict
from backend.worker import job_manager

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """获取任务状态"""
    conn = get_connection()
    job = row_to_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    conn.close()

    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    """暂停任务"""
    ok = await job_manager.pause(job_id)
    if not ok:
        raise HTTPException(404, "任务不存在或不在运行中")
    return {"status": "paused"}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """继续任务"""
    ok = await job_manager.resume(job_id)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"status": "resumed"}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    """重试失败段落"""
    ok = await job_manager.retry_failed(job_id)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"status": "retrying"}


@router.get("/jobs/{job_id}/progress")
async def job_progress(job_id: str):
    """SSE 进度推送"""
    async def event_stream():
        while True:
            job = job_manager.get_status(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                break

            data = {
                "job_id": job_id,
                "chapter_id": job["chapter_id"],
                "status": job["status"],
                "total": job["total_paragraphs"],
                "completed": job["completed_paragraphs"],
                "failed": job["failed_paragraphs"],
            }
            yield f"data: {json.dumps(data)}\n\n"

            if job["status"] in ("completed", "failed", "partial"):
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
