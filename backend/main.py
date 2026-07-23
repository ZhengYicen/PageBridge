"""FastAPI 应用入口"""

import sys
import logging
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 日志配置 — 解析、翻译进度都会显示
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.database import init_db
from backend.routers import upload, books, chapters, jobs, paragraphs, auth
from backend.auth import bootstrap_admin
from backend.config import ALLOWED_ORIGINS, DOCS_ENABLED, SECURITY_HEADERS


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    init_db()
    bootstrap_admin()
    yield
    # 关闭时通知后台解析线程退出
    books.signal_shutdown()


app = FastAPI(
    title="PageBridge",
    description="上传英文书籍 → 解析结构 → 选择章节 → 自动翻译 → 双语对照阅读",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
)

# CORS — 允许前端开发服务器跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 安全响应头中间件 ──────────────────────────────
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


# ── 注册路由 ──────────────────────────────────────
app.include_router(upload.router)
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(jobs.router)
app.include_router(paragraphs.router)
app.include_router(auth.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
