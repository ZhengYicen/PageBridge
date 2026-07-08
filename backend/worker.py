"""后台任务管理器 — 管理翻译任务的执行、暂停、继续、重试"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional

from backend.database import get_connection, row_to_dict, rows_to_list
from backend.agents.translator import TranslatorAgent


class JobManager:
    """
    翻译任务管理器
    用 in-memory dict 跟踪运行中的任务，支持暂停/继续/重试
    每个任务是一个 asyncio.Task，定期检查自己的暂停标志
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}  # job_id -> asyncio.Task
        self._pause_flags: dict[str, asyncio.Event] = {}  # job_id -> Event

    # ── 任务生命周期 ────────────────────────────────

    async def start_translate(self, chapter_id: str, job_id: str = None) -> str:
        """启动章节翻译（后台任务）"""
        if job_id is None:
            job_id = str(uuid.uuid4())

        # 检查章节是否有待翻译段落
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, source_text FROM paragraphs WHERE chapter_id=? AND status='pending' ORDER BY paragraph_order",
            (chapter_id,),
        ).fetchall()
        conn.close()

        if not rows:
            raise ValueError("该章节没有待翻译的段落")

        # 创建 job 记录
        conn = get_connection()
        conn.execute(
            "INSERT INTO jobs (id, chapter_id, status, total_paragraphs, job_type) VALUES (?,?,?,?,?)",
            (job_id, chapter_id, "running", len(rows), "translate"),
        )
        conn.commit()
        conn.close()

        # 创建暂停标志
        self._pause_flags[job_id] = asyncio.Event()
        self._pause_flags[job_id].set()  # 默认不暂停

        # 启动后台任务
        task = asyncio.create_task(self._translate_loop(job_id, chapter_id, rows))
        self._tasks[job_id] = task

        return job_id

    async def pause(self, job_id: str) -> bool:
        """暂停任务"""
        if job_id in self._pause_flags:
            self._pause_flags[job_id].clear()
            self._update_job_status(job_id, "paused")
            return True
        return False

    async def resume(self, job_id: str) -> bool:
        """继续任务"""
        if job_id in self._pause_flags:
            self._pause_flags[job_id].set()
            self._update_job_status(job_id, "running")
            return True
        return False

    async def retry_failed(self, job_id: str) -> bool:
        """重试任务中失败的段落"""
        conn = get_connection()
        job = row_to_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if not job:
            conn.close()
            return False

        # 将失败段落重置为 pending
        conn.execute(
            "UPDATE paragraphs SET status='pending', error_message='' WHERE chapter_id=? AND status='failed'",
            (job["chapter_id"],),
        )
        conn.commit()
        conn.close()

        # 准备重新运行
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
        """获取任务状态"""
        conn = get_connection()
        job = row_to_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        conn.close()
        return job

    # ── 内部 ─────────────────────────────────────────

    async def _translate_loop(self, job_id: str, chapter_id: str, paragraphs: list[dict]):
        """翻译循环：逐段翻译，支持暂停检查"""
        translator = TranslatorAgent()
        conn = get_connection()
        completed = 0
        failed = 0
        last_translation = ""

        try:
            for para_id, source_text in ((r["id"], r["source_text"]) for r in paragraphs):
                # 检查暂停
                await self._pause_flags[job_id].wait()

                # 检查是否被取消
                if asyncio.current_task().cancelled():
                    break

                try:
                    # 更新段落状态为翻译中
                    conn.execute(
                        "UPDATE paragraphs SET status='translating', updated_at=datetime('now') WHERE id=?",
                        (para_id,),
                    )
                    conn.commit()

                    # 调用翻译
                    translation = await translator.translate(source_text, last_translation)
                    last_translation = translation

                    # 保存译文
                    conn.execute(
                        "UPDATE paragraphs SET translation=?, status='completed', updated_at=datetime('now') WHERE id=?",
                        (translation, para_id),
                    )
                    completed += 1

                except Exception as e:
                    conn.execute(
                        "UPDATE paragraphs SET status='failed', error_message=?, updated_at=datetime('now') WHERE id=?",
                        (str(e)[:500], para_id),
                    )
                    failed += 1

                # 更新 job 进度
                conn.execute(
                    "UPDATE jobs SET completed_paragraphs=?, failed_paragraphs=?, updated_at=datetime('now') WHERE id=?",
                    (completed, failed, job_id),
                )
                conn.commit()

        except asyncio.CancelledError:
            pass
        finally:
            conn.close()

        # 更新最终状态
        total = len(paragraphs)
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

        # 清理
        self._tasks.pop(job_id, None)
        self._pause_flags.pop(job_id, None)

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
