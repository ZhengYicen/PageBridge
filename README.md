# 📖 AI 双语阅读器

上传英文书（PDF/EPUB）→ 解析结构 → 选择章节 → 自动翻译 → 双语对照阅读。

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- （可选）Docker + NVIDIA GPU — 用于 PDF 扫描版解析（MinerU）

### 1. 配置 API Key

```bash
# 复制配置模板
cp .env.example .env
```

编辑 `.env`，填入你的 DeepSeek API Key：

```env
DEEPSEEK_API_KEY=sk-your-key-here
```

> 也支持 OpenAI / Qwen / GLM，切换 `LLM_PROVIDER` 并设置对应的 API Key 即可。

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

API 文档自动可用：http://localhost:8000/docs

### 3. 启动前端（新开一个终端）

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173

### 4. （可选）启动 MinerU — PDF 扫描版解析

```bash
docker compose --profile mineru up -d
```

> MinerU 镜像约 10GB，首次启动需下载模型。如无 GPU，设置环境变量 `MINERU_URL=http://localhost:8000` 并用 CPU 模式启动 MinerU（详见 MinerU 文档）。

## 项目结构

```
ai-reader/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置
│   ├── database.py          # SQLite 数据库
│   ├── models.py            # 数据模型
│   ├── routers/
│   │   ├── upload.py        # 文件上传
│   │   ├── books.py         # 书籍/章节 API
│   │   ├── chapters.py      # 翻译启停
│   │   └── jobs.py          # 任务管理 + SSE
│   ├── parsers/
│   │   ├── epub_parser.py   # EPUB 解析
│   │   └── pdf_parser.py    # PDF 解析（MinerU + PyMuPDF fallback）
│   ├── agents/
│   │   ├── llm_client.py    # LLM 统一调用层
│   │   └── translator.py    # TranslatorAgent
│   ├── worker.py            # 后台任务管理
│   └── prompts/
│       └── translate.txt    # 翻译 prompt
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── UploadPage.tsx   # 上传页
│   │   │   ├── BookPage.tsx     # 书籍详情 + 章节列表
│   │   │   └── ReaderPage.tsx   # 双语阅读页
│   │   └── api/client.ts        # API 封装
│   └── ...
├── storage/                 # 上传文件 + 数据库
├── docker-compose.yml
└── .env.example
```

## 核心设计

### 翻译流程

```
用户选择章节 → 创建翻译任务 → 后台逐段翻译
                                ↓
                     每翻一段写入数据库（断点续译）
                                ↓
                     暂停/继续/失败重试
```

### 段落级双语对照

- 原文和译文通过 `paragraph_id` 一一对应
- 左栏英文，右栏中文
- 滚动同步（IntersectionObserver）
- 点击段落高亮对应段落

### PDF 解析策略

1. **首选**：MinerU API（Docker 部署）— 处理扫描版、复杂排版
2. **Fallback**：PyMuPDF — 处理可复制文本 PDF

## API 概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/upload` | POST | 上传 EPUB/PDF |
| `/api/books` | GET | 书籍列表 |
| `/api/books/{id}` | GET | 书籍详情 + 章节 |
| `/api/books/{id}/parse` | POST | 解析书籍 |
| `/api/chapters/{id}/paragraphs` | GET | 获取段落 |
| `/api/chapters/{id}/translate` | POST | 启动翻译 |
| `/api/jobs/{id}` | GET | 任务状态 |
| `/api/jobs/{id}/pause` | POST | 暂停 |
| `/api/jobs/{id}/resume` | POST | 继续 |
| `/api/jobs/{id}/retry` | POST | 重试失败段 |
| `/api/jobs/{id}/progress` | GET | SSE 进度流 |
