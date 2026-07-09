"""后台任务管理器 — 批量翻译 + 缓存 + 耗时日志"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from backend.database import get_connection, row_to_dict, rows_to_list, source_hash
from backend.agents.translator import TranslatorAgent

logger = logging.getLogger("ai-reader.worker")

BATCH_SIZE = 8


class JobManager:
    """
    翻译任务管理器
    改进：
      - 批量翻译（一次请求翻 N 段）
      - 缓存命中直接返回
      - 耗时日志
      - 增量标记完成（前端可见渐进式）
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._pause_flags: dict[str, asyncio.Event] = {}

    # ── 任务生命周期 ────────────────────────────────

    async def start_translate(self, chapter_id: str, job_id: str = None) -> str:
        """启动章节翻译（后台任务）"""
        if job_id is None:
            job_id = str(uuid.uuid4())

        conn = get_connection()
        rows = conn.execute(
            "SELECT id, source_text FROM paragraphs WHERE chapter_id=? AND status='pending' ORDER BY paragraph_order",
            (chapter_id,),
        ).fetchall()
        conn.close()

        if not rows:
            raise ValueError("该章节没有待翻译的段落")

        conn = get_connection()
        conn.execute(
            "INSERT INTO jobs (id, chapter_id, status, total_paragraphs, job_type) VALUES (?,?,?,?,?)",
            (job_id, chapter_id, "running", len(rows), "translate"),
        )
        conn.commit()
        conn.close()

        self._pause_flags[job_id] = asyncio.Event()
        self._pause_flags[job_id].set()

        # 启动后台任务（不 await）
        task = asyncio.create_task(self._translate_loop(job_id, chapter_id, rows))
        self._tasks[job_id] = task

        logger.info("任务启动: job=%s chapter=%s 段落=%d", job_id, chapter_id, len(rows))
        return job_id

    async def pause(self, job_id: str) -> bool:
        if job_id in self._pause_flags:
            self._pause_flags[job_id].clear()
            self._update_job_status(job_id, "paused")
            return True
        return False

    async def resume(self, job_id: str) -> bool:
        if job_id in self._pause_flags:
            self._pause_flags[job_id].set()
            self._update_job_status(job_id, "running")
            return True
        return False

    async def retry_failed(self, job_id: str) -> bool:
        conn = get_connection()
        job = row_to_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if not job:
            conn.close()
            return False

        conn.execute(
            "UPDATE paragraphs SET status='pending', error_message='' WHERE chapter_id=? AND status='failed'",
            (job["chapter_id"],),
        )
        conn.commit()
        conn.close()

        conn = get_connection()
        rows = conn.execute(
            "SELECT id, source_text FROM paragraphs WHERE chapter_id=? AND status='pending' ORDER BY paragraph_order",
            (job["chapter_id"],),
        ).fetchall()
        conn.close()

        if rows:
            self._pause_flags[job_id] = asyncio.Event()
            self._pause_flags[job_id].set()
            task = asyncio.create_task(self._translate_loop(job_id, job["chapter_id"], rows))
            self._tasks[job_id] = task

        return True

    def get_status(self, job_id: str) -> Optional[dict]:
        conn = get_connection()
        job = row_to_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        conn.close()
        return job

    # ── 内部：翻译循环 ──────────────────────────────

    async def _translate_loop(self, job_id: str, chapter_id: str, paragraphs: list[dict]):
        """
        批量翻译循环：
          1. 每批先查缓存
          2. 未命中的批量调 API
          3. 逐段写入 DB（前端可见渐进式）
          4. 输出耗时日志
        """
        translator = TranslatorAgent()
        conn = get_connection()
        completed = 0
        failed = 0
        total = len(paragraphs)

        job_start = time.monotonic()
        logger.info("翻译循环开始: job=%s chapter=%s 共%d段", job_id, chapter_id, total)

        try:
            # 分批处理
            for batch_start in range(0, total, BATCH_SIZE):
                # 检查暂停/取消
                await self._pause_flags[job_id].wait()
                if asyncio.current_task().cancelled():
                    logger.info("翻译循环被取消: job=%s", job_id)
                    break

                batch_rows = paragraphs[batch_start:batch_start + BATCH_SIZE]
                batch = [
                    {"id": r["id"], "text": r["source_text"], "chapter_id": chapter_id}
                    for r in batch_rows
                ]

                batch_log_start = time.monotonic()
                results = await translator.translate_batch(batch)
                batch_elapsed = time.monotonic() - batch_log_start

                # 逐段写入 DB
                write_start = time.monotonic()
                for r, result in zip(batch_rows, results):
                    para_id = r["id"]
                    translation = result.get("translation", "")
                    error = result.get("error")

                    if translation and not error:
                        conn.execute(
                            "UPDATE paragraphs SET translation=?, status='completed', updated_at=datetime('now') WHERE id=?",
                            (translation, para_id),
                        )
                        completed += 1
                    elif error:
                        conn.execute(
                            "UPDATE paragraphs SET status='failed', error_message=?, updated_at=datetime('now') WHERE id=?",
                            (str(error)[:500], para_id),
                        )
                        failed += 1
                    else:
                        # 空翻译也算失败
                        conn.execute(
                            "UPDATE paragraphs SET status='failed', error_message='empty translation', updated_at=datetime('now') WHERE id=?",
                            (para_id,),
                        )
                        failed += 1

                    # 更新 job 进度（每条写入后立即更新，前端可见）
                    conn.execute(
                        "UPDATE jobs SET completed_paragraphs=?, failed_paragraphs=?, updated_at=datetime('now') WHERE id=?",
                        (completed, failed, job_id),
                    )
                    conn.commit()

                write_elapsed = time.monotonic() - write_start

                logger.info(
                    "  batch %3d-%3d/%d: %d完成 %d失败 batch=%.1fs write=%.1fs 累计=%d/%d",
                    batch_start + 1,
                    min(batch_start + BATCH_SIZE, total),
                    total,
                    sum(1 for r in results if r.get("translation") and not r.get("error")),
                    sum(1 for r in results if r.get("error")),
                    batch_elapsed,
                    write_elapsed,
                    completed,
                    total,
                )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("翻译循环异常: job=%s error=%s", job_id, e, exc_info=True)
        finally:
            conn.close()

        # 最终状态
        total_elapsed = time.monotonic() - job_start
        final_status = "completed" if failed == 0 and completed == total else "partial"
        if failed == total:
            final_status = "failed"
        self._update_job_status(job_id, final_status)

        # 更新章节翻译状态
        conn = get_connection()
        para_statuses = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM paragraphs WHERE chapter_id=? GROUP BY status",
            (chapter_id,),
        ).fetchall()
        conn.close()

        status_map = {r["status"]: r["cnt"] for r in para_statuses}
        if status_map.get("failed", 0) > 0 or status_map.get("pending", 0) > 0:
            chapter_status = "partial"
        elif status_map.get("completed", 0) > 0:
            chapter_status = "completed"
        else:
            chapter_status = "pending"

        conn = get_connection()
        conn.execute("UPDATE chapters SET translate_status=? WHERE id=?", (chapter_status, chapter_id))
        conn.commit()
        conn.close()

        self._tasks.pop(job_id, None)
        self._pause_flags.pop(job_id, None)

        avg_speed = total / total_elapsed if total_elapsed > 0 else 0
        logger.info(
            "翻译完成: job=%s chapter=%s status=%s %d/%d 段 %.1fs (%.2f 段/秒)",
            job_id, chapter_id, final_status, completed, total, total_elapsed, avg_speed,
        )

    def _update_job_status(self, job_id: str, status: str):
        conn = get_connection()
        conn.execute(
            "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, job_id),
        )
        conn.commit()
        conn.close()


# 全局单例
job_manager = JobManager()
