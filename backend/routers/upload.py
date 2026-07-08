"""上传路由"""

import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

from backend.config import UPLOAD_DIR, SUPPORTED_FORMATS
from backend.database import get_connection

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传 EPUB 或 PDF 文件"""
    # 验证格式
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(400, f"不支持的格式: {ext}，仅支持 {SUPPORTED_FORMATS}")

    # 保存文件
    file_id = str(uuid.uuid4())
    save_name = f"{file_id}{ext}"
    save_path = UPLOAD_DIR / save_name

    content = await file.read()
    save_path.write_bytes(content)

    # 创建书籍记录
    book_id = file_id
    conn = get_connection()
    conn.execute(
        "INSERT INTO books (id, title, format, file_path) VALUES (?,?,?,?)",
        (book_id, file.filename, ext.lstrip("."), str(save_path)),
    )
    conn.commit()
    conn.close()

    return {
        "id": book_id,
        "filename": file.filename,
        "format": ext.lstrip("."),
        "size": len(content),
        "status": "uploaded",
    }
