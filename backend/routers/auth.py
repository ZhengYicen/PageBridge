import hashlib
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.auth import (
    COOKIE_NAME, admin_user, create_session, current_user,
    hash_password, optional_user, token_hash, verify_password,
)
from backend.config import (
    BOOTSTRAP_ADMIN_USERNAME, RATE_LIMIT_LOGIN, RATE_LIMIT_REGISTER,
    SESSION_COOKIE_SECURE,
)
from backend.database import get_connection, row_to_dict, rows_to_list

logger = logging.getLogger("pagebridge.auth.router")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── 进程内简单限流（多实例不共享，第一版够用） ──────────
# 测试时可通过环境变量 DISABLE_RATE_LIMIT=true 关闭限流
_DISABLE_RATE_LIMIT = os.getenv("DISABLE_RATE_LIMIT", "").lower() == "true"
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str, max_count: int, window_seconds: int = 60) -> None:
    if _DISABLE_RATE_LIMIT:
        return
    now = time.monotonic()
    records = _rate_limit_store[key]
    cutoff = now - window_seconds
    _rate_limit_store[key] = [t for t in records if t > cutoff]
    if len(_rate_limit_store[key]) >= max_count:
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    _rate_limit_store[key].append(now)


# ── Pydantic 模型 ────────────────────────────────────────


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class RegisterBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_一-鿿]+$")
    password: str = Field(..., min_length=8, max_length=128)
    invite_code: str = Field(..., min_length=1)


class CreateInviteBody(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=365)


class UserUpdateBody(BaseModel):
    is_active: bool | None = None


# ── 辅助函数 ─────────────────────────────────────────────


def _public_user(user: dict) -> dict:
    """安全地返回用户公开信息（绝不返回 password_hash 或 token）。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
    }


# ── 接口 ─────────────────────────────────────────────────


@router.post("/login")
def login(body: LoginBody, response: Response):
    _check_rate_limit(f"login:{body.username}", RATE_LIMIT_LOGIN)

    conn = get_connection()
    try:
        user = row_to_dict(conn.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1",
            (body.username.strip(),),
        ).fetchone())
    finally:
        conn.close()

    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")

    token = create_session(user["id"])
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return {"user": _public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
            conn.commit()
        finally:
            conn.close()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"user": _public_user(user)}


@router.post("/register")
def register(body: RegisterBody, response: Response):
    _check_rate_limit(f"register:{body.username}", RATE_LIMIT_REGISTER)

    code_hash = hashlib.sha256(body.invite_code.strip().encode()).hexdigest()
    conn = get_connection()
    try:
        invite = row_to_dict(conn.execute(
            "SELECT * FROM invites WHERE code_hash=? AND used_by IS NULL "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (code_hash,),
        ).fetchone())
        if not invite:
            raise HTTPException(400, "邀请码无效或已使用")

        uid = str(uuid.uuid4())
        username = body.username.strip()
        password_hash = hash_password(body.password)

        conn.execute(
            "INSERT INTO users(id,username,password_hash) VALUES(?,?,?)",
            (uid, username, password_hash),
        )
        # 原子消费邀请码（WHERE used_by IS NULL 保证并发安全）
        updated = conn.execute(
            "UPDATE invites SET used_by=? WHERE id=? AND used_by IS NULL",
            (uid, invite["id"]),
        ).rowcount
        if updated == 0:
            # 并发冲突：邀请码已被其他请求消费
            conn.rollback()
            raise HTTPException(400, "邀请码已被使用")

        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(400, "用户名已存在或密码不符合要求") from exc
    finally:
        conn.close()

    token = create_session(uid)
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return {"user": {"id": uid, "username": body.username.strip(), "role": "user"}}


@router.post("/invites")
def create_invite(body: CreateInviteBody = CreateInviteBody(), user: dict = Depends(admin_user)):
    raw = secrets.token_urlsafe(18)
    code_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO invites(id,code_hash,created_by,expires_at) VALUES(?,?,?,?)",
            (str(uuid.uuid4()), code_hash, user["id"], expires),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "invite_code": raw,
        "expires_in_days": body.expires_in_days,
    }


@router.get("/users")
def list_users(user: dict = Depends(admin_user)):
    """管理员查看所有用户及其用量统计。"""
    conn = get_connection()
    try:
        rows = rows_to_list(conn.execute("""
            SELECT
                u.id, u.username, u.role, u.is_active, u.created_at,
                COALESCE(SUM(b.file_size), 0) AS storage_bytes,
                COUNT(DISTINCT b.id) AS book_count
            FROM users u
            LEFT JOIN books b ON b.owner_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at
        """).fetchall())
    finally:
        conn.close()

    # 补充翻译用量（每个用户每日+每月）
    for r in rows:
        conn = get_connection()
        try:
            daily = conn.execute(
                "SELECT COALESCE(SUM(characters),0) FROM translation_usage "
                "WHERE user_id=? AND created_at>=datetime('now','-1 day')",
                (r["id"],),
            ).fetchone()[0]
            monthly = conn.execute(
                "SELECT COALESCE(SUM(characters),0) FROM translation_usage "
                "WHERE user_id=? AND created_at>=datetime('now','start of month')",
                (r["id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        r["daily_translation_chars"] = daily
        r["monthly_translation_chars"] = monthly

    return {"users": rows}


@router.patch("/users/{target_id}")
def update_user(target_id: str, body: UserUpdateBody, user: dict = Depends(admin_user)):
    """管理员禁用/启用用户。"""
    conn = get_connection()
    try:
        target = row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id=?", (target_id,)
        ).fetchone())
        if not target:
            raise HTTPException(404, "用户不存在")
        if target["role"] == "admin" and target["id"] != user["id"]:
            raise HTTPException(403, "不能修改其他管理员的状态")

        if body.is_active is not None:
            conn.execute(
                "UPDATE users SET is_active=? WHERE id=?",
                (1 if body.is_active else 0, target_id),
            )
            # 如果禁用用户，立即使其所有 session 失效
            if not body.is_active:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (target_id,))
            conn.commit()
            logger.info("管理员 %s 修改了用户 %s 状态: is_active=%s",
                        user["id"], target_id, body.is_active)
    finally:
        conn.close()

    return {"status": "updated"}


@router.get("/usage")
def admin_usage_stats(user: dict = Depends(admin_user)):
    """管理员查询全站翻译用量统计。"""
    conn = get_connection()
    try:
        daily_global = conn.execute(
            "SELECT COALESCE(SUM(characters),0) FROM translation_usage "
            "WHERE created_at>=datetime('now','-1 day')",
        ).fetchone()[0]
        monthly_global = conn.execute(
            "SELECT COALESCE(SUM(characters),0) FROM translation_usage "
            "WHERE created_at>=datetime('now','start of month')",
        ).fetchone()[0]
        active_jobs = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        total_users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "daily_translation_chars": daily_global,
        "monthly_translation_chars": monthly_global,
        "active_jobs": active_jobs,
        "total_users": total_users,
    }
