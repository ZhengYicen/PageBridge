const API_BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
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
  file_path: string;
  parse_status: string;
  total_chapters: number;
  total_pages: number;
  parsed_pages: number;
  failed_pages: number;
  current_stage: string;
  error_message: string;
  created_at: string;
  uploaded_at?: string;
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
  page_number: number;
  source_bbox: string;
  translation: string;
  status: string;
  type?: string;
  error_message: string;
  updated_at: string;
}

export interface Job {
  id: string;
  chapter_id: string;
  status: string;
  total_paragraphs: number;
  completed_paragraphs: number;
  failed_paragraphs: number;
  job_type: string;
  created_at: string;
  updated_at: string;
}

export const api = {
  // 上传
  upload: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail);
    return res.json();
  },

  // 书籍
  listBooks: () => request<{ books: Book[] }>("/books"),
  getBook: (id: string) => request<Book>(`/books/${id}`),
  parseBook: (id: string) => request<{ book_id: string; status: string; total_pages?: number }>(
    `/books/${id}/parse`,
    { method: "POST" }
  ),
  getBookProgress: (id: string) => request<BookProgress>(`/books/${id}/progress`),

  // 章节
  getParagraphs: (id: string) =>
    request<{ chapter: Chapter; paragraphs: Paragraph[] }>(`/chapters/${id}/paragraphs`),
  translateChapter: (id: string) =>
    request<{ job_id: string; chapter_id: string; status: string }>(`/chapters/${id}/translate`, { method: "POST" }),

  // 任务
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  pauseJob: (id: string) => request<{ status: string }>(`/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => request<{ status: string }>(`/jobs/${id}/resume`, { method: "POST" }),
  retryJob: (id: string) => request<{ status: string }>(`/jobs/${id}/retry`, { method: "POST" }),

  // 书籍管理
  deleteBook: (id: string) => request<{ status: string }>(`/books/${id}`, { method: "DELETE" }),

  // 翻译状态轮询
  getParagraphTranslations: (chapterId: string) =>
    request<{
      chapter_id: string;
      chapter_title: string;
      translate_status: string;
      total: number;
      completed: number;
      paragraphs: Paragraph[];
    }>(`/paragraphs/${chapterId}/translations`),

  // 预翻译
  preTranslateChapter: (chapterId: string) =>
    request<{ job_id?: string; chapter_id: string; status: string; message?: string; pending?: number }>(
      `/chapters/${chapterId}/pre-translate`, { method: "POST" }
    ),

  subscribeProgress: (jobId: string, onMessage: (data: any) => void) => {
    const es = new EventSource(`${API_BASE}/jobs/${jobId}/progress`);
    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      onMessage(data);
      if (["completed", "failed", "partial"].includes(data.status)) {
        es.close();
      }
    };
    es.onerror = () => es.close();
    return () => es.close();
  },
};
