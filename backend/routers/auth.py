import logging
import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.auth import (
    COOKIE_NAME, create_session, current_user, hash_password,
    normalize_email, token_hash, verify_password,
)
from backend.config import SESSION_COOKIE_SECURE
from backend.database import get_connection, row_to_dict

logger = logging.getLogger("pagebridge.auth.router")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1)


class RegisterBody(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "email": user.get("email", "")}


def _validate_email(email: str):
    """宽松邮箱校验：只检查包含 @。"""
    if "@" not in email:
        raise HTTPException(400, "邮箱必须包含 @")


def _has_username_column(conn) -> bool:
    """检查旧版 users 表是否有 username 列。"""
    try:
        conn.execute("SELECT username FROM users LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


@router.post("/login")
def login(body: LoginBody, response: Response):
    email = normalize_email(body.email)

    conn = get_connection()
    try:
        user = row_to_dict(conn.execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone())
    finally:
        conn.close()

    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "邮箱或密码错误")

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


@router.post("/register")
def register(body: RegisterBody, response: Response):
    email = normalize_email(body.email)
    _validate_email(email)

    if not body.password.strip():
        raise HTTPException(400, "密码不能为空")

    password_hash = hash_password(body.password)

    conn = get_connection()
    try:
        existing = conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            raise HTTPException(400, "该邮箱已注册")

        uid = str(uuid.uuid4())

        # 兼容旧版 users 表：如果存在 username 列，同步写入
        if _has_username_column(conn):
            conn.execute(
                "INSERT INTO users(id,email,username,password_hash) VALUES(?,?,?,?)",
                (uid, email, email, password_hash),
            )
        else:
            conn.execute(
                "INSERT INTO users(id,email,password_hash) VALUES(?,?,?)",
                (uid, email, password_hash),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        logger.error("注册失败: %s", exc)
        raise HTTPException(400, "注册失败，请稍后再试") from exc
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
    return {"user": {"id": uid, "email": email}}


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
