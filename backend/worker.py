"""Durable SQLite-backed task queue and worker.

Architecture:
  - API creates 'queued' jobs in the database
  - Worker claims jobs via BEGIN IMMEDIATE (avoids double-claim)
  - Jobs are processed sequentially (single-worker model for v1)
  - Support for 'translate' and 'parse' job types
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

from backend.agents.translator import TranslatorAgent
from backend.config import TRANSLATE_API_TIMEOUT, TRANSLATE_MAX_RETRIES, TRANSLATE_RETRY_BACKOFF
from backend.database import get_connection, row_to_dict

logger = logging.getLogger("pagebridge.worker")
BATCH_SIZE = 8


class JobManager:
    """Job creation and control API (used by FastAPI routes)."""

    async def start_translate(self, chapter_id: str, owner_id: str = "") -> str:
        """Create a translate job. Ensures only one active job per chapter."""
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")

            chapter = row_to_dict(conn.execute(
                "SELECT book_id FROM chapters WHERE id=?", (chapter_id,)
            ).fetchone())
            rows = conn.execute(
                "SELECT id FROM paragraphs WHERE chapter_id=? AND status IN ('pending','failed') "
                "ORDER BY paragraph_order",
                (chapter_id,),
            ).fetchall()

            if not chapter or not rows:
                raise ValueError("该章节没有待翻译段落")

            # 检查重复任务
            duplicate = conn.execute(
                "SELECT 1 FROM jobs WHERE chapter_id=? AND job_type='translate' "
                "AND status IN ('queued','running','paused') LIMIT 1",
                (chapter_id,),
            ).fetchone()
            if duplicate:
                raise ValueError("该章节已有翻译任务")

            # 重置失败段落为 pending
            conn.execute(
                "UPDATE paragraphs SET status='pending',error_message='' "
                "WHERE chapter_id=? AND status='failed'",
                (chapter_id,),
            )

            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO jobs(id,chapter_id,book_id,owner_id,status,total_paragraphs,job_type) "
                "VALUES(?,?,?,?,?,?,?)",
                (job_id, chapter_id, chapter["book_id"], owner_id, "queued", len(rows), "translate"),
            )
            conn.commit()
            logger.info("创建翻译任务: %s (章节=%s, %d 段, 用户=%s)",
                        job_id, chapter_id, len(rows), owner_id)
            return job_id
        except ValueError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.exception("创建翻译任务失败")
            raise ValueError(f"创建任务失败: {exc}") from exc
        finally:
            conn.close()

    async def start_parse(self, book_id: str, owner_id: str = "") -> str:
        """Create a parse job. Ensures only one active parse job per book."""
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT 1 FROM jobs WHERE book_id=? AND job_type='parse' "
                "AND status IN ('queued','running') LIMIT 1",
                (book_id,),
            ).fetchone()
            if existing:
                raise ValueError("该书籍正在解析中")

            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO jobs(id,book_id,owner_id,status,job_type) VALUES(?,?,?,?,?)",
                (job_id, book_id, owner_id, "queued", "parse"),
            )
            conn.commit()
            logger.info("创建解析任务: %s (书籍=%s)", job_id, book_id)
            return job_id
        except ValueError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise ValueError(f"创建解析任务失败: {exc}") from exc
        finally:
            conn.close()

    async def pause(self, job_id: str) -> bool:
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE jobs SET status='paused' WHERE id=? AND status IN ('queued','running')",
                (job_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    async def resume(self, job_id: str) -> bool:
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE jobs SET status='queued' WHERE id=? AND status='paused'",
                (job_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    async def retry_failed(self, job_id: str) -> bool:
        conn = get_connection()
        try:
            job = row_to_dict(conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone())
            if not job:
                return False
            conn.execute(
                "UPDATE paragraphs SET status='pending',error_message='' "
                "WHERE chapter_id=? AND status='failed'",
                (job["chapter_id"],),
            )
            conn.execute(
                "UPDATE jobs SET status='queued',completed_paragraphs=0,failed_paragraphs=0,error_message='' "
                "WHERE id=? AND status IN ('failed','partial')",
                (job_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def get_status(self, job_id: str) -> dict | None:
        conn = get_connection()
        try:
            return row_to_dict(conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone())
        finally:
            conn.close()


job_manager = JobManager()


# ═══════════════════════════════════════════════════════════
# Worker core
# ═══════════════════════════════════════════════════════════


def claim_job() -> dict | None:
    """Atomically claim the next queued job using BEGIN IMMEDIATE."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row:
            job = dict(row)
            changed = conn.execute(
                "UPDATE jobs SET status='running',started_at=datetime('now'),error_message='' "
                "WHERE id=? AND status='queued'",
                (job["id"],),
            ).rowcount
            conn.commit()
            return job if changed else None
        else:
            conn.commit()
            return None
    except Exception:
        conn.rollback()
        logger.exception("claim_job 异常")
        return None
    finally:
        conn.close()


