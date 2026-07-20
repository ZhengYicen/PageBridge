import { useEffect, useRef, useState, useCallback } from "react";
import * as pdfjsLib from "pdfjs-dist";

// 设置 worker（从 public/ 目录加载）
pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

export interface HighlightBox {
  bbox_normalized: string; // JSON: {x1,y1,x2,y2} in 0~1
  fragment_order: number;
}

interface Props {
  pdfUrl: string;
  pageNumber: number; // 1-based PDF.js page number
  highlights?: HighlightBox[];
  totalPages: number;
  onPageChange?: (pageNum: number) => void;
  onUserInteracted?: () => void;
}

export default function PdfViewer({
  pdfUrl,
  pageNumber,
  highlights = [],
  totalPages,
  onPageChange,
  onUserInteracted,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const renderTaskRef = useRef<pdfjsLib.RenderTask | null>(null);
  const [scale, setScale] = useState(0.6);
  const containerRef = useRef<HTMLDivElement>(null);

  // ── 加载 PDF 并自适应宽度 ───────────────────────────

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    pdfjsLib
      .getDocument(pdfUrl)
      .promise.then((doc) => {
        if (!cancelled) {
          setPdfDoc(doc);
          // 自适应：按容器宽度设置初始 scale
          if (containerRef.current) {
            const containerWidth = containerRef.current.clientWidth - 20; // padding
            doc.getPage(1).then((page) => {
              const vp = page.getViewport({ scale: 1 });
              const fitScale = containerWidth / vp.width;
              const clamped = Math.max(0.3, Math.min(fitScale, 2.0));
              if (!cancelled) setScale(clamped);
              page.cleanup();
            });
          }
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(`PDF 加载失败: ${err.message}`);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pdfUrl]);

  // ── 渲染页面 ────────────────────────────────────────

  useEffect(() => {
    if (!pdfDoc || !canvasRef.current) return;

    let cancelled = false;

    const renderPage = async () => {
      // 取消之前的渲染
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel();
        } catch {
          // ignore
        }
        renderTaskRef.current = null;
      }

      setRendering(true);

      try {
        const page = await pdfDoc.getPage(pageNumber);
        // eslint-disable-next-line react-hooks/exhaustive-deps
        const viewport = page.getViewport({ scale });

        const canvas = canvasRef.current!;
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const ctx = canvas.getContext("2d")!;
        const renderTask = page.render({
          canvasContext: ctx,
          viewport,
        });
        renderTaskRef.current = renderTask;

        await renderTask.promise;
        renderTaskRef.current = null;

        if (!cancelled) {
          setRendering(false);
        }
      } catch (err: any) {
        if (err?.name !== "CancelException" && !cancelled) {
          console.error("PDF 页面渲染失败:", err);
          setRendering(false);
        }
      }
    };

    renderPage();

    return () => {
      cancelled = true;
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel();
        } catch {
          // ignore
        }
        renderTaskRef.current = null;
      }
    };
  }, [pdfDoc, pageNumber, scale]);

  // ── 缩放控制 ────────────────────────────────────────

  const handleZoomIn = useCallback(() => {
    setScale((s) => Math.min(s + 0.2, 3.0));
  }, []);

  const handleZoomOut = useCallback(() => {
    setScale((s) => Math.max(s - 0.2, 0.5));
  }, []);

  // ── 页面导航 ────────────────────────────────────────

  const goToPrev = useCallback(() => {
    if (pageNumber > 1) {
      onUserInteracted?.();
      onPageChange?.(pageNumber - 1);
    }
  }, [pageNumber, onPageChange, onUserInteracted]);

  const goToNext = useCallback(() => {
    if (pageNumber < totalPages) {
      onUserInteracted?.();
      onPageChange?.(pageNumber + 1);
    }
  }, [pageNumber, totalPages, onPageChange, onUserInteracted]);

  // ── 键盘快捷键 ────────────────────────────────────

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowUp" || e.key === "PageUp") {
        e.preventDefault();
        goToPrev();
      } else if (e.key === "ArrowDown" || e.key === "PageDown") {
        e.preventDefault();
        goToNext();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goToPrev, goToNext]);

  // ── 高亮区域计算 ────────────────────────────────────

  const getHighlightStyle = (bboxNormalized: string) => {
    try {
      const { x1, y1, x2, y2 } = JSON.parse(bboxNormalized);
      const canvas = canvasRef.current;
      if (!canvas) return {};

      return {
        left: `${x1 * 100}%`,
        top: `${y1 * 100}%`,
        width: `${(x2 - x1) * 100}%`,
        height: `${(y2 - y1) * 100}%`,
      };
    } catch {
      return {};
    }
  };

  // ── 渲染 ────────────────────────────────────────────

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-500 text-sm">
        {error}
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        加载中...
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full" ref={containerRef}>
      {/* PDF 显示区域 */}
      <div className="flex-1 overflow-auto bg-gray-200 relative">
        <div className="flex justify-center p-2">
          <div className="relative inline-block shadow-xl bg-white">
            <canvas ref={canvasRef} className="block" />
            {/* 高亮覆盖层 */}
            <div
              ref={overlayRef}
              className="absolute inset-0 pointer-events-none"
            >
              {highlights.map((h, i) => {
                const style = getHighlightStyle(h.bbox_normalized);
                return (
                  <div
                    key={i}
                    className="absolute bg-yellow-300/40 border-2 border-yellow-400/60 rounded-sm"
                    style={style}
                  />
                );
              })}
            </div>
            {/* 渲染中指示 */}
            {rendering && (
              <div className="absolute inset-0 flex items-center justify-center bg-white/50">
                <span className="text-xs text-gray-500">渲染中...</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 控制栏 */}
      <div className="flex items-center justify-between px-3 py-2 bg-white border-t shrink-0">
        <div className="flex items-center gap-1">
          <button
            onClick={handleZoomOut}
            className="px-2 py-1 text-xs rounded hover:bg-gray-100 disabled:opacity-30"
            disabled={scale <= 0.5}
            title="缩小"
          >
            −
          </button>
          <span className="text-xs text-gray-500 w-8 text-center">
            {Math.round(scale * 100)}%
          </span>
          <button
            onClick={handleZoomIn}
            className="px-2 py-1 text-xs rounded hover:bg-gray-100 disabled:opacity-30"
            disabled={scale >= 3.0}
            title="放大"
          >
            +
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={goToPrev}
            disabled={pageNumber <= 1}
            className="px-3 py-1 text-sm rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-default"
          >
            ← 上一页
          </button>
          <span className="text-sm text-gray-600 min-w-[80px] text-center whitespace-nowrap">
            {pageNumber} / {totalPages}
          </span>
          <button
            onClick={goToNext}
            disabled={pageNumber >= totalPages}
            className="px-3 py-1 text-sm rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-default"
          >
            下一页 →
          </button>
        </div>
      </div>
    </div>
  );
}
