import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, Chapter, Paragraph } from "../api/client";

export default function ReaderPage() {
  const { chapterId } = useParams<{ chapterId: string }>();
  const navigate = useNavigate();
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [paragraphs, setParagraphs] = useState<Paragraph[]>([]);
  const [loading, setLoading] = useState(true);
  const [translating, setTranslating] = useState(false);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const isSyncing = useRef(false);
  const scrollFrameRef = useRef<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── 初始加载 + 预翻译 ──────────────────────────────

  useEffect(() => {
    if (!chapterId) return;

    // 加载段落
    api.getParagraphs(chapterId).then((res) => {
      setChapter(res.chapter);
      setParagraphs(res.paragraphs);
      setLoading(false);

      // 检查是否需要自动预翻译
      const pendingCount = res.paragraphs.filter(
        (p) => p.status === "pending" || p.status === "failed"
      ).length;
      const totalCount = res.paragraphs.length;

      if (pendingCount > 0 && totalCount > 0) {
        // 启动预翻译
        setTranslating(true);
        api.preTranslateChapter(chapterId).then((result) => {
          if (result.status === "started") {
            startPolling();
          } else if (result.status === "completed" || result.status === "no_pending") {
            setTranslating(false);
          }
        }).catch(() => {
          setTranslating(false);
        });
      }
    }).catch(() => setLoading(false));

    return () => {
      // 清理轮询
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      // 清理滚动帧
      if (scrollFrameRef.current !== null) {
        cancelAnimationFrame(scrollFrameRef.current);
        scrollFrameRef.current = null;
      }
    };
  }, [chapterId]);

  // ── 轮询翻译状态 ──────────────────────────────────

  const startPolling = useCallback(() => {
    if (pollRef.current) return;

    pollRef.current = setInterval(async () => {
      if (!chapterId) return;

      try {
        const result = await api.getParagraphTranslations(chapterId);
        setParagraphs(result.paragraphs);
        setTranslating(false);

        // 全部完成时停止轮询
        if (result.translate_status === "completed" || result.completed === result.total) {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch {
        // 轮询失败不中断
      }
    }, 1500);
  }, [chapterId]);

  // ── 滚动同步 ──────────────────────────────────────

  const handleScroll = (source: "left" | "right") => {
    if (isSyncing.current) return;

    // RAF 节流
    if (scrollFrameRef.current !== null) return;
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;

      const container = source === "left" ? leftRef.current : rightRef.current;
      const targetContainer = source === "left" ? rightRef.current : leftRef.current;
      if (!container || !targetContainer) return;

      const anchorRatio = 0.28;
      const containerRect = container.getBoundingClientRect();
      const anchorY = containerRect.top + containerRect.height * anchorRatio;

      const paras = container.querySelectorAll<HTMLElement>("[data-para-id]");
      if (paras.length === 0) return;

      // 找到距离锚点最近的段落，计算锚点在该段落内的滚动进度
      let anchorId = "";
      let progress = 0;
      let minDist = Infinity;

      for (const el of paras) {
        const rect = el.getBoundingClientRect();
        const id = el.dataset.paraId || "";
        const midY = (rect.top + rect.bottom) / 2;
        const dist = Math.abs(anchorY - midY);

        if (dist < minDist) {
          minDist = dist;
          anchorId = id;
          const clampedY = Math.max(rect.top, Math.min(rect.bottom, anchorY));
          progress = rect.height > 0 ? Math.min(1, Math.max(0, (clampedY - rect.top) / rect.height)) : 0;
        }
      }

      if (!anchorId) return;

      // 在目标容器中找到同一段落，按相同比例对齐到同一阅读锚点
      isSyncing.current = true;
      const targetEl = targetContainer.querySelector<HTMLElement>(`[data-para-id="${anchorId}"]`);
      if (targetEl) {
        const targetRect = targetEl.getBoundingClientRect();
        const targetContainerRect = targetContainer.getBoundingClientRect();
        const targetAnchorY = targetContainerRect.top + targetContainerRect.height * anchorRatio;

        const pointInParaY = targetRect.top + targetRect.height * progress;
        const delta = pointInParaY - targetAnchorY;
        targetContainer.scrollTop += delta;
      }

      // 两层 RAF 确保布局稳定后再解除同步锁，防止递归触发
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          isSyncing.current = false;
        });
      });
    });
  };

  const handleParaClick = (paraId: string) => {
    setHighlightedId(paraId);
    isSyncing.current = true;
    const leftEl = leftRef.current?.querySelector(`[data-para-id="${paraId}"]`);
    const rightEl = rightRef.current?.querySelector(`[data-para-id="${paraId}"]`);
    leftEl?.scrollIntoView({ behavior: "instant", block: "center" });
    rightEl?.scrollIntoView({ behavior: "instant", block: "center" });
    setTimeout(() => { isSyncing.current = false; }, 200);
    setTimeout(() => setHighlightedId(null), 2000);
  };

  // ── 渲染 ──────────────────────────────────────────

  if (loading) {
    return <div className="text-center py-12 text-gray-400">加载中...</div>;
  }

  if (!chapter) {
    return <div className="text-center py-12 text-gray-400">章节不存在</div>;
  }

  const completedCount = paragraphs.filter((p) => p.status === "completed").length;
  const totalCount = paragraphs.length;

  // 渲染译文内容（支持图片 JSX）
  const renderTranslation = (p: Paragraph) => {
    if (p.status === "image") {
      return <span dangerouslySetInnerHTML={{ __html: p.source_html || p.source_text }} />;
    }
    if (p.status === "completed") return p.translation;
    if (p.status === "failed") return `[翻译失败] ${p.error_message || ""}`;
    if (p.status === "pending") return "等待翻译...";
    return "翻译中...";
  };

  return (
    <div className="flex flex-col h-[calc(100vh-120px)]">
      {/* 顶部信息 */}
      <div className="flex items-center gap-3 mb-4 pb-3 border-b">
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-blue-600 hover:text-blue-800"
        >
          ← 返回
        </button>
        <h1 className="text-lg font-semibold truncate">{chapter.title}</h1>
        <span className="text-xs text-gray-400 ml-auto">
          {completedCount}/{totalCount} 段已翻译
          {translating && " · 翻译中..."}
          {completedCount === totalCount && totalCount > 0 && " ✅"}
        </span>
      </div>

      {/* 双语区域 */}
      <div className="flex-1 flex gap-4 overflow-hidden">
        {/* 原文 */}
        <div
          ref={leftRef}
          className="flex-1 overflow-y-auto bg-white rounded-xl p-6 border"
          onScroll={() => handleScroll("left")}
        >
          <h2 className="text-sm font-semibold text-gray-400 mb-4 uppercase tracking-wider">English</h2>
          <div className="space-y-4">
            {paragraphs.map((p) => (
              <div
                key={p.id}
                data-para-id={p.id}
                onClick={() => handleParaClick(p.id)}
                className={`leading-relaxed cursor-pointer transition rounded px-2 py-1
                  ${highlightedId === p.id ? "bg-yellow-100 ring-2 ring-yellow-300" : "hover:bg-gray-50"}
                  ${p.status === "image" ? "p-0 bg-gray-50 flex justify-center" : ""}
                `}
                dangerouslySetInnerHTML={{ __html: p.source_html || p.source_text }}
              />
            ))}
          </div>
        </div>

        {/* 分隔线 */}
        <div className="w-px bg-gray-200 shrink-0" />

        {/* 译文 — 渐进式显示 */}
        <div
          ref={rightRef}
          className="flex-1 overflow-y-auto bg-white rounded-xl p-6 border"
          onScroll={() => handleScroll("right")}
        >
          <h2 className="text-sm font-semibold text-gray-400 mb-4 uppercase tracking-wider">中文</h2>
          <div className="space-y-4">
            {paragraphs.map((p) => (
              <div
                key={p.id}
                data-para-id={p.id}
                onClick={() => handleParaClick(p.id)}
                className={`leading-relaxed cursor-pointer transition rounded px-2 py-1
                  ${highlightedId === p.id ? "bg-yellow-100 ring-2 ring-yellow-300" : "hover:bg-gray-50"}
                  ${p.status === "pending" ? "text-gray-300 italic" : ""}
                  ${p.status === "failed" ? "text-red-400" : ""}
                  ${p.status === "completed" ? "text-gray-800" : ""}
                  ${p.status === "image" ? "p-0 bg-gray-50 flex justify-center" : ""}
                `}
              >
                {renderTranslation(p)}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
