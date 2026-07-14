"""书籍和章节路由"""

import gc
import json
import logging
import threading
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.database import get_connection, row_to_dict, rows_to_list
from backend.parsers import get_parser
from backend.config import STORAGE_DIR

logger = logging.getLogger("ai-reader.parse")

router = APIRouter(prefix="/api", tags=["books"])

# ── 全局 shutdown 事件 ────────────────────────────────
_shutdown_event = threading.Event()


def signal_shutdown():
    """通知所有后台解析线程尽快退出。"""
    _shutdown_event.set()


# ═══════════════════════════════════════════════════════════
# 书籍 CRUD
# ═══════════════════════════════════════════════════════════


@router.get("/books")
async def list_books():
    """获取所有书籍，按上传时间倒序"""
    conn = get_connection()
    rows = rows_to_list(conn.execute(
        "SELECT * FROM books ORDER BY uploaded_at DESC"
    ).fetchall())
    conn.close()
    return {"books": rows}


@router.get("/books/{book_id}")
async def get_book(book_id: str):
    """获取书籍详情（含章节列表）"""
    conn = get_connection()
    book = row_to_dict(conn.execute(
        "SELECT * FROM books WHERE id=?", (book_id,)
    ).fetchone())
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


@router.get("/books/{book_id}/progress")
async def get_book_progress(book_id: str):
    """获取书籍解析进度"""
    conn = get_connection()
    book = row_to_dict(conn.execute(
        "SELECT parse_status, current_stage, total_pages, parsed_pages, "
        "failed_pages, error_message FROM books WHERE id=?",
        (book_id,),
    ).fetchone())
    conn.close()

    if not book:
        raise HTTPException(404, "书籍不存在")

    total = book["total_pages"] or 0
    parsed = book["parsed_pages"] or 0
    progress = round(parsed / total * 100, 1) if total > 0 else 0.0

    return {
        "book_id": book_id,
        "status": book["parse_status"],
        "current_stage": book["current_stage"],
        "total_pages": total,
        "parsed_pages": parsed,
        "failed_pages": book["failed_pages"] or 0,
        "progress": progress,
        "error_message": book["error_message"] or "",
    }


@router.post("/books/{book_id}/parse")
async def parse_book(book_id: str):
    """解析书籍：后台线程逐页解析 + 最后统一组装"""
    conn = get_connection()
    book = row_to_dict(conn.execute(
        "SELECT * FROM books WHERE id=?", (book_id,)
    ).fetchone())
    conn.close()

    if not book:
        raise HTTPException(404, "书籍不存在")

    file_path = book["file_path"]
    if not Path(file_path).exists():
        raise HTTPException(400, "原文件不存在")

    # 检查是否已在解析中
    if book["parse_status"] in ("parsing", "assembling"):
        raise HTTPException(409, "书籍正在解析中，请等待完成")

    # ── 清理上一次解析的残留数据 ──────────────────
    _clean_book_parse_data(book_id)

    # ── 初始化书籍状态 ────────────────────────────
    book_id_str = book_id
    conn = get_connection()

    # 获取总页数
    pdf_doc = fitz.open(file_path)
    total_pages = len(pdf_doc)
    pdf_doc.close()

    conn.execute(
        "UPDATE books SET parse_status='parsing', current_stage='parsing', "
        "total_pages=?, parsed_pages=0, failed_pages=0, "
        "error_message='', total_chapters=0 WHERE id=?",
        (total_pages, book_id_str),
    )
    conn.commit()
    conn.close()

    logger.info("📖 开始后台解析: %s (%d 页)", Path(file_path).name, total_pages)

    # ── 启动后台线程 ──────────────────────────────
    thread = threading.Thread(
        target=_parse_book_background,
        args=(book_id_str, file_path, total_pages, _shutdown_event),
        name=f"parse-{book_id_str[:8]}",
        daemon=True,
    )
    thread.start()

    return {"book_id": book_id_str, "status": "started", "total_pages": total_pages}


@router.delete("/books/{book_id}")
async def delete_book(book_id: str):
    """删除书籍及其所有关联数据"""
    conn = get_connection()
    book = row_to_dict(conn.execute(
        "SELECT * FROM books WHERE id=?", (book_id,)
    ).fetchone())
    if not book:
        conn.close()
        raise HTTPException(404, "书籍不存在")

    _clean_book_parse_data(book_id)

    # 删除书籍记录
    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()

    # 清理图片资源
    import shutil
    asset_dir = STORAGE_DIR / "books" / book_id
    if asset_dir.exists():
        shutil.rmtree(asset_dir)

    logger.info("已删除书籍: %s (%s)", book["title"], book_id)
    return {"status": "deleted"}


