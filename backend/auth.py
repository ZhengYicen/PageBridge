"""Small, dependency-free cookie authentication for the first public release."""

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from backend.config import BOOTSTRAP_ADMIN_PASSWORD, BOOTSTRAP_ADMIN_USERNAME, SESSION_DAYS
from backend.database import get_connection, row_to_dict

logger = logging.getLogger("pagebridge.auth")

COOKIE_NAME = "pagebridge_session"
# 每日 session 清理阈值：超过此数量触发清理
SESSION_CLEANUP_THRESHOLD = 1000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 密码哈希，600k 迭代。"""
    if len(password) < 8:
        raise ValueError("密码长度至少 8 位")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """验证密码，使用恒定时间比较防止时序攻击。"""
    try:
        _, rounds, salt, expected = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def cleanup_expired_sessions(conn):
    """删除过期 session，避免 sessions 表无限增长。"""
    result = conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    if result.rowcount:
        logger.debug("清理了 %d 个过期 session", result.rowcount)


def create_session(user_id: str) -> str:
    """创建新 session，返回原始 token（只存 hash 到数据库）。"""
    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn = get_connection()
    try:
        cleanup_expired_sessions(conn)
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
            "WHERE s.token_hash=? AND s.expires_at > datetime('now') AND u.is_active=1",
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
            "WHERE s.token_hash=? AND s.expires_at > datetime('now') AND u.is_active=1",
            (token_hash(token),),
        ).fetchone())
    finally:
        conn.close()
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    """FastAPI 依赖：要求当前用户为管理员。"""
    if user["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_book_owner(conn, book_id: str, user: dict) -> dict:
    """校验当前用户对指定书籍的归属。越权时返回 404（不泄漏资源存在性）。"""
    book = row_to_dict(conn.execute(
        "SELECT * FROM books WHERE id=?", (book_id,)
    ).fetchone())
    if not book or (user["role"] != "admin" and book.get("owner_id") != user["id"]):
        raise HTTPException(404, "书籍不存在")
    return book


def require_chapter_owner(conn, chapter_id: str, user: dict) -> dict:
    """校验当前用户对指定章节的归属（通过 book.owner_id 间接鉴权）。"""
    chapter = row_to_dict(conn.execute(
        "SELECT c.* FROM chapters c JOIN books b ON b.id=c.book_id "
        "WHERE c.id=? AND (?='admin' OR b.owner_id=?)",
        (chapter_id, user["role"], user["id"])
    ).fetchone())
    if not chapter:
        raise HTTPException(404, "章节不存在")
    return chapter


def require_job_owner(conn, job_id: str, user: dict) -> dict:
    """校验当前用户对指定任务的归属。对 parse job 直接查 job.owner_id。"""
    job = row_to_dict(conn.execute(
        """SELECT j.* FROM jobs j
           LEFT JOIN chapters c ON j.chapter_id != '' AND j.chapter_id = c.id
           LEFT JOIN books b ON b.id = COALESCE(j.book_id, c.book_id)
         WHERE j.id=? AND (?='admin' OR b.owner_id=? OR j.owner_id=?)""",
        (job_id, user["role"], user["id"], user["id"])
    ).fetchone())
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


def bootstrap_admin() -> None:
    """启动时确保存在至少一个管理员账号。"""
    if not BOOTSTRAP_ADMIN_PASSWORD:
        logger.info("BOOTSTRAP_ADMIN_PASSWORD 未设置，跳过管理员自动创建")
        return
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not exists:
            uid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users(id,username,password_hash,role) VALUES(?,?,?,'admin')",
                (uid, BOOTSTRAP_ADMIN_USERNAME, hash_password(BOOTSTRAP_ADMIN_PASSWORD))
            )
            conn.commit()
            logger.info("已创建引导管理员: %s/%s", BOOTSTRAP_ADMIN_USERNAME, uid)
    finally:
        conn.close()
