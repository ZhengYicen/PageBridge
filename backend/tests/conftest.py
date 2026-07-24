"""Test configuration — uses a unique temporary SQLite database per test."""

import os
import sys
import tempfile
from pathlib import Path

os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["ALLOWED_ORIGINS"] = "http://testserver"
os.environ["MAX_UPLOAD_MB"] = "10"
os.environ["MAX_USER_STORAGE_MB"] = "50"

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import backend.database as db_mod
import pytest


@pytest.fixture(autouse=True)
def setup_db():
    _fd, _path = tempfile.mkstemp(suffix=".test.db")
    os.close(_fd)
    db_mod.DB_PATH = Path(_path)
    db_mod.init_db()
    yield Path(_path)
    Path(_path).unlink(missing_ok=True)
    for wal in [
        Path(_path).parent / f"{Path(_path).name}-wal",
        Path(_path).parent / f"{Path(_path).name}-shm",
    ]:
        wal.unlink(missing_ok=True)


@pytest.fixture
def client():
    from backend.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def _register_user(client, email="test@example.com", password="Test1234!"):
    """注册用户并返回 response（注册后自动登录）。"""
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _login(client, email="test@example.com", password="Test1234!"):
    """登录并返回 response。"""
    return client.post("/api/auth/login", json={"email": email, "password": password})
