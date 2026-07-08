"""应用配置，优先从环境变量读取，fallback 到默认值"""

import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据库
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'storage' / 'app.db'}")

# 文件存储
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# LLM 配置
LLM_CONFIG = {
    "provider": os.getenv("LLM_PROVIDER", "deepseek"),
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "model": os.getenv("LLM_MODEL", "deepseek-chat"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
}

# MinerU 配置
MINERU_URL = os.getenv("MINERU_URL", "http://localhost:8000")

# 支持的格式
SUPPORTED_FORMATS = {".pdf", ".epub"}
