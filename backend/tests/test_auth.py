"""Tests: authentication, registration, invites, sessions."""

import pytest
from fastapi.testclient import TestClient


def _admin_login(client):
    """Helper: login as admin."""
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "Admin123456!",
    })
    assert resp.status_code == 200, f"admin login failed: {resp.json()}"
    # 确保 cookie 被提取
    _ = resp.cookies


def _create_invite(client):
    """Helper: create an invite code (must be logged in as admin)."""
    resp = client.post("/api/auth/invites", json={"expires_in_days": 30})
    assert resp.status_code == 200, f"create invite failed: {resp.json()}"
    return resp.json()["invite_code"]


def _register_user(client, username="newuser", password="Test1234!"):
    """Helper: full flow — admin login → create invite → register → return response."""
    _admin_login(client)
    code = _create_invite(client)
    client.post("/api/auth/logout")
    resp = client.post("/api/auth/register", json={
        "username": username,
        "password": password,
        "invite_code": code,
    })
    assert resp.status_code == 200, f"register failed: {resp.json()}"
    return resp


class TestAuth:
    """认证基础功能测试"""

    def test_login_success(self, client):
        """管理员使用正确密码登录成功"""
        resp = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "Admin123456!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"
        # 不返回密码
        assert "password" not in str(data).lower()

    def test_login_wrong_password(self, client):
        """错误密码返回 401"""
        resp = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrong_password",
        })
        assert resp.status_code == 401

    def test_login_inactive_user(self, client):
        """已禁用用户无法登录"""
        _register_user(client, "testuser")
        client.post("/api/auth/logout")

        # 登录 admin
        _admin_login(client)
        users_resp = client.get("/api/auth/users")
        assert users_resp.status_code == 200
        target = next(u for u in users_resp.json()["users"]
                      if u["username"] == "testuser")

        # 禁用
        disable_resp = client.patch(f"/api/auth/users/{target['id']}", json={
            "is_active": False,
        })
        assert disable_resp.status_code == 200
        client.post("/api/auth/logout")

        # testuser 尝试登录
        resp = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "Test1234!",
        })
        assert resp.status_code == 401

    def test_me_unauthenticated(self, client):
        """未登录访问 /me 返回 401"""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_authenticated(self, client):
        """登录后可获取当前用户信息"""
        _admin_login(client)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "admin"

    def test_logout_invalidates_session(self, client):
        """退出后 session 立即失效"""
        _admin_login(client)
        client.post("/api/auth/logout")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_register_with_invite(self, client):
        """使用有效邀请码注册成功"""
        resp = _register_user(client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["username"] == "newuser"
        assert data["user"]["role"] == "user"

        # 注册后自动登录
        me_resp = client.get("/api/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["user"]["username"] == "newuser"

    def test_register_without_invite(self, client):
        """没有邀请码无法注册"""
        resp = client.post("/api/auth/register", json={
            "username": "baduser",
            "password": "Test1234!",
            "invite_code": "invalid_code",
        })
        assert resp.status_code == 400

    def test_invite_one_time_use(self, client):
        """邀请码只能使用一次"""
        _admin_login(client)
        code = _create_invite(client)
        client.post("/api/auth/logout")

        # 第一次使用成功
        resp1 = client.post("/api/auth/register", json={
            "username": "user1", "password": "Test1234!", "invite_code": code,
        })
        assert resp1.status_code == 200
        client.post("/api/auth/logout")

        # 第二次使用同一邀请码失败
        resp2 = client.post("/api/auth/register", json={
            "username": "user2", "password": "Test1234!", "invite_code": code,
        })
        assert resp2.status_code == 400

    def test_register_duplicate_username(self, client):
        """重复用户名注册失败"""
        _register_user(client, "dupuser")
        client.post("/api/auth/logout")

        # 用新邀请码注册同用户名
        _admin_login(client)
        code2 = _create_invite(client)
        client.post("/api/auth/logout")
        resp = client.post("/api/auth/register", json={
            "username": "dupuser",
            "password": "Test1234!",
            "invite_code": code2,
        })
        assert resp.status_code == 400

    def test_regular_user_cannot_create_invite(self, client):
        """普通用户不能创建邀请码"""
        _register_user(client)
        resp = client.post("/api/auth/invites", json={"expires_in_days": 7})
        assert resp.status_code == 403

    def test_admin_can_list_users(self, client):
        """管理员可以查看用户列表"""
        _register_user(client)
        client.post("/api/auth/logout")
        _admin_login(client)

        resp = client.get("/api/auth/users")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert len(data["users"]) >= 2  # admin + newuser
        # 不包含密码
        assert "password_hash" not in str(data).lower()

    def test_regular_user_cannot_list_users(self, client):
        """普通用户不能查看用户列表"""
        _register_user(client)
        resp = client.get("/api/auth/users")
        assert resp.status_code == 403

    def test_admin_can_disable_user(self, client):
        """管理员可以禁用用户"""
        _register_user(client, "target_user")
        client.post("/api/auth/logout")

        _admin_login(client)
        users_resp = client.get("/api/auth/users")
        target = next(u for u in users_resp.json()["users"]
                      if u["username"] == "target_user")

        resp = client.patch(f"/api/auth/users/{target['id']}", json={
            "is_active": False,
        })
        assert resp.status_code == 200

        # 验证已禁用
        users_resp2 = client.get("/api/auth/users")
        target2 = next(u for u in users_resp2.json()["users"]
                       if u["username"] == "target_user")
        assert target2["is_active"] == 0

    def test_unauthenticated_access_to_books(self, client):
        """未登录无法访问书籍"""
        resp = client.get("/api/books")
        assert resp.status_code == 401
