"""SQLite 数据库初始化，支持后续迁移到 PostgreSQL（预留 async 接口）"""

import sqlite3
import os
import hashlib
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "storage" / "app.db"

# 建表 SQL
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    author TEXT DEFAULT '',
    format TEXT NOT NULL,
    file_path TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    total_chapters INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    chapter_id TEXT NOT NULL REFERENCES chapters(id),
    status TEXT NOT NULL DEFAULT 'running',
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

-- 索引
CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_chapter_id ON paragraphs(chapter_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_status ON paragraphs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_chapter_id ON jobs(chapter_id);
CREATE INDEX IF NOT EXISTS idx_glossary_book_id ON glossary(book_id);
CREATE INDEX IF NOT EXISTS idx_translations_source_hash ON translations(source_hash);
CREATE INDEX IF NOT EXISTS idx_translations_paragraph_id ON translations(paragraph_id);
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
    """初始化数据库表，兼容旧库添加新字段"""
    conn = get_connection()
    conn.executescript(CREATE_TABLES_SQL)
    # 兼容旧库：添加 uploaded_at 列
    try:
        conn.execute("ALTER TABLE books ADD COLUMN uploaded_at TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()


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
