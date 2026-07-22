---
name: pdf-linked-reading
description: PDF 原文联动阅读闭环开发阶段 - 已完成部分和下次继续
metadata:
  type: project
  status: paused
---

# PDF 联动阅读 — 开发进度保存

## ✅ 已完成

### 1. 数据库迁移
- 新增 `paragraph_source_fragments` 表
- `paragraphs` 加 `page_end`, `page_start` 列（可 NULL）
- `book_pages` 加 `page_index`, `rotation` 列
- 通过 `_add_column_if_missing` 兼容旧版本

### 2. 解析器修改 (`backend/parsers/pdf_parser.py`)
- 新增 `_make_source_fragments()` — 按 page_number 分组，同页非连续区域拆为多个 fragment
- 新增 `_build_single_fragment()` — 每个 fragment 包含原始坐标 + 标准化坐标
- 修改 `_merge_para_lines()` — 生成 `source_fragments` 而非合并 bbox
- 修改 `_merge_cross_page_paragraphs()` — 合并 fragments 列表（追加不合并 bbox）
- 修改 `_detect_chapters()` — 首个匹配前的段落放入 "Front Matter" 章节，不再丢失
- 修改 `_build_output()` — 输出包含 `source_fragments`

### 3. 后端 API
- `GET /api/books/{book_id}/read` — 阅读页初始化（book info + sections）
- `GET /api/books/{book_id}/pdf` — PDF 文件流（用于 PDF.js 加载）
- `GET /api/books/{book_id}/sections/{section_id}/paragraphs?offset=0&limit=50` — 分段分页

### 4. 数据迁移
- `backend/scripts/migrate_paragraphs.py` — 从 book_pages.lines_json 重跑 assemble 生成新段落 + fragments
- 使用文本相似度匹配迁移已有翻译
- 274 页书已迁移完成：
  - 24 章（含 Front Matter）
  - 1914 段落
  - 3401 source_fragments（平均 1.8/段落）
  - 13 个旧翻译 100% 匹配保留
- book_pages.page_index = page_number - 1 已回填
- rotation 已从原 PDF 读取

### 5. 前端依赖
- `pdfjs-dist@4.0.379` 已安装

## 📌 下次继续

### ReaderPage.tsx — 三栏布局
- 左栏: PDF viewer（32% 宽度，sticky）
- 中栏: 英文原文（34%）
- 右栏: 中文译文（34%）
- 英文和中文合并为同一滚动容器
- 每个段落一个双语单元

### PdfViewer 组件 (`frontend/src/components/PdfViewer.tsx`)
- 使用 pdfjs-dist，canvas 自定义渲染
- 单页显示模式
- 覆盖层半透明高亮
- 页码显示
- 上一页/下一页按钮

### PDF 适配层 (`frontend/src/lib/pdfAdapter.ts`)
- 唯一的 +1/-1 转换层
- `toPdfJsPage(index)` 和 `toPageIndex(number)`

### active_paragraph 检测
- IntersectionObserver
- 视口中间 40% 区域
- 200ms 防抖

### PDF 联动
- active_paragraph → source_fragments[0] → PDF 翻页
- bbox_normalized → 高亮区域

### 手动暂停
- PDF 交互时暂停自动跟随
- "跟随已暂停" 提示
- "回到当前正文" 按钮

## 验收条件
- 三栏显示正常
- 滚动自动翻页
- 高亮覆盖正确区域
- 手动翻页暂停跟随
- 恢复跟随回到正确页
- 抽样 20 个段落记录

## 后端注意
- 后端运行在端口 8000（`start.ps1` 配置 8000，`vite.config.ts` proxy 到 8000）
- MinerU 已弃用，不再需要
