"""Tests: user isolation — user A cannot access user B's resources."""

import io
import pytest
from fastapi.testclient import TestClient


def _admin_login(client):
    client.post("/api/auth/login", json={
        "username": "admin", "password": "Admin123456!",
    })


def _create_invite(client):
    resp = client.post("/api/auth/invites", json={"expires_in_days": 30})
    return resp.json()["invite_code"]


def _register_user(client, username, password="Test1234!"):
    _admin_login(client)
    code = _create_invite(client)
    client.post("/api/auth/logout")
    return client.post("/api/auth/register", json={
        "username": username, "password": password, "invite_code": code,
    })


def _login_as(client, username, password="Test1234!"):
    resp = client.post("/api/auth/login", json={
        "username": username, "password": password,
    })
    assert resp.status_code == 200


def _create_minimal_pdf():
    """Create a minimal PDF that fitz can open."""
    return io.BytesIO(b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
190
%%EOF""")


class TestIsolation:
    """用户隔离测试"""

    def _upload_book(self, client, content=None):
        if content is None:
            content = _create_minimal_pdf()
        return client.post(
            "/api/upload",
            files={"file": ("test.pdf", content, "application/pdf")},
        )

    def test_user_a_does_not_see_user_b_books(self, client):
        """用户 A 看不到用户 B 的书"""
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        assert resp_a.status_code == 200
        book_a_id = resp_a.json()["id"]

        books_a = client.get("/api/books").json()["books"]
        assert len(books_a) == 1
        assert books_a[0]["id"] == book_a_id

        client.post("/api/auth/logout")
        _register_user(client, "user_b")

        books_b = client.get("/api/books").json()["books"]
        assert len(books_b) == 0

    def test_user_a_cannot_get_user_b_book(self, client):
        """用户 A 不能获取用户 B 的书籍详情"""
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")

        _register_user(client, "user_b")
        resp = client.get(f"/api/books/{book_a_id}")
        assert resp.status_code == 404

    def test_user_a_cannot_get_user_b_progress(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.get(f"/api/books/{book_a_id}/progress")
        assert resp.status_code == 404

    def test_user_a_cannot_read_user_b_reader(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.get(f"/api/books/{book_a_id}/read")
        assert resp.status_code == 404

    def test_user_a_cannot_parse_user_b_book(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.post(f"/api/books/{book_a_id}/parse")
        assert resp.status_code == 404

    def test_user_a_cannot_delete_user_b_book(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.delete(f"/api/books/{book_a_id}")
        assert resp.status_code == 404

    def test_user_a_cannot_access_user_b_pdf(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.get(f"/api/books/{book_a_id}/pdf")
        assert resp.status_code == 404

    def test_user_a_cannot_access_user_b_assets(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.get(f"/api/books/{book_a_id}/assets/nonexistent.png")
        assert resp.status_code == 404

    def test_user_a_cannot_access_user_b_section_paragraphs(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.get(f"/api/books/{book_a_id}/sections/__full__/paragraphs")
        assert resp.status_code == 404

    def test_user_a_cannot_access_user_b_job(self, client):
        _register_user(client, "user_a")
        resp_a = self._upload_book(client)
        book_a_id = resp_a.json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "user_b")
        resp = client.get(f"/api/jobs/{book_a_id}")
        assert resp.status_code == 404
