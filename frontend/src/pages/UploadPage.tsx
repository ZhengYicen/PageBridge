import { useState, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, Book } from "../api/client";

export default function UploadPage() {
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [books, setBooks] = useState<Book[]>([]);
  const [loading, setLoading] = useState(true);
  const [parsingIds, setParsingIds] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  // 加载书籍列表，用 useCallback 避免重复 fetch
  const loadBooks = useCallback(async () => {
    try {
      const res = await api.listBooks();
      setBooks(res.books);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  // 初始化加载
  if (loading) {
    loadBooks();
  }

  // ── 上传 ──
  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const result = await api.upload(file);
      await loadBooks();
      navigate(`/books/${result.id}`);
    } catch (e: any) {
      alert("上传失败: " + e.message);
    } finally {
      setUploading(false);
    }
  };

  // ── 重新解析 ──
  const handleReparse = async (bookId: string) => {
    setParsingIds((prev) => new Set(prev).add(bookId));
    try {
      await api.parseBook(bookId);
      await loadBooks();
    } catch (e: any) {
      alert("解析失败: " + e.message);
      await loadBooks();
    } finally {
      setParsingIds((prev) => {
        const next = new Set(prev);
        next.delete(bookId);
        return next;
      });
    }
  };

  // ── 删除 ──
  const handleDelete = async (book: Book) => {
    if (!confirm(`确定删除「${book.title}」？\n关联的章节、翻译缓存和图片资源都会被清理。`)) {
      return;
    }
    try {
      await api.deleteBook(book.id);
      setBooks((prev) => prev.filter((b) => b.id !== book.id));
    } catch (e: any) {
      alert("删除失败: " + e.message);
    }
  };

  // ── 格式化时间 ──
  const formatTime = (iso: string | undefined | null) => {
    if (!iso) return "未知时间";
    try {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return "未知时间";
    }
  };

  // ── 解析状态配置 ──
  const statusConfig: Record<string, { label: string; bg: string; text: string }> = {
    pending:   { label: "未解析",  bg: "bg-gray-100", text: "text-gray-600" },
    parsing:   { label: "解析中",  bg: "bg-yellow-100", text: "text-yellow-700" },
    completed: { label: "已解析",  bg: "bg-green-100", text: "text-green-700" },
    failed:    { label: "解析失败", bg: "bg-red-100", text: "text-red-600" },
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
            {books.map((book) => {
              const isParsing = parsingIds.has(book.id) || book.parse_status === "parsing";
              const status = statusConfig[book.parse_status] || statusConfig.pending;

              return (
                <div
                  key={book.id}
                  className="bg-white rounded-xl p-4 shadow-sm border hover:shadow-md transition"
                >
                  <div className="flex items-center gap-4">
                    <span className="text-2xl">{book.format === "pdf" ? "📕" : "📘"}</span>

                    {/* 书名 + 元信息 */}
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{book.title}</p>
                      <p className="text-sm text-gray-400 mt-0.5">
                        {book.format.toUpperCase()}
                        {book.total_chapters > 0 && ` · ${book.total_chapters} 章`}
                        {` · 上传于 ${formatTime(book.uploaded_at || book.created_at)}`}
                      </p>
                    </div>

                    {/* 解析状态标签 */}
                    {isParsing ? (
                      <span className="text-xs px-2.5 py-1 rounded-full bg-yellow-100 text-yellow-700 shrink-0">
                        解析中...
                      </span>
                    ) : (
                      <span className={`text-xs px-2.5 py-1 rounded-full shrink-0 ${status.bg} ${status.text}`}>
                        {status.label}
                      </span>
                    )}
                  </div>

                  {/* 操作按钮 */}
                  <div className="flex gap-2 mt-3 pt-3 border-t border-gray-100">
                    <button
                      onClick={() => navigate(`/books/${book.id}`)}
                      className="px-4 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition"
                    >
                      打开
                    </button>
                    <button
                      onClick={() => handleReparse(book.id)}
                      disabled={isParsing}
                      className={`px-4 py-1.5 text-sm rounded-lg border transition
                        ${isParsing
                          ? "bg-gray-100 text-gray-400 cursor-not-allowed"
                          : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"
                        }`}
                    >
                      重新解析
                    </button>
                    <button
                      onClick={() => handleDelete(book)}
                      className="px-4 py-1.5 text-sm rounded-lg border border-red-200 text-red-500 hover:bg-red-50 transition"
                    >
                      删除
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 空状态 */}
      {!loading && books.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          还没有上传过书籍
        </div>
      )}
    </div>
  );
}
