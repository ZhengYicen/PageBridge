"""FastAPI 应用入口"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routers import upload, books, chapters, jobs, paragraphs, auth
from backend.config import ALLOWED_ORIGINS


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    books.signal_shutdown()


app = FastAPI(
    title="PageBridge",
    description="上传英文书籍 → 解析结构 → 选择章节 → 自动翻译 → 双语对照阅读",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(jobs.router)
app.include_router(paragraphs.router)
app.include_router(auth.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
