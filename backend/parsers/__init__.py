from .epub_parser import EpubParser
from .pdf_parser import PdfParser

PARSERS = {
    ".epub": EpubParser,
    ".pdf": PdfParser,
}


def get_parser(file_path: str):
    """根据文件扩展名返回对应的解析器实例"""
    import os
    ext = os.path.splitext(file_path)[1].lower()
    cls = PARSERS.get(ext)
    if cls is None:
        raise ValueError(f"不支持的格式: {ext}")
    return cls()
