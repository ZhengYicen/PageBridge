"""LLM API 统一封装层 — 支持 OpenAI / DeepSeek / Qwen / GLM"""

import json
import time
from typing import Optional

import httpx

from backend.config import LLM_CONFIG


class LLMClient:
    """
    统一 LLM 调用客户端
    所有提供商使用 OpenAI 兼容接口，通过 base_url 区分
    """

    def __init__(self, config: dict = None):
        cfg = config or LLM_CONFIG
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.temperature = cfg["temperature"]
        self.max_tokens = cfg["max_tokens"]

    @property
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
    ) -> str:
        """
        发送对话请求，返回文本内容
        messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": False,
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            elapsed = time.monotonic() - start
            content = data["choices"][0]["message"]["content"]
            # 记录简要日志
            import logging
            logger = logging.getLogger("ai-reader.llm")
            logger.info(
                "chat: model=%s in=%d out=%d %.1fs",
                self.model,
                sum(len(m.get("content", "")) for m in messages),
                len(content),
                elapsed,
            )
            return content

    async def chat_batch(
        self,
        system_prompt: str,
        items: list[dict],
        user_prompt_template: str = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 300,
    ) -> str:
        """
        批量对话请求：将多个 item 合并到一次 API 调用。
        items: [{"id": str, "text": str, ...}, ...]
        返回模型原始回复文本（需调用方解析 JSON）。
        """
        # 构造 user message：将 items 序列化为 JSON
        if user_prompt_template:
            user_content = user_prompt_template.format(items=json.dumps(items, ensure_ascii=False))
        else:
            user_content = (
                "Translate the following paragraphs into Chinese.\n"
                "Return a JSON array where each item has 'id' and 'translation'.\n"
                "Only output valid JSON, no explanation.\n\n"
                f"{json.dumps(items, ensure_ascii=False)}"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        return await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens or (self.max_tokens * max(1, len(items))),
            timeout=timeout,
        )

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """流式版本，供后续 SSE 使用"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", url, headers=self._headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except json.JSONDecodeError:
                            continue
