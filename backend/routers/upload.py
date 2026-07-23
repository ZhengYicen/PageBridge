"""Authenticated streaming uploads with hard resource limits."""

import logging
import re
import uuid
import zipfile
from pathlib import Path

import fitz
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from backend.auth import current_user
from backend.config import (
    MAX_EPUB_COMPRESSION_RATIO, MAX_EPUB_FILES, MAX_EPUB_SINGLE_FILE_BYTES,
    MAX_EPUB_UNCOMPRESSED_BYTES, MAX_PDF_PAGES, MAX_UPLOAD_BYTES,
    MAX_USER_STORAGE_BYTES, SUPPORTED_FORMATS, UPLOAD_DIR,
)
from backend.database import get_connection

logger = logging.getLogger("pagebridge.upload")

router = APIRouter(prefix="/api", tags=["upload"])
CHUNK_SIZE = 1024 * 1024


def clean_filename(name: str | None) -> str:
    """清理文件名：移除不安全字符，限制长度。"""
    name = Path(name or "book").name
    name = re.sub(r"[^\w.()\-一-鿿 ]+", "_", name, flags=re.UNICODE).strip(" .")
    return name[:180] or "book"


def validate_pdf(path: Path) -> int:
    """验证 PDF 文件：魔数 + PyMuPDF 打开检查。只读取前 5 字节，不读取整个文件。"""
    with path.open("rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        raise ValueError("文件内容不是有效 PDF")
    try:
        with fitz.open(path) as doc:
            pages = len(doc)
            if pages <= 0:
                raise ValueError("PDF 文件为空")
            if pages > MAX_PDF_PAGES:
                raise ValueError(f"PDF 页数不得超过 {MAX_PDF_PAGES}")
            return pages
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 文件损坏或无法读取") from exc


def validate_epub(path: Path) -> int:
    """验证 EPUB 文件：ZIP 结构、mimetype、路径穿越、文件数、解压大小、压缩比。"""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()

            if len(infos) > MAX_EPUB_FILES:
                raise ValueError(f"EPUB 内文件数量 ({len(infos)}) 超过限制 ({MAX_EPUB_FILES})")

            total_uncompressed = 0
            total_compressed = 0

            for info in infos:
                # 路径穿越检测（同时兼容 POSIX 和 Windows）
                norm_path = info.filename.replace("\\", "/")
                if norm_path.startswith("/") or ".." in Path(norm_path).parts:
                    raise ValueError(f"EPUB 包含不安全路径: {info.filename}")

                # 单文件解压后大小限制
                if info.file_size > MAX_EPUB_SINGLE_FILE_BYTES:
                    raise ValueError(
                        f"EPUB 内文件 {info.filename} 解压后大小 ({info.file_size}) "
                        f"超过单文件限制 ({MAX_EPUB_SINGLE_FILE_BYTES})"
                    )

                total_uncompressed += info.file_size
                total_compressed += info.compress_size

            # 总解压大小限制
            if total_uncompressed > MAX_EPUB_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"EPUB 解压后总大小 ({total_uncompressed}) 超过限制 ({MAX_EPUB_UNCOMPRESSED_BYTES})"
                )

            # 压缩比检测（ZIP Bomb 防护）
            if total_compressed > 0:
                ratio = total_uncompressed / total_compressed
                if ratio > MAX_EPUB_COMPRESSION_RATIO:
                    raise ValueError(
                        f"EPUB 压缩比 ({ratio:.1f}:1) 超过限制，疑似 ZIP Bomb"
                    )

            # 验证 mimetype
            try:
                mimetype = archive.read("mimetype")
            except KeyError as exc:
                raise ValueError("文件内容不是有效 EPUB（缺少 mimetype）") from exc
            if mimetype.strip() != b"application/epub+zip":
                raise ValueError("文件内容不是有效 EPUB（mimetype 不正确）")

    except zipfile.BadZipFile as exc:
        raise ValueError("EPUB 文件损坏或无法读取") from exc

    return 0


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(current_user)):
    display_name = clean_filename(file.filename)
    ext = Path(display_name).suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(400, "仅支持 PDF 和 EPUB")

    # 检查用户存储配额
    conn = get_connection()
    try:
        used = conn.execute(
            "SELECT COALESCE(SUM(file_size),0) n FROM books WHERE owner_id=?",
            (user["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()

    if used >= MAX_USER_STORAGE_BYTES:
        raise HTTPException(413, "用户存储空间已用完")

    file_id = str(uuid.uuid4())
    # 先写入 .part 临时文件，验证成功后再重命名
    temp_path = UPLOAD_DIR / f"{file_id}{ext}.part"
    final_path = UPLOAD_DIR / f"{file_id}{ext}"
    size = 0

    try:
        # ── 流式写入临时文件 ───────────────────────
        with temp_path.open("xb") as output:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"文件大小 ({size}) 超过单文件限制 ({MAX_UPLOAD_BYTES})")
                if used + size > MAX_USER_STORAGE_BYTES:
                    raise HTTPException(413, "上传后总存储将超过用户配额")
                output.write(chunk)

        # ── 内容验证 ──────────────────────────────
        try:
            if ext == ".pdf":
                total_pages = validate_pdf(temp_path)
            else:
                total_pages = validate_epub(temp_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        # ── 验证通过，原子重命名 ──────────────────
        temp_path.rename(final_path)

        # ── 写入数据库 ────────────────────────────
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO books(id,title,format,file_path,uploaded_at,owner_id,file_size,total_pages) "
                "VALUES(?,?,?,?,datetime('now'),?,?,?)",
                (file_id, display_name, ext[1:], str(final_path), user["id"], size, total_pages),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except HTTPException:
        # HTTPException（如 413）仍需清理临时文件
        temp_path.unlink(missing_ok=True)
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        logger.exception("上传失败，已清理临时文件: %s", temp_path)
        raise
    finally:
        await file.close()

    return {
        "id": file_id,
        "filename": display_name,
        "format": ext[1:],
        "size": size,
        "total_pages": total_pages,
        "status": "uploaded",
    }


@router.get("/limits")
def upload_limits(user: dict = Depends(current_user)):
    conn = get_connection()
    try:
        used = conn.execute(
            "SELECT COALESCE(SUM(file_size),0) n FROM books WHERE owner_id=?",
            (user["id"],),
        ).fetchone()["n"]
    finally:
        conn.close()
    return {
        "max_file_bytes": MAX_UPLOAD_BYTES,
        "max_storage_bytes": MAX_USER_STORAGE_BYTES,
        "used_storage_bytes": used,
        "max_pdf_pages": MAX_PDF_PAGES,
        "max_epub_files": MAX_EPUB_FILES,
        "max_epub_uncompressed_bytes": MAX_EPUB_UNCOMPRESSED_BYTES,
        "max_epub_single_file_bytes": MAX_EPUB_SINGLE_FILE_BYTES,
        "max_epub_compression_ratio": MAX_EPUB_COMPRESSION_RATIO,
    }
