"""Test configuration — uses a unique temporary SQLite database per test.

IMPORTANT: Must set environment variables BEFORE any backend imports.
"""

import os
import sys
import tempfile
from pathlib import Path

# 1. 在所有后端导入之前，设置测试环境变量
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "Admin123456!"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["MAX_UPLOAD_MB"] = "10"
os.environ["MAX_USER_STORAGE_MB"] = "50"
os.environ["DAILY_TRANSLATION_CHARS"] = "10000"
os.environ["MONTHLY_TRANSLATION_CHARS"] = "100000"
os.environ["MAX_USER_TRANSLATE_JOBS"] = "2"
os.environ["MAX_GLOBAL_TRANSLATE_JOBS"] = "5"
os.environ["ALLOWED_ORIGINS"] = "http://testserver"
os.environ["DISABLE_RATE_LIMIT"] = "true"

# 2. 添加项目根目录到 Python 路径
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import backend.database as db_mod
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def setup_db():
    """每个测试前创建新的临时 SQLite 数据库。

    使用完全独立的数据库文件路径避免文件锁冲突。
    测试结束后清理所有相关文件。
    """
    # 创建临时数据库文件（unique）
    _fd, _path = tempfile.mkstemp(suffix=".test.db")
    os.close(_fd)  # 关闭文件描述符，只保留路径
    test_db_path = Path(_path)

    # 覆盖 database 模块的 DB_PATH
    db_mod.DB_PATH = test_db_path

    # 初始化数据库
    db_mod.init_db()

    yield test_db_path

    # 测试结束后清理
    test_db_path.unlink(missing_ok=True)
    for wal in [
        test_db_path.parent / f"{test_db_path.name}-wal",
        test_db_path.parent / f"{test_db_path.name}-shm",
    ]:
        wal.unlink(missing_ok=True)


@pytest.fixture
def client():
    from backend.main import app
    with TestClient(app) as c:
        yield c
