"""SQLite 数据库初始化，支持后续迁移到 PostgreSQL（预留 async 接口）"""

import logging
import sqlite3
import os
import hashlib
from pathlib import Path

logger = logging.getLogger("pagebridge.db")

DB_PATH = Path(__file__).resolve().parent.parent / "storage" / "app.db"

# 建表 SQL — 所有 CREATE TABLE / INDEX 在此统一
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    author TEXT DEFAULT '',
    format TEXT NOT NULL,
    file_path TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    total_chapters INTEGER DEFAULT 0,
    total_pages INTEGER DEFAULT 0,
    parsed_pages INTEGER DEFAULT 0,
    failed_pages INTEGER DEFAULT 0,
    current_stage TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invites (
    id TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL REFERENCES users(id),
    used_by TEXT REFERENCES users(id),
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS translation_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    job_id TEXT NOT NULL,
    characters INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS book_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id TEXT NOT NULL REFERENCES books(id),
    page_number INTEGER NOT NULL,
    width REAL NOT NULL,
    height REAL NOT NULL,
    parse_method TEXT NOT NULL DEFAULT '',
    lines_json TEXT DEFAULT '',
    raw_text TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(book_id, page_number)
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id),
    title TEXT NOT NULL,
    chapter_order INTEGER NOT NULL,
    paragraph_count INTEGER DEFAULT 0,
    translate_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paragraphs (
    id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL REFERENCES chapters(id),
    paragraph_order INTEGER NOT NULL,
    source_text TEXT NOT NULL DEFAULT '',
    source_html TEXT DEFAULT '',
    page_number INTEGER DEFAULT 0,
    source_bbox TEXT DEFAULT '',
    translation TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    chapter_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    total_paragraphs INTEGER DEFAULT 0,
    completed_paragraphs INTEGER DEFAULT 0,
    failed_paragraphs INTEGER DEFAULT 0,
    job_type TEXT NOT NULL DEFAULT 'translate',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS glossary (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id),
    term TEXT NOT NULL,
    translation TEXT DEFAULT '',
    category TEXT DEFAULT 'term',
    UNIQUE(book_id, term)
);

CREATE TABLE IF NOT EXISTS translations (
    id TEXT PRIMARY KEY,
    paragraph_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    target_lang TEXT NOT NULL DEFAULT 'zh',
    engine TEXT NOT NULL DEFAULT '',
    prompt_version TEXT DEFAULT '',
    translated_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paragraph_source_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paragraph_id TEXT NOT NULL REFERENCES paragraphs(id),
    pdf_page_index INTEGER NOT NULL,
    pdf_page_number INTEGER NOT NULL,
    bbox TEXT NOT NULL,
    bbox_normalized TEXT NOT NULL,
    original_page_width REAL NOT NULL,
    original_page_height REAL NOT NULL,
    fragment_order INTEGER NOT NULL DEFAULT 0,
    source_text TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    parse_method TEXT DEFAULT '',
    book_page_id INTEGER DEFAULT 0,
    line_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_chapter_id ON paragraphs(chapter_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_status ON paragraphs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_chapter_id ON jobs(chapter_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_glossary_book_id ON glossary(book_id);
CREATE INDEX IF NOT EXISTS idx_translations_source_hash ON translations(source_hash);
CREATE INDEX IF NOT EXISTS idx_translations_paragraph_id ON translations(paragraph_id);
CREATE INDEX IF NOT EXISTS idx_book_pages_book_id ON book_pages(book_id);
CREATE INDEX IF NOT EXISTS idx_source_frags_para ON paragraph_source_fragments(paragraph_id);
CREATE INDEX IF NOT EXISTS idx_source_frags_page ON paragraph_source_fragments(pdf_page_index);
CREATE INDEX IF NOT EXISTS idx_usage_user_created ON translation_usage(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_invites_code_hash ON invites(code_hash);
"""


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表，兼容旧库添加新字段。可重复执行，不破坏已有数据。"""
    conn = get_connection()
    try:
        conn.executescript(CREATE_TABLES_SQL)

        # ── 兼容旧库：渐进式加列 ─────────────────────────
        _add_column_if_missing(conn, "books", "owner_id", "TEXT")
        _add_column_if_missing(conn, "books", "file_size", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "books", "total_pages", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "books", "parsed_pages", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "books", "failed_pages", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "books", "current_stage", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "books", "error_message", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "paragraphs", "page_end", "INTEGER")
        _add_column_if_missing(conn, "paragraphs", "page_start", "INTEGER")
        _add_column_if_missing(conn, "book_pages", "page_index", "INTEGER")
        _add_column_if_missing(conn, "book_pages", "rotation", "INTEGER")
        _add_column_if_missing(conn, "jobs", "book_id", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "jobs", "owner_id", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "jobs", "reserved_characters", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "jobs", "error_message", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "jobs", "started_at", "TEXT")

        # ── 迁移：为旧书籍设置 owner_id ──────────────────
        _migrate_old_books_owner(conn)

        # ── 启动时恢复中断的任务 ────────────────────────
        _cleanup_stale_parse_state(conn)
        _cleanup_stale_jobs(conn)

        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("数据库初始化失败")
        raise
    finally:
        conn.close()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """兼容旧库：如果列不存在则添加。失败时记录日志但不中断。"""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError as e:
        # SQLite 错误代码 1 表示"重复列名"——这是正常情况
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            pass
        else:
            logger.warning("ALTER TABLE %s ADD %s: %s", table, column, e)


def _migrate_old_books_owner(conn: sqlite3.Connection):
    """将 owner_id 为空的旧书籍归属给 bootstrap 管理员（如果存在）。"""
    null_count = conn.execute(
        "SELECT COUNT(*) FROM books WHERE owner_id IS NULL"
    ).fetchone()[0]
    if null_count == 0:
        return

    # 找第一个管理员
    admin = conn.execute(
        "SELECT id FROM users WHERE role='admin' ORDER BY created_at LIMIT 1"
    ).fetchone()
    if admin:
        conn.execute(
            "UPDATE books SET owner_id=? WHERE owner_id IS NULL",
            (admin["id"],)
        )
        logger.info("已迁移 %d 本旧书籍归属到管理员 %s", null_count, admin["id"])
    else:
        logger.warning(
            "发现 %d 本旧书籍没有 owner_id，但尚未创建管理员账号。"
            "引导管理员账号初始化后这些书籍将无法访问，"
            "请在 bootstrap 后手动迁移。", null_count
        )


def _cleanup_stale_parse_state(conn: sqlite3.Connection):
    """启动时将残留的 parsing / assembling 状态统一改为 failed"""
    result = conn.execute(
        "UPDATE books SET parse_status='failed', current_stage='' "
        "WHERE parse_status IN ('parsing', 'assembling')"
    )
    conn.commit()
    if result.rowcount:
        logger.info("恢复了 %d 个中断的解析状态", result.rowcount)


def _cleanup_stale_jobs(conn: sqlite3.Connection):
    """服务重启后，将遗留的 running 状态任务恢复为 queued 或标记中断。"""
    result = conn.execute(
        "UPDATE jobs SET status='queued', error_message='recovered after restart', started_at=NULL "
        "WHERE status IN ('running', 'pausing')"
    )
    conn.commit()
    if result.rowcount:
        logger.info("恢复了 %d 个中断的任务", result.rowcount)


def source_hash(text: str) -> str:
    """计算文本的 MD5 哈希，用于翻译缓存查找"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """将 sqlite3.Row 转为 dict"""
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
