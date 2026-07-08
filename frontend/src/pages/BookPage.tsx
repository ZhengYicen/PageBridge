import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, Book, Job } from "../api/client";

export default function BookPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();
  const [book, setBook] = useState<Book | null>(null);
  const [loading, setLoading] = useState(true);
  const [parsing, setParsing] = useState(false);
  const [activeJobs, setActiveJobs] = useState<Record<string, string>>({}); // chapterId -> jobId

  useEffect(() => {
    if (!bookId) return;
    api.getBook(bookId).then((b) => {
      setBook(b);
      setLoading(false);
    }).catch(() => {
      setLoading(false);
    });
  }, [bookId]);

  const handleParse = async () => {
    if (!bookId) return;
    setParsing(true);
    try {
      await api.parseBook(bookId);
      const b = await api.getBook(bookId);
      setBook(b);
    } catch (e: any) {
      alert("解析失败: " + e.message);
    } finally {
      setParsing(false);
    }
  };

  const handleTranslate = async (chapterId: string) => {
    try {
      const result = await api.translateChapter(chapterId);
      setActiveJobs((prev) => ({ ...prev, [chapterId]: result.job_id }));

      // 订阅进度
      api.subscribeProgress(result.job_id, (data) => {
        if (["completed", "failed", "partial"].includes(data.status)) {
          // 刷新书籍信息
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
              {book.author && ` · ${book.author}`}
            </p>
          </div>
          <button
            onClick={handleParse}
            disabled={parsing || book.parse_status === "completed"}
            className={`px-5 py-2 rounded-xl text-sm font-medium transition
              ${book.parse_status === "completed"
                ? "bg-green-100 text-green-700 cursor-default"
                : parsing
                ? "bg-yellow-100 text-yellow-700 cursor-wait"
                : "bg-blue-600 text-white hover:bg-blue-700"
              }`}
          >
            {parsing ? "解析中..." : book.parse_status === "completed" ? "已解析" : "解析书籍"}
          </button>
        </div>
      </div>

      {/* 章节列表 */}
      <div>
        <h2 className="text-lg font-semibold mb-3">章节列表</h2>
        {(!book.chapters || book.chapters.length === 0) ? (
          <div className="bg-white rounded-xl p-8 text-center text-gray-400 border">
            {book.parse_status === "completed"
              ? "暂无章节数据"
              : "请先解析书籍"}
          </div>
        ) : (
          <div className="space-y-2">
            {book.chapters.map((ch) => {
              const jobId = activeJobs[ch.id];
              const isTranslating = !!jobId;
              const isCompleted = ch.translate_status === "completed";
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
                      {isCompleted
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
                    {!isCompleted && !isTranslating && (
                      <button
                        onClick={() => handleTranslate(ch.id)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700"
                      >
                        翻译
                      </button>
                    )}
                    {isCompleted && (
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