@router.get("/books/{book_id}/assets/{asset_path:path}")
async def serve_book_asset(book_id: str, asset_path: str):
    """提供书籍的静态资源（图片等）。带路径穿越防护。"""
    base_dir = (STORAGE_DIR / "books" / book_id / "assets").resolve()
    requested = (base_dir / asset_path).resolve()

    if not str(requested).startswith(str(base_dir)):
        raise HTTPException(403, "Forbidden")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(404, "资源不存在")

    return FileResponse(requested)


# ═══════════════════════════════════════════════════════════
# 内部函数
# ═══════════════════════════════════════════════════════════


def _clean_book_parse_data(book_id: str):
    """删除书本的上一次解析残留数据。"""
    conn = get_connection()

    # 删除旧段落 → 旧章节 → 旧页面
    old_chapters = conn.execute(
        "SELECT id FROM chapters WHERE book_id=?", (book_id,)
    ).fetchall()
    old_chapter_ids = [r["id"] for r in old_chapters]

    if old_chapter_ids:
        placeholders = ",".join("?" * len(old_chapter_ids))
        para_ids = conn.execute(
            f"SELECT id FROM paragraphs WHERE chapter_id IN ({placeholders})",
            old_chapter_ids,
        ).fetchall()
        if para_ids:
            pid_placeholders = ",".join("?" * len(para_ids))
            pids = [r["id"] for r in para_ids]
            conn.execute(
                f"DELETE FROM translations WHERE paragraph_id IN ({pid_placeholders})", pids
            )
        conn.execute(
            f"DELETE FROM paragraphs WHERE chapter_id IN ({placeholders})", old_chapter_ids
        )
        conn.execute(
            f"DELETE FROM jobs WHERE chapter_id IN ({placeholders})", old_chapter_ids
        )
        conn.execute(
            f"DELETE FROM chapters WHERE id IN ({placeholders})", old_chapter_ids
        )

    # 删除旧页面记录
    conn.execute("DELETE FROM book_pages WHERE book_id=?", (book_id,))
    conn.commit()
    conn.close()


def _parse_book_background(
    book_id: str,
    file_path: str,
    total_pages: int,
    shutdown: threading.Event,
):
    """
    后台线程：逐页解析 + 最终组装。

    每页处理完立即 upsert 到 book_pages 并 commit。
    不使用请求线程的数据库连接。
    """
    parser = get_parser(file_path)

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        _fail_book(book_id, f"无法打开 PDF: {e}")
        return

    try:
        failed_count = 0

        for page_idx in range(total_pages):
            # ── 检查 shutdown ──────────────────────
            if shutdown.is_set():
                logger.info("[PDF] 收到 shutdown 信号，停止解析: %s", book_id)
                _fail_book(book_id, "服务关闭，解析中断")
                doc.close()
                return

            page_number = page_idx + 1

            if page_number % 20 == 1 or page_number == total_pages:
                logger.info(
                    "[PDF] 正在处理第 %d/%d 页", page_number, total_pages
                )

            # ── 解析单页 ────────────────────────────
            try:
                page = doc[page_idx]
                result = parser.parse_single_page(page, page_number, doc=doc)
            except Exception as exc:
                logger.error(
                    "[PDF] 第 %d/%d 页异常: %s", page_number, total_pages, exc
                )
                result = {
                    "page_number": page_number,
                    "width": page.rect.width * 200 / 72 if 'page' in dir() else 0,
                    "height": page.rect.height * 200 / 72 if 'page' in dir() else 0,
                    "parse_method": "",
                    "lines": [],
                    "raw_text": "",
                    "confidence": 0.0,
                    "status": "failed",
                    "error_message": str(exc)[:500],
                }

            # ── 保存单页结果 ────────────────────────
            _upsert_page_result(book_id, result)

            if result.get("status") == "failed":
                failed_count += 1

            # ── 更新进度 ────────────────────────────
            _update_progress(book_id, total_pages, page_number, failed_count)

            # ── 释放内存 ────────────────────────────
            del result
            if page_number % 10 == 0:
                gc.collect()

        doc.close()
        doc = None

        if shutdown.is_set():
            _fail_book(book_id, "服务关闭，解析中断")
            return

        # ════════════════════════════════════════════
        # 阶段 2：整书组装
        # ════════════════════════════════════════════

        _set_stage(book_id, "assembling")
        logger.info("[PDF] 开始组装: %s (%d 页)", book_id, total_pages)

        # 从 book_pages 读取所有 completed 页面
        all_page_data = _load_page_results(book_id)

        if not all_page_data:
            _fail_book(book_id, "没有可用页面数据")
            return

        # 调用解析器组装
        chapters_data = parser.assemble(all_page_data)

        if not chapters_data:
            _fail_book(book_id, "组装失败：无法生成章节")
            return

        logger.info("[PDF] 组装完成: %d 章", len(chapters_data))

        # ── 写入 chapters 和 paragraphs ────────────
        conn = get_connection()
        chapter_count = 0
        paragraph_count = 0

        for ch in chapters_data:
            chapter_id = str(uuid.uuid4())
            title = ch["title"]
            paragraphs = ch.get("paragraphs", [])

            conn.execute(
                "INSERT INTO chapters (id, book_id, title, chapter_order, paragraph_count) "
                "VALUES (?,?,?,?,?)",
                (chapter_id, book_id, title, ch["chapter_order"], len(paragraphs)),
            )

            for para in paragraphs:
                para_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO paragraphs "
                    "(id, chapter_id, paragraph_order, source_text, source_html, "
                    "page_number, source_bbox, status) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        para_id, chapter_id,
                        para.get("paragraph_order", 0),
                        para.get("text", ""),
                        para.get("html", ""),
                        para.get("page_number", 0),
                        para.get("bbox", ""),
                        "pending",
                    ),
                )
                paragraph_count += 1
            chapter_count += 1

        # 标记完成
        conn.execute(
            "UPDATE books SET parse_status='completed', current_stage='completed', "
            "total_chapters=?, error_message='' WHERE id=?",
            (chapter_count, book_id),
        )
        conn.commit()
        conn.close()

        logger.info(
            "[PDF] 解析完成: %s — %d 章, %d 段, %d/%d 页失败",
            book_id, chapter_count, paragraph_count, failed_count, total_pages,
        )

    except Exception as exc:
        logger.error("[PDF] 解析异常: %s — %s", book_id, exc, exc_info=True)
        if doc:
            try:
                doc.close()
            except Exception:
                pass
        _fail_book(book_id, str(exc)[:500])


