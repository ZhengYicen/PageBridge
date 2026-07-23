"""Tests: upload limits, validation, security."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient


def _admin_login(client):
    client.post("/api/auth/login", json={
        "username": "admin", "password": "Admin123456!",
    })


def _create_invite_and_register(client, username="uploader"):
    _admin_login(client)
    resp = client.post("/api/auth/invites", json={"expires_in_days": 30})
    code = resp.json()["invite_code"]
    client.post("/api/auth/logout")
    resp2 = client.post("/api/auth/register", json={
        "username": username, "password": "Test1234!", "invite_code": code,
    })
    assert resp2.status_code == 200
    return resp2


def _create_minimal_pdf():
    """Create a minimal PDF that fitz can open."""
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
    """上传功能测试"""

    def test_upload_unauthenticated(self, client):
        """未登录无法上传"""
        resp = client.post(
            "/api/upload",
            files={"file": ("test.pdf", _create_minimal_pdf(), "application/pdf")},
        )
        assert resp.status_code == 401

    def test_upload_pdf_success(self, client):
        """上传有效的 PDF 成功"""
        _create_invite_and_register(client)
        resp = client.post(
            "/api/upload",
            files={"file": ("test.pdf", _create_minimal_pdf(), "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "test.pdf"
        assert data["format"] == "pdf"
        assert data["status"] == "uploaded"

    def test_upload_invalid_extension(self, client):
        """不支持的扩展名被拒绝"""
        _create_invite_and_register(client)
        resp = client.post(
            "/api/upload",
            files={"file": ("test.exe", b"fake content", "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_upload_fake_pdf_rejected(self, client):
        """伪造的 PDF 被拒绝（魔数校验）"""
        _create_invite_and_register(client)
        resp = client.post(
            "/api/upload",
            files={"file": ("fake.pdf", b"Not a PDF file", "application/pdf")},
        )
        assert resp.status_code == 400

    def test_upload_exceeding_file_size(self, client):
        """超过单文件大小限制被拒绝"""
        _create_invite_and_register(client)
        # Test config: MAX_UPLOAD_MB=10 → 10MB limit
        huge = b"X" * (11 * 1024 * 1024)
        resp = client.post(
            "/api/upload",
            files={"file": ("huge.pdf", huge, "application/pdf")},
        )
        assert resp.status_code == 413

    def test_upload_limits_endpoint(self, client):
        """/limits 接口返回正确信息"""
        _create_invite_and_register(client)
        resp = client.get("/api/limits")
        assert resp.status_code == 200
        data = resp.json()
        assert "max_file_bytes" in data
        assert "max_storage_bytes" in data
        assert "used_storage_bytes" in data

    def test_filename_cleanup(self, client):
        """恶意路径在文件名中被清除"""
        _create_invite_and_register(client)
        resp = client.post(
            "/api/upload",
            files={"file": ("../../bad.pdf", _create_minimal_pdf(), "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Path.name 已经去掉了路径部分，最终文件名应不含路径分隔符
        assert "/" not in data["filename"]
        assert "\\" not in data["filename"]
        assert ".." not in data["filename"]
