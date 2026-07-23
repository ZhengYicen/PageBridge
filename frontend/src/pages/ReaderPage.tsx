import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { api, ReadingParagraph, ReadingSection, SourceFragment } from "../api/client";
import PdfViewer, { HighlightBox } from "../components/PdfViewer";
import { toPdfJsPage } from "../lib/pdfAdapter";

const PARA_BATCH_SIZE = 50;
const PRELOAD_THRESHOLD = 20;

export default function ReaderPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const [searchParams] = useSearchParams();
  const targetSection = searchParams.get("section");
  const navigate = useNavigate();

  // ── 书籍状态 ────────────────────────────────────────
  const [bookTitle, setBookTitle] = useState("");
  const [totalPages, setTotalPages] = useState(0);
  const [pdfUrl, setPdfUrl] = useState("");
  const [sections, setSections] = useState<ReadingSection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── 段落状态 ────────────────────────────────────────
  const [paragraphs, setParagraphs] = useState<ReadingParagraph[]>([]);
  const [totalParas, setTotalParas] = useState(0);
  const [loadedOffset, setLoadedOffset] = useState(0);
  const [loadingParas, setLoadingParas] = useState(false);

  // ── PDF 联动 ────────────────────────────────────────
  const [pdfPageNumber, setPdfPageNumber] = useState(1); // 1-based PDF.js
  const [highlights, setHighlights] = useState<HighlightBox[]>([]);
  const [followPaused, setFollowPaused] = useState(false);
  const [activeParaId, setActiveParaId] = useState<string | null>(null);

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const paraElementsRef = useRef<Map<string, HTMLElement>>(new Map());
  const followPausedRef = useRef(false);
  const activeParaIdRef = useRef<string | null>(null);
  const currentPdfPageRef = useRef(1);
  const scrollToOffsetRef = useRef<number>(-1);

  // ── 翻译轮询状态 ──────────────────────────────
  const [translating, setTranslating] = useState(false);

  // ── 加载阅读页 ──────────────────────────────────────

  useEffect(() => {
    if (!bookId) return;

    api
      .getReaderInfo(bookId)
      .then((info) => {
        setBookTitle(info.book.title);
        setTotalPages(info.total_pages);
        setPdfUrl(info.book.pdf_url);
        setSections(info.sections);
        setLoading(false);

        // 如果指定了目标 section，跳到该 section 的段落偏移位置
        let targetOffset = 0;
        if (targetSection) {
          const found = info.sections.find(
            (s: ReadingSection) => s.section_id === targetSection,
          );
          if (found) {
            targetOffset = found.start_paragraph_order;
            scrollToOffsetRef.current = targetOffset;
          }
        }

        // 使用 __full__ 虚拟 section 加载全本
        const fullSection = info.sections.find(
          (s: ReadingSection) => s.section_id === "__full__",
        ) || info.sections[0];
        loadParagraphs(bookId, fullSection.section_id, targetOffset);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [bookId]);

  // ── 加载段落（不会自动触发翻译） ──────────────

  const loadParagraphs = useCallback(
    async (bid: string, sectionId: string, offset: number) => {
      setLoadingParas(true);
      try {
        const result = await api.getSectionParagraphs(
          bid,
          sectionId,
          offset,
          PARA_BATCH_SIZE,
        );
        if (offset === 0) {
          setParagraphs(result.paragraphs);
        } else {
          setParagraphs((prev) => [...prev, ...result.paragraphs]);
        }
        setTotalParas(result.total);
        setLoadedOffset(offset + result.paragraphs.length);

        // 不再自动触发预翻译。用户需要先在章节页确认翻译。
        // 如果已有段落处于 translating 状态，启动轮询
        const hasActive = result.paragraphs.some(
          (p) => p.status === "translating"
        );
        if (hasActive) {
          startTranslationPolling(sectionId);
        }
      } catch (err: any) {
        console.error("加载段落失败:", err);
      } finally {
        setLoadingParas(false);
      }
    },
    [],
  );

  // ── 翻译轮询 ──────────────────────────────────────

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startTranslationPolling = useCallback((sectionId: string) => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const result = await api.getParagraphTranslations(sectionId);
        // 只更新已有段落的翻译
        setParagraphs((prev) => {
          const updated = [...prev];
          for (const p of result.paragraphs) {
            const idx = updated.findIndex((u) => u.id === p.id);
            if (idx >= 0) {
              updated[idx] = {
                ...updated[idx],
                translation: p.translation,
                status: p.status,
                error_message: p.error_message,
              };
            }
          }
          return updated;
        });
        if (
          result.translate_status === "completed" ||
          result.completed === result.total
        ) {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          setTranslating(false);
        }
      } catch {
        // 忽略轮询错误
      }
    }, 2000);
  }, []);

  // 清理轮询
  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  // ── Active Paragraph 检测（基于滚动位置）───────────

  const findActiveParagraph = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return null;

    const paraElements = container.querySelectorAll<HTMLElement>(
      "[data-para-id]",
    );
    if (paraElements.length === 0) return null;

    const containerRect = container.getBoundingClientRect();
    const anchorMid =
      containerRect.top + containerRect.height * 0.5;

    let closestId: string | null = null;
    let minDist = Infinity;

    for (const el of paraElements) {
      const rect = el.getBoundingClientRect();
      const elMid = (rect.top + rect.bottom) / 2;
      const dist = Math.abs(elMid - anchorMid);
      if (dist < minDist) {
        minDist = dist;
        closestId = el.getAttribute("data-para-id");
      }
    }

    return closestId;
  }, []);

  // 统一滚动处理：加载更多 + 更新 active paragraph
  const scrollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || !bookId) return;

    const { scrollTop, scrollHeight, clientHeight } = container;

    // 加载更多（距离底部 300px）
    if (
      scrollHeight - scrollTop - clientHeight < 300 &&
      loadedOffset < totalParas &&
      !loadingParas
    ) {
      const firstSection = sections[0];
      if (firstSection) {
        loadParagraphs(bookId, firstSection.section_id, loadedOffset);
      }
    }

    // 防抖更新 active paragraph（200ms）
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    scrollTimeoutRef.current = setTimeout(() => {
      const id = findActiveParagraph();
      if (id) {
        setActiveParaId(id);
        activeParaIdRef.current = id;
      }
    }, 200);
  }, [
    bookId,
    loadedOffset,
    totalParas,
    loadingParas,
    sections,
    loadParagraphs,
    findActiveParagraph,
  ]);

  // 初始加载后：设置 active paragraph + 跳转到目标 section
  useEffect(() => {
    if (paragraphs.length > 0) {
      requestAnimationFrame(() => {
        // 如果指定了章节跳转，滚动到目标位置
        if (scrollToOffsetRef.current >= 0) {
          const container = scrollContainerRef.current;
          if (container) {
            const firstEl = container.querySelector<HTMLElement>(
              "[data-para-id]",
            );
            if (firstEl) {
              firstEl.scrollIntoView({ block: "start" });
            }
          }
          scrollToOffsetRef.current = -1;
        }

        const id = findActiveParagraph();
        if (id) {
          setActiveParaId(id);
          activeParaIdRef.current = id;
        }
      });
    }
  }, [paragraphs.length, findActiveParagraph]);

  // ── Active Paragraph → PDF 联动 ─────────────────────

  useEffect(() => {
    if (!activeParaId || followPausedRef.current) return;

    const para = paragraphs.find((p) => p.id === activeParaId);
    if (!para || !para.source_fragments || para.source_fragments.length === 0)
      return;

    const firstFrag = para.source_fragments[0];
    const targetPage = toPdfJsPage(firstFrag.pdf_page_index);

    // 同一页不重复跳转
    if (targetPage === currentPdfPageRef.current) {
      // 但高亮需要更新（同一页不同段落）
      setHighlights(
        para.source_fragments.map((f) => ({
          bbox_normalized: f.bbox_normalized,
          fragment_order: f.fragment_order,
        })),
      );
      return;
    }

    currentPdfPageRef.current = targetPage;
    setPdfPageNumber(targetPage);
    setHighlights(
      para.source_fragments.map((f) => ({
        bbox_normalized: f.bbox_normalized,
        fragment_order: f.fragment_order,
      })),
    );
  }, [activeParaId, paragraphs]);

  // ── 用户手动操作 PDF ─────────────────────────────

  const handleUserInteracted = useCallback(() => {
    followPausedRef.current = true;
    setFollowPaused(true);
  }, []);

  const handlePdfPageChange = useCallback((newPage: number) => {
    setPdfPageNumber(newPage);
    currentPdfPageRef.current = newPage;
  }, []);

  // ── 恢复跟随 ──────────────────────────────────────

  const resumeFollow = useCallback(() => {
    followPausedRef.current = false;
    setFollowPaused(false);

    // 立即跳转到当前 active paragraph 对应的页面
    const id = activeParaIdRef.current;
    if (!id) return;

    const para = paragraphs.find((p) => p.id === id);
    if (!para || !para.source_fragments || para.source_fragments.length === 0)
      return;

    const firstFrag = para.source_fragments[0];
    const targetPage = toPdfJsPage(firstFrag.pdf_page_index);
    currentPdfPageRef.current = targetPage;
    setPdfPageNumber(targetPage);
    setHighlights(
      para.source_fragments.map((f) => ({
        bbox_normalized: f.bbox_normalized,
        fragment_order: f.fragment_order,
      })),
    );
  }, [paragraphs]);

  // ── 渲染帮助 ──────────────────────────────────────

  const renderTranslation = (p: ReadingParagraph) => {
    if (p.status === "completed") return p.translation;
    if (p.status === "failed") return `[翻译失败] ${p.error_message || ""}`;
    if (p.status === "pending" || p.status === "") return "等待翻译...";
    return "翻译中...";
  };

  const getPageRange = (p: ReadingParagraph) => {
    if (p.page_end > p.page_start) {
      return `P${p.page_start}–P${p.page_end}`;
    }
    return `P${p.page_start}`;
  };

  // ── 页面范围标记 ──────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-60px)] text-gray-400">
        加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-60px)] text-red-500">
        {error}
      </div>
    );
  }

  const completedCount = paragraphs.filter((p) => p.status === "completed").length;

  return (
    <div className="flex h-[calc(100vh-60px)] bg-white">
      {/* ── 左侧：PDF 查看器 ─────────────────────────── */}
      <div className="w-[32%] shrink-0 border-r sticky top-0 h-full flex flex-col bg-gray-50">
        {/* PDF 区域 */}
        <div className="flex-1 overflow-hidden">
          <PdfViewer
            pdfUrl={pdfUrl}
            pageNumber={pdfPageNumber}
            highlights={highlights}
            totalPages={totalPages}
            onPageChange={handlePdfPageChange}
            onUserInteracted={handleUserInteracted}
          />
        </div>

        {/* 联动状态指示 */}
        <div className="px-3 py-2 border-t bg-white shrink-0">
          {followPaused ? (
            <div className="flex items-center justify-between">
              <span className="text-xs text-amber-600 font-medium">
                ⏸ 跟随已暂停
              </span>
              <button
                onClick={resumeFollow}
                className="text-xs text-blue-600 hover:text-blue-800 font-medium"
              >
                回到当前正文 →
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <span className="text-xs text-green-600 font-medium">
                ● 联动中
              </span>
              <span className="text-xs text-gray-400">
                已翻译 {completedCount}/{totalParas} 段
                {translating && " · 翻译中..."}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ── 右侧：英中双语正文 ──────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 头部信息 */}
        <div className="flex items-center gap-3 px-5 py-3 border-b shrink-0 bg-white">
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-blue-600 hover:text-blue-800 shrink-0"
          >
            ← 返回
          </button>
          <h1 className="text-base font-semibold truncate">{bookTitle}</h1>
        </div>

        {/* 双语滚动容器 */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto"
        >
          {paragraphs.length === 0 ? (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              暂无内容
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {paragraphs.map((p) => (
                <div
                  key={p.id}
                  data-para-id={p.id}
                  ref={(el) => {
                    if (el) paraElementsRef.current.set(p.id, el);
                  }}
                  className={`flex transition-colors ${
                    activeParaId === p.id ? "bg-blue-50/50" : ""
                  }`}
                >
                  {/* 英文 */}
                  <div className="w-1/2 p-4 pr-3 border-r border-gray-100">
                    <div
                      className="text-sm leading-relaxed text-gray-900"
                      dangerouslySetInnerHTML={{
                        __html: p.source_html || p.source_text,
                      }}
                    />
                    {p.source_fragments && p.source_fragments.length > 0 && (
                      <div className="mt-1 text-xs text-gray-400">
                        {getPageRange(p)}
                        {p.source_fragments.length > 1 &&
                          ` · ${p.source_fragments.length} 个片段`}
                      </div>
                    )}
                  </div>

                  {/* 中文 */}
                  <div className="w-1/2 p-4 pl-3">
                    <div
                      className={`text-sm leading-relaxed ${
                        p.status === "pending"
                          ? "text-gray-300 italic"
                          : p.status === "failed"
                          ? "text-red-400"
                          : "text-gray-800"
                      }`}
                    >
                      {renderTranslation(p)}
                    </div>
                  </div>
                </div>
              ))}

              {/* 加载中指示 */}
              {loadingParas && (
                <div className="p-4 text-center text-xs text-gray-400">
                  加载中...
                </div>
              )}

              {/* 全部加载完成 */}
              {!loadingParas && loadedOffset >= totalParas && totalParas > 0 && (
                <div className="p-4 text-center text-xs text-gray-400">
                  — 全部加载完成 ({totalParas} 段) —
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
