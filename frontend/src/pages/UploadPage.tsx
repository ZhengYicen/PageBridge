import { useState, useRef, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api, Book, UploadLimits } from "../api/client";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadPage() {
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [books, setBooks] = useState<Book[]>([]);
  const [loading, setLoading] = useState(true);
  const [parsingIds, setParsingIds] = useState<Set<string>>(new Set());
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [preCheckError, setPreCheckError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const loadBooks = useCallback(async () => {
    try {
      const res = await api.listBooks();
      setBooks(res.books);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLimits = useCallback(async () => {
    try {
      const data = await api.getLimits();
      setLimits(data);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    loadBooks();
    loadLimits();
  }, [loadBooks, loadLimits]);

  // ── 上传前检查 ──────────────────────────────
  const preCheckFile = (file: File): string | null => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (![".pdf", ".epub"].includes(ext)) {
      return "仅支持 PDF 和 EPUB 格式";
    }
    if (limits && file.size > limits.max_file_bytes) {
      return `文件过大（${formatBytes(file.size)}），单文件上限为 ${formatBytes(limits.max_file_bytes)}`;
    }
    if (limits && limits.used_storage_bytes + file.size > limits.max_storage_bytes) {
      return "存储空间不足，上传后将超过用户配额";
    }
    return null;
  };

  const handleUpload = async (file: File) => {
    const checkError = preCheckFile(file);
    if (checkError) {
      setPreCheckError(checkError);
      setTimeout(() => setPreCheckError(""), 5000);
      return;
    }

    setUploading(true);
    setPreCheckError("");
    try {
      const result = await api.upload(file);
      await loadBooks();
      await loadLimits();
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
    if (!confirm(`确定删除「${book.title}」？\n关联的章节、翻译缓存、图片资源和解析任务都会被清理。`)) {
      return;
    }
    try {
      await api.deleteBook(book.id);
      setBooks((prev) => prev.filter((b) => b.id !== book.id));
      await loadLimits();
    } catch (e: any) {
      alert("删除失败: " + e.message);
    }
  };

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

  return (
    <div className="space-y-8">
      {/* 存储限制提示 */}
      {limits && (
        <div className="bg-white rounded-xl px-5 py-3 shadow-sm border text-sm text-gray-500">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            <span>
              存储：<strong>{formatBytes(limits.used_storage_bytes)}</strong> / {formatBytes(limits.max_storage_bytes)}
            </span>
            <span>
              单文件上限：<strong>{formatBytes(limits.max_file_bytes)}</strong>
            </span>
            <span>PDF 页数上限：<strong>{limits.max_pdf_pages}</strong></span>
            <span className="text-xs text-gray-400">
              EPUB：最多 {limits.max_epub_files} 文件，解压上限 {formatBytes(limits.max_epub_uncompressed_bytes)}
            </span>
          </div>
          {/* 存储进度条 */}
          <div className="mt-2 w-full bg-gray-200 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all ${
                limits.used_storage_bytes / limits.max_storage_bytes > 0.9
                  ? "bg-red-500"
                  : "bg-blue-500"
              }`}
              style={{ width: `${Math.min(100, (limits.used_storage_bytes / limits.max_storage_bytes) * 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* 上传前错误提示 */}
      {preCheckError && (
        <div className="bg-red-50 text-red-600 text-sm px-4 py-2 rounded-lg border border-red-200">
          ⚠️ {preCheckError}
        </div>
      )}

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
          accept=".pdf,.epub"
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
              const statusLabel = isParsing ? "解析中..." : status.label;
              const statusBg = isParsing ? "bg-yellow-100" : status.bg;
              const statusText = isParsing ? "text-yellow-700" : status.text;

              return (
                <div
                  key={book.id}
                  className="bg-white rounded-xl px-5 py-4 shadow-sm border hover:shadow-md transition flex items-center gap-4"
                >
                  <span className="text-3xl shrink-0 self-center">
                    {book.format === "pdf" ? "📕" : "📘"}
                  </span>

                  <div className="flex-1 min-w-0 self-center">
                    <p
                      className="font-medium truncate text-gray-900 cursor-pointer hover:text-blue-600"
                      onClick={() => navigate(`/books/${book.id}`)}
                    >
                      {book.title}
                    </p>
                    <p className="text-sm text-gray-400 mt-0.5">
                      {book.format.toUpperCase()}
                      {book.total_chapters > 0 && ` · ${book.total_chapters} 章`}
                      {book.file_size && ` · ${formatBytes(book.file_size)}`}
                      {` · 上传于 ${formatTime(book.uploaded_at || book.created_at)}`}
                    </p>
                    <span className={`inline-block mt-1.5 text-xs px-2.5 py-0.5 rounded-full ${statusBg} ${statusText}`}>
                      {statusLabel}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 shrink-0 self-center">
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

      {!loading && books.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          还没有上传过书籍
        </div>
      )}
    </div>
  );
}
