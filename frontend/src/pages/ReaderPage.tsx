import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, Chapter, Paragraph } from "../api/client";

export default function ReaderPage() {
  const { chapterId } = useParams<{ chapterId: string }>();
  const navigate = useNavigate();
  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [paragraphs, setParagraphs] = useState<Paragraph[]>([]);
  const [loading, setLoading] = useState(true);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const isSyncing = useRef(false);
  const lastSyncedPara = useRef("");

  useEffect(() => {
    if (!chapterId) return;
    api.getParagraphs(chapterId).then((res) => {
      setChapter(res.chapter);
      setParagraphs(res.paragraphs);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [chapterId]);

  // 滚动同步（防回弹）
  const handleScroll = (source: "left" | "right") => {
    if (isSyncing.current) return;

    const container = source === "left" ? leftRef.current : rightRef.current;
    const targetContainer = source === "left" ? rightRef.current : leftRef.current;
    if (!container || !targetContainer) return;

    // 找出视口内最靠上的段落 ID（不用中心点，避免反复跳动）
    const paras = container.querySelectorAll<HTMLElement>("[data-para-id]");
    let targetId = "";
    for (const el of paras) {
      if (el.offsetTop >= container.scrollTop - 10) {
        targetId = el.dataset.paraId || "";
        break;
      }
    }

    // 如果和最上次同步的是同一个，跳过（防回弹）
    if (!targetId || targetId === lastSyncedPara.current) return;
    lastSyncedPara.current = targetId;

    isSyncing.current = true;
    const targetEl = targetContainer.querySelector(`[data-para-id="${targetId}"]`);
    if (targetEl) {
      targetEl.scrollIntoView({ behavior: "instant", block: "nearest" });
    }
    // 等浏览器渲染完再解锁
    requestAnimationFrame(() => { isSyncing.current = false; });
  };

  const handleParaClick = (paraId: string) => {
    setHighlightedId(paraId);
    // 两侧同时高亮对应段落
    isSyncing.current = true;
    const leftEl = leftRef.current?.querySelector(`[data-para-id="${paraId}"]`);
    const rightEl = rightRef.current?.querySelector(`[data-para-id="${paraId}"]`);
    leftEl?.scrollIntoView({ behavior: "instant", block: "center" });
    rightEl?.scrollIntoView({ behavior: "instant", block: "center" });
    setTimeout(() => { isSyncing.current = false; }, 200);
    setTimeout(() => setHighlightedId(null), 2000);
  };

  if (loading) {
    return <div className="text-center py-12 text-gray-400">加载中...</div>;
  }

  if (!chapter) {
    return <div className="text-center py-12 text-gray-400">章节不存在</div>;
  }

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
          {paragraphs.filter((p) => p.status === "completed").length}/{paragraphs.length} 段已翻译
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
                `}
                dangerouslySetInnerHTML={{ __html: p.source_html || p.source_text }}
              />
            ))}
          </div>
        </div>

        {/* 分隔线 */}
        <div className="w-px bg-gray-200 shrink-0" />

        {/* 译文 */}
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
                `}
              >
                {p.status === "completed"
                  ? p.translation
                  : p.status === "failed"
                  ? `[翻译失败] ${p.error_message || ""}`
                  : p.status === "pending"
                  ? "等待翻译..."
                  : "翻译中..."}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