async def process_translate_job(job: dict):
    """Process a translate job with retry logic."""
    from backend.agents.translator import TranslatorAgent
    translator = TranslatorAgent()
    job_id = job["id"]
    chapter_id = job["chapter_id"]
    owner_id = job.get("owner_id", "")

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, source_text FROM paragraphs "
            "WHERE chapter_id=? AND status='pending' ORDER BY paragraph_order",
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()

    completed = failed = 0

    try:
        for batch_start in range(0, len(rows), BATCH_SIZE):
            # 检查 pause 信号
            conn = get_connection()
            try:
                state = conn.execute(
                    "SELECT status FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
            finally:
                conn.close()
            if not state or state["status"] == "paused":
                logger.info("任务 %s 被暂停 (%d/%d 完成)", job_id, completed, len(rows))
                return

            batch = rows[batch_start:batch_start + BATCH_SIZE]
            batch_data = [
                {"id": r["id"], "text": r["source_text"], "chapter_id": chapter_id}
                for r in batch
            ]

            # 带重试的翻译调用
            last_error = None
            for attempt in range(TRANSLATE_MAX_RETRIES):
                try:
                    results = await asyncio.wait_for(
                        translator.translate_batch(batch_data),
                        timeout=TRANSLATE_API_TIMEOUT,
                    )
                    if len(results) != len(batch_data):
                        result_map = {r.get("id"): r for r in results}
                        results = [result_map.get(b["id"], {"translation": "", "error": "missing"})
                                   for b in batch_data]
                    last_error = None
                    break
                except asyncio.TimeoutError:
                    last_error = f"API 超时 (第 {attempt+1} 次)"
                    if attempt < TRANSLATE_MAX_RETRIES - 1:
                        await asyncio.sleep(TRANSLATE_RETRY_BACKOFF ** attempt)
                except Exception as exc:
                    last_error = str(exc)
                    if attempt < TRANSLATE_MAX_RETRIES - 1:
                        await asyncio.sleep(TRANSLATE_RETRY_BACKOFF ** attempt)

            conn = get_connection()
            try:
                if last_error:
                    for item in batch_data:
                        conn.execute(
                            "UPDATE paragraphs SET status='failed',error_message=?,updated_at=datetime('now') WHERE id=?",
                            (str(last_error)[:500], item["id"]),
                        )
                    failed += len(batch_data)
                else:
                    for source_item, result in zip(batch_data, results):
                        text = result.get("translation", "")
                        error = result.get("error")
                        if text and not error:
                            conn.execute(
                                "UPDATE paragraphs SET translation=?,status='completed',updated_at=datetime('now') WHERE id=?",
                                (text, source_item["id"]),
                            )
                            completed += 1
                        else:
                            conn.execute(
                                "UPDATE paragraphs SET status='failed',error_message=?,updated_at=datetime('now') WHERE id=?",
                                (str(error or "empty translation")[:500], source_item["id"]),
                            )
                            failed += 1

                conn.execute(
                    "UPDATE jobs SET completed_paragraphs=?,failed_paragraphs=?,updated_at=datetime('now') WHERE id=?",
                    (completed, failed, job_id),
                )
                conn.commit()
            finally:
                conn.close()

        # 最终状态
        final_status = "completed" if not failed else ("failed" if not completed else "partial")
        conn = get_connection()
        try:
            conn.execute("UPDATE jobs SET status=?,updated_at=datetime('now') WHERE id=?", (final_status, job_id))
            conn.execute("UPDATE chapters SET translate_status=? WHERE id=?", (final_status, chapter_id))
            conn.commit()
        finally:
            conn.close()

        logger.info("翻译任务 %s → %s (%d/%d 成功)", job_id, final_status, completed, len(rows))

    except Exception as exc:
        logger.exception("翻译任务异常: %s", job_id)
        conn = get_connection()
        try:
            conn.execute("UPDATE jobs SET status='failed',error_message=?,updated_at=datetime('now') WHERE id=?",
                         (str(exc)[:500], job_id))
            conn.commit()
        finally:
            conn.close()


async def process_parse_job(job: dict):
    """Process a parse job."""
    from backend.routers.books import _parse_book_background, _shutdown_event
    book_id = job["book_id"]
    job_id = job["id"]

    conn = get_connection()
    try:
        book = row_to_dict(conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    finally:
        conn.close()

    if not book or not Path(book["file_path"]).exists():
        conn = get_connection()
        try:
            conn.execute("UPDATE jobs SET status='failed',error_message='文件不存在' WHERE id=?", (job_id,))
            conn.commit()
        finally:
            conn.close()
        return

    try:
        import fitz
        pdf_doc = fitz.open(book["file_path"])
        total_pages = len(pdf_doc)
        pdf_doc.close()

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE books SET parse_status='parsing', current_stage='parsing', "
                "total_pages=?, parsed_pages=0, failed_pages=0, error_message='', total_chapters=0 WHERE id=?",
                (total_pages, book_id),
            )
            conn.commit()
        finally:
            conn.close()

        await asyncio.to_thread(_parse_book_background, book_id, book["file_path"], total_pages, _shutdown_event)

        conn = get_connection()
        try:
            final_book = row_to_dict(conn.execute("SELECT parse_status FROM books WHERE id=?", (book_id,)).fetchone())
            final_status = "completed" if final_book and final_book["parse_status"] == "completed" else "failed"
            conn.execute("UPDATE jobs SET status=?,updated_at=datetime('now') WHERE id=?", (final_status, job_id))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("解析任务异常: %s", job_id)
        conn = get_connection()
        try:
            conn.execute("UPDATE jobs SET status='failed',error_message=?,updated_at=datetime('now') WHERE id=?",
                         (str(exc)[:500], job_id))
            conn.commit()
        finally:
            conn.close()


async def run_worker():
    logger.info("PageBridge worker started")
    while True:
        job = claim_job()
        if job:
            try:
                if job["job_type"] == "translate":
                    await process_translate_job(job)
                elif job["job_type"] == "parse":
                    await process_parse_job(job)
            except Exception as exc:
                logger.exception("处理任务异常: %s", job.get("id"))
        else:
            await asyncio.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run_worker())
