"""Tests: email registration, login, logout, session."""

import pytest
from backend.tests.conftest import _register_user, _login


class TestAuth:
    def test_register_success(self, client):
        resp = _register_user(client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["email"] == "test@example.com"

        # 注册后自动登录
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] == "test@example.com"

    def test_register_duplicate_email(self, client):
        _register_user(client)
        resp2 = _register_user(client)  # same email
        assert resp2.status_code == 400

    def test_register_then_logout(self, client):
        _register_user(client)
        # 退出
        client.post("/api/auth/logout")
        me = client.get("/api/auth/me")
        assert me.status_code == 401

    def test_login_success(self, client):
        _register_user(client)
        client.post("/api/auth/logout")
        resp = _login(client)
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "test@example.com"

    def test_login_wrong_password(self, client):
        _register_user(client)
        client.post("/api/auth/logout")
        resp = client.post("/api/auth/login", json={
            "email": "test@example.com", "password": "wrong",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_email(self, client):
        resp = client.post("/api/auth/login", json={
            "email": "nobody@example.com", "password": "Test1234!",
        })
        assert resp.status_code == 401

    def test_logout_invalidates_session(self, client):
        _register_user(client)
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401

    def test_email_normalization(self, client):
        """邮箱大小写和空格应被统一处理。"""
        _register_user(client, email="  Test@Example.COM ")
        # 用小写登录
        resp = _login(client, email="test@example.com")
        assert resp.status_code == 200

    def test_me_unauthenticated(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_register_empty_password_rejected(self, client):
        resp = client.post("/api/auth/register", json={
            "email": "empty@test.com", "password": "",
        })
        assert resp.status_code in (400, 422)
        """密码只要非空即可，没有最小长度限制。"""
        resp = client.post("/api/auth/register", json={
            "email": "short@test.com", "password": "1",
        })
        assert resp.status_code == 200

    def test_unauthenticated_cannot_upload(self, client):
        resp = client.post("/api/upload", files={"file": ("test.pdf", b"fake", "application/pdf")})
        assert resp.status_code == 401
