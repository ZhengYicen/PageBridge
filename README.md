# 📖 PageBridge

**Bilingual reading, reimagined.** Upload English books (PDF/EPUB), auto-parse structure, browse chapters, batch-translate into Chinese, and read side-by-side with an integrated PDF viewer.

> 🚧 **Actively developed.** The focus is PDF with OCR — EPUB is supported but has had less battle testing.

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| 📤 | **Smart Upload** | Drag & drop PDF or EPUB files |
| 🔍 | **Intelligent Parsing** | PDF: native text extraction → RapidOCR fallback with auto DPI retry. EPUB: semantic extraction via spine/TOC |
| 🌐 | **Batch Translation** | LLM-powered (DeepSeek / OpenAI / Qwen). 8 paragraphs per request, cached via `source_hash` |
| 📖 | **Bilingual Reader** | Three-column layout: PDF viewer + English text + Chinese translation |
| 🔗 | **PDF–Text Linking** | Scroll the text → PDF auto-flips to the right page and highlights the paragraph region |
| ⏸️ | **Follow Control** | Manually browsing PDF pauses auto-follow; "Resume" snaps back to the active paragraph |
| ⚡ | **Background Jobs** | Parse books page-by-page with progress push; translate asynchronously with pause/resume/retry via SSE |
| 📚 | **Chapter Navigation** | Auto-detect chapter boundaries. Jump to any chapter, or read the full book as one continuous scroll |
| 🎯 | **Cached Translations** | MD5-hash-based translation cache — re-parsing a book re-matches existing translations by text content |

---

## 🖼️ Screenshots

<details>
<summary><b>Upload page</b></summary>

A clean drag-and-drop upload zone with a list of uploaded books showing parsing status and quick actions (open / re-parse / delete).

</details>

<details>
<summary><b>Book detail page</b></summary>

Shows book metadata, parsing progress bar, and a chapter list with per-chapter translate / pause / read buttons. Translation progress updates in real-time via SSE.

</details>

<details>
<summary><b>Reader (three-column layout)</b></summary>

```
┌─────────────┬──────────────────┬──────────────────┐
│  PDF Viewer  │  English Text     │  Chinese Translation │
│  (32%)       │  (34%)            │  (34%)               │
│              │                   │                      │
│  [page]      │  Paragraph 1      │  段落 1              │
│  highlights  │  Paragraph 2      │  段落 2              │
│              │  ...              │  ...                 │
│  ◀ 3/274 ▶   │                   │                      │
├──────────────┴──────────────────┴──────────────────┤
│ ● 联动中      已翻译 142/210 段                       │
└─────────────────────────────────────────────────────┘
```

- Scroll English/Chinese text together as one scrollable column
- Active paragraph is highlighted; its PDF source page auto-opens
- PDF region shows semi-transparent yellow highlight over the original paragraph area
- Zoom in/out, keyboard navigation (↑↓ / PageUp / PageDown)

</details>

---

## 🧱 Architecture

```
┌──────────────┐    ┌──────────────────────────────────┐
│   Browser    │    │  FastAPI Backend (port 8000)      │
│  (React 18)  │    │                                  │
│              │    │  ┌──────────┐  ┌──────────────┐  │
│  UploadPage  │────┼─▶│ Routers  │  │ Parsers       │  │
│  BookPage    │    │  │          │  │              │  │
│  ReaderPage  │    │  │ upload   │  │ PdfParser    │  │
│              │    │  │ books    │  │  ├─ RapidOCR  │  │
│  PdfViewer   │    │  │ chapters │  │  └─ PyMuPDF   │  │
│  (pdfjs-dist)│    │  │ jobs     │  │              │  │
│              │    │  │ paragraphs│  │ EpubParser   │  │
│              │    │  └──────────┘  └──────────────┘  │
│              │    │                                  │
│              │    │  ┌──────────┐  ┌──────────────┐  │
│              │    │  │Worker    │  │LLM Client    │  │
│              │    │  │(asyncio) │──│(httpx)       │  │
│              │    │  │JobManager│  │DeepSeek/... │  │
│              │    │  └──────────┘  └──────────────┘  │
│              │    │                                  │
│              │    │  ┌──────────────────────────────┐ │
│              │    │  │  SQLite (storage/app.db)     │ │
│              │    │  │  books / chapters / paras    │ │
│              │    │  │  translations / jobs / pages │ │
│              │    │  └──────────────────────────────┘ │
└──────────────┘    └──────────────────────────────────┘
```

### Key Design Decisions

- **PDF parsing**: Native text extraction first (PyMuPDF); falls back to RapidOCR for scanned pages. Auto-retries at higher DPI when quality is insufficient. Two-phase: parse page-by-page → assemble into chapters and paragraphs.
- **EPUB parsing**: Reads documents in spine order, builds a semantic block stream (paragraphs / headings / images / quotes), locates TOC entries as cut points, then slices into chapters.
- **Paragraph sources**: Each paragraph stores `source_fragments` — one per contiguous region on a PDF page (cross-page paragraphs merge their fragment lists). Each fragment carries absolute + normalized bbox coordinates for PDF highlight overlay.
- **Translation pipeline**: Chapter → batch (8 paras) → cache lookup → API call → per-paragraph DB write. Cache keyed by MD5 of source text, so re-parsing a book preserves existing translations.
- **Pagination**: Reader loads 50 paragraphs at a time, infinite-scroll. All sections share one virtual `__full__` section that concatenates every chapter.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- An LLM API key (DeepSeek recommended; also supports OpenAI / Qwen)

