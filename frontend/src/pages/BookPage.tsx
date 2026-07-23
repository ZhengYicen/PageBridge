import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, Book, BookProgress, Job, TranslationEstimate } from "../api/client";

export default function BookPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();
  const [book, setBook] = useState<Book | null>(null);
  const [loading, setLoading] = useState(true);
  const [parsing, setParsing] = useState(false);
  const [progress, setProgress] = useState<BookProgress | null>(null);
  const [activeJobs, setActiveJobs] = useState<Record<string, { jobId: string; status: string }>>({});
  const [estimates, setEstimates] = useState<Record<string, TranslationEstimate>>({});
  const [estimatingCh, setEstimatingCh] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    if (!bookId) return;
    api.getBook(bookId).then((b) => {
      setBook(b);
      setLoading(false);
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
          const b = await api.getBook(bookId!);
          setBook(b);
          setParsing(false);
        }
      } catch { /* ignore */ }
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
      startPolling();
    } catch (e: any) {
      alert("解析启动失败: " + e.message);
      setParsing(false);
    }
  };

  // ── 翻译流程：先估算 → 显示确认 → 带 confirmed=true 提交 ──
  const [confirmDialog, setConfirmDialog] = useState<{
    chapterId: string;
    estimate: TranslationEstimate;
  } | null>(null);

  const handleTranslateClick = async (chapterId: string) => {
    setEstimatingCh(chapterId);
    try {
      const est = await api.getTranslationEstimate(chapterId);
      setEstimates((prev) => ({ ...prev, [chapterId]: est }));
      if (!est.allowed) {
        if (est.characters === 0) {
          alert("该章节没有待翻译的段落");
        } else {
          alert("翻译额度不足，请查看详情后重试");
        }
        return;
      }
      setConfirmDialog({ chapterId, estimate: est });
    } catch (e: any) {
      alert("估算失败: " + e.message);
    } finally {
      setEstimatingCh(null);
    }
  };

  const handleConfirmTranslate = async () => {
    if (!confirmDialog) return;
    const { chapterId } = confirmDialog;
    setConfirmDialog(null);

    try {
      const result = await api.translateChapter(chapterId, true);
      setActiveJobs((prev) => ({
        ...prev,
        [chapterId]: { jobId: result.job_id, status: "queued" },
      }));
      // 轮询进度
      api.subscribeProgress(result.job_id, (data) => {
        if (["completed", "failed", "partial"].includes(data.status)) {
          if (bookId) api.getBook(bookId).then((b) => setBook(b));
          setActiveJobs((prev) => {
            const next = { ...prev };
            delete next[chapterId];
            return next;
          });
        } else {
          setActiveJobs((prev) => ({
            ...prev,
            [chapterId]: { jobId: result.job_id, status: data.status },
          }));
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
        setActiveJobs((prev) => ({ ...prev, [chapterId]: { jobId, status: "queued" } }));
      } else {
        await api.pauseJob(jobId);
        setActiveJobs((prev) => ({ ...prev, [chapterId]: { jobId, status: "paused" } }));
      }
    } catch (e: any) {
      alert("操作失败: " + e.message);
    }
  };

  const handleRetry = async (chapterId: string) => {
    const job = activeJobs[chapterId];
    if (!job) return;
    try {
      await api.retryJob(job.jobId);
      setActiveJobs((prev) => ({ ...prev, [chapterId]: { jobId: job.jobId, status: "queued" } }));
      api.subscribeProgress(job.jobId, (data) => {
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
      alert("重试失败: " + e.message);
    }
  };

  const getJobDetails = (chapterId: string) => {
    const j = activeJobs[chapterId];
    if (!j) return null;
    const statusMap: Record<string, string> = {
      queued: "排队中",
      running: "翻译中...",
      paused: "已暂停",
      partial: "部分完成",
      completed: "已完成",
      failed: "失败",
    };
    return statusMap[j.status] || j.status;
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
              const jobInfo = activeJobs[ch.id];
              const isTranslating = !!jobInfo;
              const isTransCompleted = ch.translate_status === "completed";
              const isPartial = ch.translate_status === "partial";
              const isFailedTranslate = ch.translate_status === "failed";
              const showRetry = isFailedTranslate || ch.translate_status === "partial";
              const jobDetails = getJobDetails(ch.id);
              const chapterEst = estimates[ch.id];

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
                      onClick={() => navigate(`/read/${bookId}?section=${ch.id}`)}
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
                        ? ` ${jobDetails}`
                        : isFailedTranslate
                        ? " 翻译失败"
                        : " 待翻译"}
                    </p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {isTranslating && jobInfo.status === "running" && (
                      <button
                        onClick={() => handlePauseResume(ch.id, jobInfo.jobId, "running")}
                        className="px-3 py-1.5 text-xs rounded-lg bg-yellow-100 text-yellow-700 hover:bg-yellow-200"
                      >
                        暂停
                      </button>
                    )}
                    {isTranslating && jobInfo.status === "paused" && (
                      <button
                        onClick={() => handlePauseResume(ch.id, jobInfo.jobId, "paused")}
                        className="px-3 py-1.5 text-xs rounded-lg bg-green-100 text-green-700 hover:bg-green-200"
                      >
                        继续
                      </button>
                    )}
                    {!isTransCompleted && !isTranslating && (
                      <button
                        onClick={() => handleTranslateClick(ch.id)}
                        disabled={estimatingCh === ch.id}
                        className={`px-3 py-1.5 text-xs rounded-lg transition
                          ${estimatingCh === ch.id
                            ? "bg-gray-100 text-gray-400 cursor-wait"
                            : "bg-blue-600 text-white hover:bg-blue-700"
                          }`}
                      >
                        {estimatingCh === ch.id ? "估算中..." : "翻译"}
                      </button>
                    )}
                    {showRetry && (
                      <button
                        onClick={() => handleRetry(ch.id)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-orange-100 text-orange-700 hover:bg-orange-200"
                      >
                        重试
                      </button>
                    )}
                    {isTransCompleted && (
                      <button
                        onClick={() => navigate(`/read/${bookId}?section=${ch.id}`)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-green-100 text-green-700 hover:bg-green-200"
                      >
                        阅读
                      </button>
                    )}
                    {chapterEst && !isTranslating && !isTransCompleted && (
                      <span className="text-xs text-gray-400 self-center">
                        ~{chapterEst.characters} 字符
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── 翻译确认弹窗 ── */}
      {confirmDialog && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full shadow-xl space-y-4">
            <h3 className="text-lg font-semibold">确认翻译</h3>

            <div className="bg-blue-50 rounded-xl p-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-500">待翻译字符</span>
                <span className="font-medium">{confirmDialog.estimate.characters.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">今日已用</span>
                <span>{confirmDialog.estimate.daily_used.toLocaleString()} / {confirmDialog.estimate.daily_limit.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">今日剩余</span>
                <span className={confirmDialog.estimate.daily_remaining >= confirmDialog.estimate.characters ? "text-green-600" : "text-red-600"}>
                  {confirmDialog.estimate.daily_remaining.toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">本月已用</span>
                <span>{confirmDialog.estimate.monthly_used.toLocaleString()} / {confirmDialog.estimate.monthly_limit.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">本月剩余</span>
                <span className={confirmDialog.estimate.monthly_remaining >= confirmDialog.estimate.characters ? "text-green-600" : "text-red-600"}>
                  {confirmDialog.estimate.monthly_remaining.toLocaleString()}
                </span>
              </div>
            </div>

            {confirmDialog.estimate.characters > 0 && (
              <p className="text-xs text-gray-400">
                创建翻译任务后，字符数将计入预留额度。任务完成后按实际翻译字符数计费。
              </p>
            )}

            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setConfirmDialog(null)}
                className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={handleConfirmTranslate}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                确认翻译
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
