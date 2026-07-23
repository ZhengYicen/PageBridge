"""应用配置，优先从环境变量读取，fallback 到默认值"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（在项目根目录）
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=True)
else:
    # 尝试加载 .env.example 作为兜底
    _example = _env_path.with_suffix(".env.example")
    if _example.exists():
        load_dotenv(_example, override=True)

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

# 支持的格式
SUPPORTED_FORMATS = {".pdf", ".epub"}

# ── Public deployment safety limits ──────────────────────
# 所有限制均可通过环境变量配置

# 上传限制
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
MAX_USER_STORAGE_BYTES = int(os.getenv("MAX_USER_STORAGE_MB", "500")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "1000"))
MAX_EPUB_FILES = int(os.getenv("MAX_EPUB_FILES", "10000"))
MAX_EPUB_UNCOMPRESSED_BYTES = int(os.getenv("MAX_EPUB_UNCOMPRESSED_MB", "500")) * 1024 * 1024
MAX_EPUB_COMPRESSION_RATIO = float(os.getenv("MAX_EPUB_COMPRESSION_RATIO", "200"))
# 单文件解压大小上限 (EPUB内单个文件)
MAX_EPUB_SINGLE_FILE_BYTES = int(os.getenv("MAX_EPUB_SINGLE_FILE_MB", "50")) * 1024 * 1024

# 翻译字符额度（每用户）
DAILY_TRANSLATION_CHARS = int(os.getenv("DAILY_TRANSLATION_CHARS", "200000"))
MONTHLY_TRANSLATION_CHARS = int(os.getenv("MONTHLY_TRANSLATION_CHARS", "2000000"))

# 并发任务限制
MAX_USER_TRANSLATE_JOBS = int(os.getenv("MAX_USER_TRANSLATE_JOBS", "1"))
MAX_GLOBAL_TRANSLATE_JOBS = int(os.getenv("MAX_GLOBAL_TRANSLATE_JOBS", "2"))

# 会话
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"

# CORS
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if x.strip()]

# 管理员引导
BOOTSTRAP_ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

# API 文档（生产环境建议关闭）
DOCS_ENABLED = os.getenv("DOCS_ENABLED", "true").lower() == "true"

# 安全响应头
SECURITY_HEADERS = {
    "X-Content-Type-Options": os.getenv("SEC_HDR_XCTO", "nosniff"),
    "X-Frame-Options": os.getenv("SEC_HDR_XFO", "DENY"),
    "X-XSS-Protection": os.getenv("SEC_HDR_XXSS", "0"),
    "Referrer-Policy": os.getenv("SEC_HDR_REFERRER", "strict-origin-when-cross-origin"),
}

# 简单进程内限流（多实例不共享，第一版够用）
RATE_LIMIT_LOGIN = int(os.getenv("RATE_LIMIT_LOGIN", "5"))       # 次/分钟
RATE_LIMIT_REGISTER = int(os.getenv("RATE_LIMIT_REGISTER", "3"))  # 次/分钟
RATE_LIMIT_UPLOAD = int(os.getenv("RATE_LIMIT_UPLOAD", "10"))     # 次/分钟
RATE_LIMIT_TRANSLATE = int(os.getenv("RATE_LIMIT_TRANSLATE", "5"))# 次/分钟
RATE_LIMIT_PARSE = int(os.getenv("RATE_LIMIT_PARSE", "3"))        # 次/分钟

# 翻译重试
TRANSLATE_MAX_RETRIES = int(os.getenv("TRANSLATE_MAX_RETRIES", "3"))
TRANSLATE_RETRY_BACKOFF = float(os.getenv("TRANSLATE_RETRY_BACKOFF", "2.0"))
TRANSLATE_API_TIMEOUT = int(os.getenv("TRANSLATE_API_TIMEOUT", "120"))

# 解析超时（秒）
PARSE_TIMEOUT = int(os.getenv("PARSE_TIMEOUT", "600"))
