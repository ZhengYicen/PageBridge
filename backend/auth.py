"""Cookie-based email authentication."""

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from backend.config import SESSION_DAYS
from backend.database import get_connection, row_to_dict

logger = logging.getLogger("pagebridge.auth")

COOKIE_NAME = "pagebridge_session"


def normalize_email(email: str) -> str:
    """统一处理邮箱：去除首尾空格、转小写。"""
    return email.strip().lower()


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 密码哈希。"""
    if len(password) < 6:
        raise ValueError("密码长度至少 6 位")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt, expected = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str) -> str:
    """创建 session，返回原始 token（只存 hash 到数据库）。"""
    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn = get_connection()
    try:
        # 清理过期 session
        conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
        conn.execute(
            "INSERT INTO sessions(id,user_id,token_hash,expires_at) VALUES(?,?,?,?)",
            (str(uuid.uuid4()), user_id, token_hash(token), expires.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def current_user(request: Request) -> dict:
    """FastAPI 依赖：从 Cookie 解析当前登录用户。未登录返回 401。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "请先登录")
    conn = get_connection()
    try:
        user = row_to_dict(conn.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
            "WHERE s.token_hash=? AND s.expires_at > datetime('now')",
            (token_hash(token),),
        ).fetchone())
    finally:
        conn.close()
    if not user:
        raise HTTPException(401, "登录已失效")
    return user


def optional_user(request: Request) -> dict | None:
    """同 current_user，但未登录时返回 None 而非 401。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    conn = get_connection()
    try:
        user = row_to_dict(conn.execute(
            "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
            "WHERE s.token_hash=? AND s.expires_at > datetime('now')",
            (token_hash(token),),
        ).fetchone())
    finally:
        conn.close()
    return user


def require_book_owner(conn, book_id: str, user: dict) -> dict:
    """校验当前用户对书籍的归属。越权时返回 404。"""
    book = row_to_dict(conn.execute(
        "SELECT * FROM books WHERE id=?", (book_id,)
    ).fetchone())
    if not book or book.get("owner_id") != user["id"]:
        raise HTTPException(404, "书籍不存在")
    return book


def require_chapter_owner(conn, chapter_id: str, user: dict) -> dict:
    """校验当前用户对章节的归属（通过 book.owner_id 间接鉴权）。"""
    chapter = row_to_dict(conn.execute(
        "SELECT c.* FROM chapters c JOIN books b ON b.id=c.book_id "
        "WHERE c.id=? AND b.owner_id=?",
        (chapter_id, user["id"]),
    ).fetchone())
    if not chapter:
        raise HTTPException(404, "章节不存在")
    return chapter


def require_job_owner(conn, job_id: str, user: dict) -> dict:
    """校验当前用户对任务的归属。"""
    job = row_to_dict(conn.execute(
        """SELECT j.* FROM jobs j
           LEFT JOIN chapters c ON j.chapter_id != '' AND j.chapter_id = c.id
           LEFT JOIN books b ON b.id = COALESCE(j.book_id, c.book_id)
         WHERE j.id=? AND (b.owner_id=? OR j.owner_id=?)""",
        (job_id, user["id"], user["id"]),
    ).fetchone())
    if not job:
        raise HTTPException(404, "任务不存在")
    return job
