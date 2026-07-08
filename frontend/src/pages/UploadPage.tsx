import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

export default function UploadPage() {
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [books, setBooks] = useState<any[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const loaded = useRef(false);

  // 页面加载时取书籍列表
  if (!loaded.current) {
    loaded.current = true;
    api.listBooks().then((res) => setBooks(res.books)).catch(() => {});
  }

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const result = await api.upload(file);
      // 刷新列表
      const res = await api.listBooks();
      setBooks(res.books);
      // 跳转到书籍详情
      navigate(`/books/${result.id}`);
    } catch (e: any) {
      alert("上传失败: " + e.message);
    } finally {
      setUploading(false);
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleUpload(file);
  };

  const allowedTypes = [".pdf", ".epub"];

  return (
    <div className="space-y-8">
      {/* 上传区域 */}
      <div
        className={`border-2 border-dashed rounded-2xl p-16 text-center transition-colors cursor-pointer
          ${dragOver ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-blue-400"}
        `}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept={allowedTypes.join(",")}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleUpload(file);
          }}
        />
        <div className="text-5xl mb-4">{uploading ? "⏳" : "📂"}</div>
        <p className="text-xl text-gray-600 mb-2">
          {uploading ? "上传中..." : "拖拽 PDF 或 EPUB 到此处，或点击选择文件"}
        </p>
        <p className="text-sm text-gray-400">支持 PDF、EPUB 格式</p>
      </div>

      {/* 已上传书籍列表 */}
      {books.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold mb-3">已上传的书籍</h2>
          <div className="grid gap-3">
            {books.map((book) => (
              <div
                key={book.id}
                className="bg-white rounded-xl p-4 shadow-sm border flex items-center gap-4 hover:shadow-md cursor-pointer"
                onClick={() => navigate(`/books/${book.id}`)}
              >
                <span className="text-2xl">{book.format === "pdf" ? "📕" : "📘"}</span>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{book.title}</p>
                  <p className="text-sm text-gray-400">
                    {book.format.toUpperCase()} ·{" "}
                    {book.parse_status === "completed"
                      ? `${book.total_chapters} 章`
                      : book.parse_status === "parsing"
                      ? "解析中..."
                      : "待解析"}
                  </p>
                </div>
                <span className={`text-xs px-2 py-1 rounded-full ${
                  book.parse_status === "completed"
                    ? "bg-green-100 text-green-700"
                    : book.parse_status === "parsing"
                    ? "bg-yellow-100 text-yellow-700"
                    : "bg-gray-100 text-gray-500"
                }`}>
                  {book.parse_status === "completed" ? "已解析" : book.parse_status === "parsing" ? "解析中" : "待解析"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
