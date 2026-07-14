import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, Book, BookProgress, Job } from "../api/client";

export default function BookPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();
  const [book, setBook] = useState<Book | null>(null);
  const [loading, setLoading] = useState(true);
  const [parsing, setParsing] = useState(false);
  const [progress, setProgress] = useState<BookProgress | null>(null);
  const [activeJobs, setActiveJobs] = useState<Record<string, string>>({});
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    if (!bookId) return;
    api.getBook(bookId).then((b) => {
      setBook(b);
      setLoading(false);
      // 如果正在解析中，自动开始轮询
      if (b.parse_status === "parsing" || b.parse_status === "assembling") {
        startPolling();
      }
    }).catch(() => {
      setLoading(false);
    });
    return () => stopPolling();
  }, [bookId]);

  const startPolling = () => {
    if (!bookId || pollRef.current) return;
    setParsing(true);
    pollRef.current = window.setInterval(async () => {
      try {
        const p = await api.getBookProgress(bookId!);
        setProgress(p);
        if (p.status === "completed" || p.status === "failed") {
          stopPolling();
          // 刷新书籍信息
          const b = await api.getBook(bookId!);
          setBook(b);
          setParsing(false);
        }
      } catch {
        // 忽略轮询错误
      }
    }, 1500);
  };

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const handleParse = async () => {
    if (!bookId) return;
    setParsing(true);
    setProgress(null);
    try {
      await api.parseBook(bookId);
      // 立即开始轮询进度
      startPolling();
    } catch (e: any) {
      alert("解析启动失败: " + e.message);
      setParsing(false);
    }
  };

  const handleTranslate = async (chapterId: string) => {
    try {
      const result = await api.translateChapter(chapterId);
      setActiveJobs((prev) => ({ ...prev, [chapterId]: result.job_id }));
      api.subscribeProgress(result.job_id, (data) => {
        if (["completed", "failed", "partial"].includes(data.status)) {
          if (bookId) api.getBook(bookId).then((b) => setBook(b));
          setActiveJobs((prev) => {
            const next = { ...prev };
            delete next[chapterId];
            return next;
          });
        }
      });
    } catch (e: any) {
      alert("翻译启动失败: " + e.message);
    }
  };

  const handlePauseResume = async (chapterId: string, jobId: string, currentStatus: string) => {
    try {
      if (currentStatus === "paused") {
        await api.resumeJob(jobId);
      } else {
        await api.pauseJob(jobId);
      }
    } catch (e: any) {
      alert("操作失败: " + e.message);
    }
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">加载中...</div>;
  }

  if (!book) {
    return <div className="text-center py-12 text-gray-400">书籍不存在</div>;
  }

  const isProcessing = book.parse_status === "parsing" || book.parse_status === "assembling";
  const isCompleted = book.parse_status === "completed";
  const isFailed = book.parse_status === "failed";

  return (
    <div className="space-y-6">
      {/* 书籍信息 */}
      <div className="bg-white rounded-2xl p-6 shadow-sm border">
        <div className="flex items-start gap-4">
          <span className="text-4xl">{book.format === "pdf" ? "📕" : "📘"}</span>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold">{book.title}</h1>
            <p className="text-gray-500 mt-1">
              {book.format.toUpperCase()} · {book.total_chapters || "?"} 章
              {book.total_pages > 0 && ` · ${book.total_pages} 页`}
              {book.author && ` · ${book.author}`}
            </p>
          </div>
          <button
            onClick={handleParse}
            disabled={parsing || isCompleted || isProcessing}
            className={`px-5 py-2 rounded-xl text-sm font-medium transition
              ${isCompleted
                ? "bg-green-100 text-green-700 cursor-default"
                : isProcessing || parsing
                ? "bg-yellow-100 text-yellow-700 cursor-wait"
                : isFailed
                ? "bg-red-100 text-red-700 hover:bg-red-200"
                : "bg-blue-600 text-white hover:bg-blue-700"
              }`}
          >
            {isProcessing
              ? (book.current_stage === "assembling" ? "组装中..." : "解析中...")
              : parsing
              ? "启动中..."
              : isCompleted
              ? "已解析"
              : isFailed
              ? "重新解析"
              : "解析书籍"}
          </button>
        </div>

        {/* 进度条 */}
        {(parsing || isProcessing) && progress && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-sm text-gray-500 mb-1">
              <span>
                {progress.current_stage === "assembling"
                  ? "正在组装章节..."
                  : `正在解析：${progress.parsed_pages} / ${progress.total_pages} 页`}
                {progress.failed_pages > 0 && `（${progress.failed_pages} 页失败）`}
              </span>
              <span>{progress.progress}%</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2.5">
              <div
                className="bg-blue-600 h-2.5 rounded-full transition-all duration-500"
                style={{ width: `${progress.progress}%` }}
              />
            </div>
          </div>
        )}

        {/* 失败信息 */}
        {isFailed && book.error_message && (
          <div className="mt-3 p-2 bg-red-50 text-red-600 text-sm rounded-lg">
            解析失败：{book.error_message}
          </div>
        )}
      </div>

      {/* 章节列表 */}
      <div>
        <h2 className="text-lg font-semibold mb-3">章节列表</h2>
        {isProcessing || parsing ? (
          <div className="bg-white rounded-xl p-8 text-center text-gray-400 border">
            {progress?.current_stage === "assembling"
              ? "正在组装章节，请稍候..."
              : `正在解析页面（${progress?.parsed_pages ?? 0} / ${progress?.total_pages ?? "?"}），完成后将自动显示章节...`}
          </div>
        ) : (!book.chapters || book.chapters.length === 0) ? (
          <div className="bg-white rounded-xl p-8 text-center text-gray-400 border">
            {isCompleted
              ? "暂无章节数据"
              : isFailed
              ? "解析失败，请重新解析"
              : "请先解析书籍"}
          </div>
        ) : (
          <div className="space-y-2">
            {book.chapters.map((ch) => {
              const jobId = activeJobs[ch.id];
              const isTranslating = !!jobId;
              const isTransCompleted = ch.translate_status === "completed";
              const isPartial = ch.translate_status === "partial";

              return (
                <div
                  key={ch.id}
                  className="bg-white rounded-xl p-4 shadow-sm border flex items-center gap-4 hover:shadow-md transition"
                >
                  <span className="text-lg font-mono text-gray-400 w-8">
                    {ch.chapter_order + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p
                      className="font-medium truncate cursor-pointer text-blue-600 hover:text-blue-800"
                      onClick={() => navigate(`/read/${ch.id}`)}
                    >
                      {ch.title}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {ch.paragraph_count} 段 ·
                      {isTransCompleted
                        ? " 已翻译"
                        : isPartial
                        ? " 部分完成"
                        : isTranslating
                        ? " 翻译中..."
                        : " 待翻译"}
                    </p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {isTranslating && (
                      <button
                        onClick={() => handlePauseResume(ch.id, jobId, "running")}
                        className="px-3 py-1.5 text-xs rounded-lg bg-yellow-100 text-yellow-700 hover:bg-yellow-200"
                      >
                        暂停
                      </button>
                    )}
                    {!isTransCompleted && !isTranslating && (
                      <button
                        onClick={() => handleTranslate(ch.id)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700"
                      >
                        翻译
                      </button>
                    )}
                    {isTransCompleted && (
                      <button
                        onClick={() => navigate(`/read/${ch.id}`)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-green-100 text-green-700 hover:bg-green-200"
                      >
                        阅读
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
