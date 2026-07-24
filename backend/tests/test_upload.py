"""Tests: upload security validation."""

import io
import zipfile
from backend.tests.conftest import _register_user
from pathlib import Path


def _create_minimal_pdf():
    return b"""%PDF-1.4
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
%%EOF"""


class TestUpload:
    def test_upload_unauthenticated(self, client):
        resp = client.post("/api/upload", files={"file": ("test.pdf", b"fake", "application/pdf")})
        assert resp.status_code == 401

    def test_upload_pdf_success(self, client):
        _register_user(client)
        resp = client.post("/api/upload", files={"file": ("test.pdf", _create_minimal_pdf(), "application/pdf")})
        assert resp.status_code == 200
        assert resp.json()["filename"] == "test.pdf"

    def test_upload_invalid_extension(self, client):
        _register_user(client)
        resp = client.post("/api/upload", files={"file": ("test.exe", b"x", "application/octet-stream")})
        assert resp.status_code == 400

    def test_upload_fake_pdf_rejected(self, client):
        _register_user(client)
        resp = client.post("/api/upload", files={"file": ("fake.pdf", b"Not a PDF", "application/pdf")})
        assert resp.status_code == 400

    def test_upload_exceeding_file_size(self, client):
        _register_user(client)
        huge = b"X" * (11 * 1024 * 1024)
        resp = client.post("/api/upload", files={"file": ("huge.pdf", huge, "application/pdf")})
        assert resp.status_code == 413

    def test_filename_cleanup(self, client):
        _register_user(client)
        resp = client.post("/api/upload", files={"file": ("../../bad.pdf", _create_minimal_pdf(), "application/pdf")})
        assert resp.status_code == 200
        assert "/" not in resp.json()["filename"]
        assert ".." not in resp.json()["filename"]
