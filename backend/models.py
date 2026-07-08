"""Pydantic 请求/响应模型"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# --- 书籍 ---
class BookResponse(BaseModel):
    id: str
    title: str
    author: str
    format: str
    file_path: str
    parse_status: str
    total_chapters: int
    created_at: str


class BookListResponse(BaseModel):
    books: list[BookResponse]


# --- 章节 ---
class ChapterResponse(BaseModel):
    id: str
    book_id: str
    title: str
    chapter_order: int
    paragraph_count: int
    translate_status: str
    created_at: str


class ChapterListResponse(BaseModel):
    chapters: list[ChapterResponse]


# --- 段落 ---
class ParagraphResponse(BaseModel):
    id: str
    chapter_id: str
    paragraph_order: int
    source_text: str
    source_html: Optional[str] = ""
    page_number: int = 0
    source_bbox: Optional[str] = ""
    translation: Optional[str] = ""
    status: str
    error_message: Optional[str] = ""
    updated_at: str


class ParagraphListResponse(BaseModel):
    paragraphs: list[ParagraphResponse]


# --- 任务 ---
class JobResponse(BaseModel):
    id: str
    chapter_id: str
    status: str
    total_paragraphs: int
    completed_paragraphs: int
    failed_paragraphs: int
    job_type: str
    created_at: str
    updated_at: str


class JobProgressResponse(BaseModel):
    """SSE 推送的进度"""
    job_id: str
    chapter_id: str
    status: str
    total: int
    completed: int
    failed: int
    current_paragraph: Optional[str] = ""


# --- 操作请求 ---
class TranslateRequest(BaseModel):
    """启动翻译请求"""
    pass  # 未来可加 language_pair 等


# --- 解析结果 ---
class ParseResult(BaseModel):
    """解析一本书后的结果摘要"""
    book_id: str
    chapters: int
    paragraphs: int
    status: str