### 1. Clone & configure

```bash
git clone https://github.com/your-username/pagebridge.git
cd pagebridge

cp .env.example .env
# Edit .env — set your DEEPSEEK_API_KEY
```

### 2. Start backend

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS / Linux

pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 3. Start frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

### Docker (recommended)

```bash
cp .env.example .env
# Edit .env — set your DEEPSEEK_API_KEY

docker compose up --build
```

- **Frontend**: http://localhost:5173
- **Backend health**: http://localhost:8000/health
- **API docs**: http://localhost:8000/docs

Stop with `docker compose down`. Data persists in `./storage/` on the host.

---

### 4. Use it

1. Drop a PDF or EPUB onto the upload area
2. Click the book → **Parse** button (page-by-page OCR/text extraction)
3. Once parsing completes, click **Translate** on a chapter
4. When translation finishes, click **Read** to open the bilingual reader

---

## ⚙️ Configuration

All config lives in `.env` (copy from `.env.example`):

```env
# ── LLM ──────────────────────────────────────
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=2048

# Also supports OpenAI / Qwen — see .env.example
```

---

## 📁 Project Structure

```
pagebridge/
├── backend/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Environment config
│   ├── database.py          # SQLite init + migrations
│   ├── models.py            # Pydantic request/response schemas
│   ├── routers/
│   │   ├── upload.py        # File upload endpoint
│   │   ├── books.py         # Book CRUD, parse, progress, reader info
│   │   ├── chapters.py      # Chapter paragraphs + translate trigger
│   │   ├── paragraphs.py    # Section paragraphs (paginated, with fragments)
│   │   └── jobs.py          # Job status + SSE progress stream
│   ├── parsers/
│   │   ├── pdf_parser.py    # PDF parser (RapidOCR + PyMuPDF)
│   │   └── epub_parser.py   # EPUB parser (ebooklib + BeautifulSoup)
│   ├── agents/
│   │   ├── llm_client.py    # Unified LLM API client (httpx)
│   │   └── translator.py    # Batch translator with cache
│   ├── worker.py            # Async job manager (translate jobs)
│   ├── scripts/
│   │   └── migrate_paragraphs.py  # Data migration tool
│   └── prompts/
│       └── translate.txt    # Translation system prompt
├── frontend/
│   ├── src/
│   │   ├── App.tsx          # Shell + routing + nav bar
│   │   ├── pages/
│   │   │   ├── UploadPage.tsx   # Upload + book list
│   │   │   ├── BookPage.tsx     # Book detail + chapter list
│   │   │   └── ReaderPage.tsx   # Bilingual reader (three-column)
│   │   ├── components/
│   │   │   └── PdfViewer.tsx    # PDF.js canvas renderer + highlights
│   │   ├── lib/
│   │   │   └── pdfAdapter.ts    # Page number conversion layer
│   │   └── api/
│   │       └── client.ts        # API client
│   └── index.html
├── storage/                 # Uploaded files, database, backups
│   ├── uploads/
│   ├── books/
│   └── app.db
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
└── .env.example
```

---

## 🧪 Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18, React Router 6, TypeScript, Tailwind CSS, Vite |
| **PDF Rendering** | pdfjs-dist 4.0 (canvas rendering + overlay highlights) |
| **Backend** | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| **Database** | SQLite (WAL mode, with migration helpers) |
| **PDF Parsing** | RapidOCR (ONNX Runtime) + PyMuPDF (fitz) |
| **EPUB Parsing** | ebooklib + BeautifulSoup 4 + lxml |
| **LLM Client** | httpx (async), OpenAI-compatible API |
| **Infrastructure** | Docker, Docker Compose |

---

## 📊 Database Schema (key tables)

```
books            → chapters       → paragraphs      → paragraph_source_fragments
                                      ↕ translations (cached)
                  book_pages (per-page parse results)
                  jobs (translate jobs with progress)
                  glossary (terms → custom translations)
```

---

## 👨‍💻 Development

```bash
# Backend (hot-reload)
uvicorn backend.main:app --reload --port 8000

# Frontend (HMR on port 5173, proxies /api → :8000)
cd frontend && npm run dev
```

The Vite dev server proxies `/api` to the backend. Default backend port is 8000; adjust `frontend/vite.config.ts` if yours differs.

---

## 🤝 Contributing

Contributions are welcome! This project is in active development. Areas that would benefit most:

- **More LLM providers** — the client abstraction is ready, just needs config wiring
- **EPUB polish** — better image extraction, complex layout handling
- **PDF reading order** — the heuristic works for single/double column but won't handle every layout
- **Glossary UI** — a frontend page to manage term→translation mappings
- **i18n** — the UI is currently Chinese-only; English localization would broaden the audience

---

## 📄 License

MIT
