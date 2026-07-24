const API_BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export interface Book {
  id: string;
  title: string;
  author: string;
  format: string;
  parse_status: string;
  total_chapters: number;
  total_pages: number;
  parsed_pages: number;
  failed_pages: number;
  current_stage: string;
  error_message: string;
  created_at: string;
  uploaded_at?: string;
  file_size?: number;
  chapters?: Chapter[];
}

export interface BookProgress {
  book_id: string;
  status: string;
  current_stage: string;
  total_pages: number;
  parsed_pages: number;
  failed_pages: number;
  progress: number;
  error_message: string;
}

export interface Chapter {
  id: string;
  book_id: string;
  title: string;
  chapter_order: number;
  paragraph_count: number;
  translate_status: string;
  created_at: string;
}

export interface Paragraph {
  id: string;
  chapter_id: string;
  paragraph_order: number;
  source_text: string;
  source_html: string;
  translation: string;
  status: string;
  error_message: string;
  updated_at: string;
}

export interface Job {
  id: string;
  chapter_id?: string;
  book_id?: string;
  status: string;
  total_paragraphs: number;
  completed_paragraphs: number;
  failed_paragraphs: number;
  job_type: string;
  created_at: string;
  updated_at: string;
  error_message?: string;
}

export interface SourceFragment {
  pdf_page_index: number;
  pdf_page_number: number;
  bbox: string;
  bbox_normalized: string;
  original_page_width: number;
  original_page_height: number;
  fragment_order: number;
  source_text: string;
  confidence: number;
}

export interface ReadingParagraph {
  id: string;
  paragraph_order: number;
  source_text: string;
  source_html: string;
  translation: string;
  status: string;
  error_message: string;
  page_number: number;
  page_start: number;
  page_end: number;
  source_fragments: SourceFragment[];
}

export interface ReadingSection {
  section_id: string;
  title: string;
  paragraph_count: number;
  start_paragraph_order: number;
  page_start: number;
  page_end: number;
}

export interface ReaderInfo {
  book: { id: string; title: string; format: string; total_pages: number; pdf_url: string; parse_status: string };
  total_pages: number;
  sections: ReadingSection[];
}

export interface PaginatedParagraphs {
  section_id: string;
  paragraphs: ReadingParagraph[];
  total: number;
  offset: number;
  limit: number;
}

export const api = {
  upload: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/upload`, {
      method: "POST", body: form, credentials: "include",
    });
    if (!res.ok) throw new Error((await res.json()).detail);
    return res.json();
  },

  listBooks: () => request<{ books: Book[] }>("/books"),
  getBook: (id: string) => request<Book>(`/books/${id}`),
  parseBook: (id: string) =>
    request<{ book_id: string; job_id?: string; status: string }>(`/books/${id}/parse`, { method: "POST" }),
  getBookProgress: (id: string) => request<BookProgress>(`/books/${id}/progress`),
  deleteBook: (id: string) => request<{ status: string }>(`/books/${id}`, { method: "DELETE" }),

  getParagraphs: (id: string) =>
    request<{ chapter: Chapter; paragraphs: Paragraph[] }>(`/chapters/${id}/paragraphs`),
  translateChapter: (chapterId: string) =>
    request<{ job_id: string; chapter_id: string; status: string }>(
      `/chapters/${chapterId}/translate`, { method: "POST" }
    ),

  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  pauseJob: (id: string) => request<{ status: string }>(`/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => request<{ status: string }>(`/jobs/${id}/resume`, { method: "POST" }),
  retryJob: (id: string) => request<{ status: string }>(`/jobs/${id}/retry`, { method: "POST" }),

  getParagraphTranslations: (chapterId: string) =>
    request<{
      chapter_id: string; chapter_title: string; translate_status: string;
      total: number; completed: number; paragraphs: Paragraph[];
    }>(`/paragraphs/${chapterId}/translations`),

  subscribeProgress: (jobId: string, onMessage: (data: any) => void) => {
    const es = new EventSource(`${API_BASE}/jobs/${jobId}/progress`);
    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      onMessage(data);
      if (["completed", "failed", "partial"].includes(data.status)) es.close();
    };
    es.onerror = () => es.close();
    return () => es.close();
  },

  getReaderInfo: (bookId: string) => request<ReaderInfo>(`/books/${bookId}/read`),
  getSectionParagraphs: (bookId: string, sectionId: string, offset = 0, limit = 50) =>
    request<PaginatedParagraphs>(
      `/books/${bookId}/sections/${sectionId}/paragraphs?offset=${offset}&limit=${limit}`
    ),
};