def _upsert_page_result(book_id: str, result: dict):
    """插入或更新单页解析结果。"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO book_pages "
            "(book_id, page_number, width, height, parse_method, "
            "lines_json, raw_text, confidence, status, error_message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(book_id, page_number) DO UPDATE SET "
            "width=excluded.width, height=excluded.height, "
            "parse_method=excluded.parse_method, lines_json=excluded.lines_json, "
            "raw_text=excluded.raw_text, confidence=excluded.confidence, "
            "status=excluded.status, error_message=excluded.error_message, "
            "updated_at=datetime('now')",
            (
                book_id,
                result["page_number"],
                result.get("width", 0),
                result.get("height", 0),
                result.get("parse_method", ""),
                json.dumps(result.get("lines", []), ensure_ascii=False),
                result.get("raw_text", ""),
                result.get("confidence", 0.0),
                result.get("status", "failed"),
                result.get("error_message", ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _update_progress(book_id: str, total_pages: int, current_page: int, failed: int):
    """更新解析进度。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE books SET parsed_pages=?, failed_pages=? WHERE id=?",
            (current_page, failed, book_id),
        )
        conn.commit()
    finally:
        conn.close()


def _set_stage(book_id: str, stage: str):
    """更新解析阶段。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE books SET current_stage=? WHERE id=?",
            (stage, book_id),
        )
        conn.commit()
    finally:
        conn.close()


def _fail_book(book_id: str, error_message: str):
    """标记书籍解析失败。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE books SET parse_status='failed', current_stage='', "
            "error_message=? WHERE id=?",
            (error_message[:500] if error_message else "", book_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.error("[PDF] 解析失败: %s — %s", book_id, error_message)


def _load_page_results(book_id: str) -> list[dict]:
    """从 book_pages 加载所有已完成页面的解析结果。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM book_pages WHERE book_id=? AND status='completed' "
            "ORDER BY page_number",
            (book_id,),
        ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            # 还原 lines_json
            try:
                lines = json.loads(r.get("lines_json", "[]"))
            except json.JSONDecodeError:
                lines = []

            results.append({
                "page_number": r["page_number"],
                "width": r["width"],
                "height": r["height"],
                "parse_method": r["parse_method"],
                "lines": lines,
                "raw_text": r.get("raw_text", ""),
                "confidence": r.get("confidence", 0.0),
                "status": r.get("status", "completed"),
                "error_message": r.get("error_message", ""),
            })
        return results
    finally:
        conn.close()
