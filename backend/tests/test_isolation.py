"""Tests: user isolation — user A cannot access user B's resources."""

import io
import pytest
from backend.tests.conftest import _register_user


def _create_minimal_pdf():
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
    def _upload_book(self, client):
        return client.post("/api/upload", files={"file": ("test.pdf", _create_minimal_pdf(), "application/pdf")})

    def test_user_a_does_not_see_user_b_books(self, client):
        _register_user(client, "a@test.com")
        resp = self._upload_book(client)
        assert resp.status_code == 200
        book_a_id = resp.json()["id"]
        assert len(client.get("/api/books").json()["books"]) == 1

        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert len(client.get("/api/books").json()["books"]) == 0

    def test_user_a_cannot_get_user_b_book(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.get(f"/api/books/{book_a_id}").status_code == 404

    def test_user_a_cannot_get_user_b_progress(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.get(f"/api/books/{book_a_id}/progress").status_code == 404

    def test_user_a_cannot_parse_user_b_book(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.post(f"/api/books/{book_a_id}/parse").status_code == 404

    def test_user_a_cannot_delete_user_b_book(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.delete(f"/api/books/{book_a_id}").status_code == 404

    def test_user_a_cannot_access_user_b_pdf(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.get(f"/api/books/{book_a_id}/pdf").status_code == 404

    def test_user_a_cannot_access_user_b_section_paragraphs(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.get(f"/api/books/{book_a_id}/sections/__full__/paragraphs").status_code == 404

    def test_user_a_cannot_access_user_b_read(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.get(f"/api/books/{book_a_id}/read").status_code == 404

    def test_user_a_cannot_access_user_b_job(self, client):
        _register_user(client, "a@test.com")
        book_a_id = self._upload_book(client).json()["id"]
        client.post("/api/auth/logout")
        _register_user(client, "b@test.com")
        assert client.get(f"/api/jobs/{book_a_id}").status_code == 404
