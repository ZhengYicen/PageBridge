/**
 * PDF 页码适配层
 *
 * 唯一的 +1/-1 转换层，所有组件都通过此模块进行页码转换。
 * 不要在各组件中直接加减页码。
 *
 * 约定：
 *   - pdf_page_index: 0-based，PDF.js 内部使用
 *   - pdf_page_number: 1-based，用户可见页码
 *   - pdfJsPageNumber: PDF.js getPage(pageNumber) 的参数，1-based
 */

/** 0-based page index → PDF.js 1-based page number */
export function toPdfJsPage(pageIndex: number): number {
  return pageIndex + 1;
}

/** PDF.js 1-based page number → 0-based page index */
export function toPageIndex(pdfJsPageNumber: number): number {
  return pdfJsPageNumber - 1;
}

/** fragment 中的 pdf_page_index → PDF.js page number */
export function fragmentToPdfJsPage(
  fragment: { pdf_page_index: number } | undefined | null,
): number {
  if (!fragment) return 1;
  return toPdfJsPage(fragment.pdf_page_index);
}

/** 格式化页码显示 */
export function formatPageLabel(
  pageNumber: number,
  totalPages: number,
): string {
  return `第 ${pageNumber} / ${totalPages} 页`;
}
