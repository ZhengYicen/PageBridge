"""TranslatorAgent — 批量翻译 + 缓存，支持术语注入"""

import json
import logging
import time
from pathlib import Path

from backend.agents.llm_client import LLMClient
from backend.database import get_connection, source_hash

logger = logging.getLogger("ai-reader.agent.translate")

# 默认 batch 大小
BATCH_SIZE = 8


class TranslatorAgent:
    """
    翻译 Agent
    职责：
    - 批量翻译（一次 API 请求翻多个段落）
    - 缓存命中直接返回（source_hash 匹配）
    - 支持术语表注入、上下文连贯
    """

    def __init__(self, llm_client: LLMClient = None, glossary: list[dict] = None):
        self.llm = llm_client or LLMClient()
        self.glossary = glossary or []

        prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "translate.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else (
            "你是一个专业的中英翻译专家。请将以下英文文本翻译成流畅自然的中文。\n"
            "要求：\n"
            "1. 保持原文的语义准确\n"
            "2. 中文表达流畅自然，符合中文阅读习惯\n"
            "3. 专业术语保持统一\n"
            "4. 只输出译文，不要解释\n"
            "5. 保持原文的段落结构和标点\n"
        )

    # ── 单段翻译（兼容旧接口） ──────────────────────────

    async def translate(self, text: str, context: str = "") -> str:
        """单段翻译（兼容）"""
        batch = await self.translate_batch([{"text": text}], context)
        return batch[0]["translation"] if batch else ""

    # ── 批量翻译（核心） ────────────────────────────────

    async def translate_batch(
        self,
        paragraphs: list[dict],
        context: str = "",
        batch_size: int = BATCH_SIZE,
    ) -> list[dict]:
        """
        批量翻译段落。

        paragraphs: [{"id": str, "text": str, "chapter_id": str}, ...]
                    id 可选，无 id 时按索引对应。
        返回: [{"id": str, "translation": str, "cached": bool, "error": str | None}, ...]

        流程：
          1. 对每个段落计算 source_hash，查 translations 缓存
          2. 缓存命中的直接返回
          3. 未命中的按 batch_size 分批调用 API
          4. 解析 API 返回的 JSON，写入 DB 缓存
        """
        if not paragraphs:
            return []

        total = len(paragraphs)
        results: list[dict] = []
        batch_to_translate: list[dict] = []  # 需要调用 API 的段落
        batch_idx_map: list[int] = []         # 在 results 中的索引

        step_lookup_start = time.monotonic()

        for i, para in enumerate(paragraphs):
            text = para.get("text", "")
            if not text.strip():
                results.append({"id": para.get("id"), "translation": "", "cached": False, "error": None})
                continue

            h = source_hash(text)

            # 查缓存
            cached = self._lookup_cache(h)
            if cached is not None:
                results.append({
                    "id": para.get("id"),
                    "translation": cached,
                    "cached": True,
                    "error": None,
                })
                continue

            # 需要翻译
            results.append({"id": para.get("id"), "translation": "", "cached": False, "error": None})
            batch_to_translate.append({
                "id": para.get("id", str(i)),
                "text": text,
                "hash": h,
                "chapter_id": para.get("chapter_id"),
            })
            batch_idx_map.append(i)

        step_lookup_elapsed = time.monotonic() - step_lookup_start
        if batch_to_translate:
            logger.info(
                "缓存查找: %d/%d 命中, %d 待翻译 (%.1fs)",
                total - len(batch_to_translate), total, len(batch_to_translate),
                step_lookup_elapsed,
            )
        else:
            logger.info("缓存查找: 全部命中 %d 段 (%.1fs)", total, step_lookup_elapsed)
            return results

        # 分批调用 API
        for batch_start in range(0, len(batch_to_translate), batch_size):
            batch = batch_to_translate[batch_start:batch_start + batch_size]
            batch_indices = batch_idx_map[batch_start:batch_start + batch_size]

            # 构造带上下文的请求
            api_start = time.monotonic()
            batch_translations = await self._call_api_batch(batch, context)
            api_elapsed = time.monotonic() - api_start

            char_count = sum(len(b["text"]) for b in batch)
            logger.info(
                "批量翻译: %d 段 %d 字符 (%.1fs, %.0f 字符/秒)",
                len(batch), char_count, api_elapsed,
                char_count / api_elapsed if api_elapsed > 0 else 0,
            )

            # 写入结果
            write_start = time.monotonic()
            for j, (item, trans) in enumerate(zip(batch, batch_translations)):
                idx = batch_indices[j]
                results[idx]["translation"] = trans.get("translation", "")
                if trans.get("error"):
                    results[idx]["error"] = trans["error"]
                else:
                    # 写入缓存
                    self._write_cache(item, trans["translation"])
            write_elapsed = time.monotonic() - write_start
            if write_elapsed > 0.1:
                logger.info("  缓存写入: %d 条 (%.1fs)", len(batch), write_elapsed)

        return results

    # ── 缓存操作 ────────────────────────────────────────

    def _lookup_cache(self, h: str) -> str | None:
        """根据 source_hash 查找缓存译文"""
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT translated_text FROM translations WHERE source_hash=? AND status='completed' LIMIT 1",
                (h,),
            ).fetchone()
            conn.close()
            return row["translated_text"] if row else None
        except Exception:
            return None

    def _write_cache(self, item: dict, translation: str):
        """将翻译结果写入 translations 表"""
        try:
            conn = get_connection()
            conn.execute(
                "INSERT OR REPLACE INTO translations "
                "(id, paragraph_id, source_hash, target_lang, engine, prompt_version, translated_text, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"{item['hash']}_zh",
                    item.get("id", ""),
                    item["hash"],
                    "zh",
                    "deepseek",
                    "v1",
                    translation,
                    "completed",
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("缓存写入失败: %s", e)

    # ── API 调用 ────────────────────────────────────────

    async def _call_api_batch(self, batch: list[dict], context: str) -> list[dict]:
        """
        调用 API 翻译一个 batch。
        batch: [{"id": str, "text": str, ...}]
        返回: [{"id": str, "translation": str, "error": str | None}, ...]
        """
        if not batch:
            return []

        # 构造 system prompt
        system = self._build_system_prompt()
        if context:
            system += f"\n\n以下为前一段的译文，保持风格一致：\n{context[:500]}"

        # 构造 items（只传 id 和 text）
        items = [{"id": b.get("id", str(i)), "text": b["text"]} for i, b in enumerate(batch)]

        try:
            raw = await self.llm.chat_batch(system, items)
            return self._parse_batch_response(raw, batch)
        except Exception as e:
            logger.error("批量 API 调用失败: %s", e)
            # fallback: 逐段翻译
            return await self._fallback_one_by_one(batch)

    def _parse_batch_response(self, raw: str, batch: list[dict]) -> list[dict]:
        """解析 API 返回的 JSON 数组"""
        # 尝试提取 JSON 数组
        text = raw.strip()
        # 去掉可能的 markdown 代码块标记
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        if text.startswith("```json"):
            text = text[7:]
            text = text.rsplit("```", 1)[0]

        text = text.strip()

        try:
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("响应不是数组")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("JSON 解析失败 (%s), 尝试逐段 fallback: %s", e, raw[:200])
            return self._fallback_one_by_one(batch)

        # 构建 id→translation 映射
        trans_map = {}
        for item in data:
            tid = item.get("id", "")
            trans = item.get("translation", "")
            if tid:
                trans_map[tid] = trans

        results = []
        for i, b in enumerate(batch):
            bid = b.get("id", str(i))
            translation = trans_map.get(bid, "")
            if not translation:
                logger.warning("batch 中缺少 id=%s 的译文", bid)
            results.append({"id": bid, "translation": translation, "error": None if translation else "missing"})
        return results

    async def _fallback_one_by_one(self, batch: list[dict]) -> list[dict]:
        """逐段翻译 fallback"""
        logger.info("逐段 fallback: %d 段", len(batch))
        results = []
        for b in batch:
            try:
                trans = await self.llm.chat([
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": b["text"]},
                ])
                results.append({"id": b.get("id"), "translation": trans.strip(), "error": None})
            except Exception as e:
                results.append({"id": b.get("id"), "translation": "", "error": str(e)})
        return results

    # ── Prompt 构建 ─────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """构建带术语表的 system prompt"""
        base = self.system_prompt
        if self.glossary:
            terms_str = "\n".join(
                [f"{g['term']} → {g['translation']}" for g in self.glossary]
            )
            base += f"\n\n术语表（请严格遵守）:\n{terms_str}"
        return base
