"""TranslatorAgent — 逐段翻译，支持术语注入"""

from pathlib import Path

from .llm_client import LLMClient


class TranslatorAgent:
    """
    翻译 Agent
    职责：将一段英文文本翻译为中文
    特点：支持术语表注入、可指定翻译风格
    """

    def __init__(self, llm_client: LLMClient = None, glossary: list[dict] = None):
        self.llm = llm_client or LLMClient()
        self.glossary = glossary or []

        # 加载系统 prompt
        prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "translate.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    async def translate(self, text: str, context: str = "") -> str:
        """
        翻译一段文本
        text: 英文原文
        context: 上下文（前一段的译文），用于保持连贯
        """
        messages = [{"role": "system", "content": self._build_system_prompt()}]

        if context:
            messages.append({
                "role": "user",
                "content": f"这是前一段的译文（仅供参考上下文）:\n{context}\n\n---\n\n请翻译这一段:\n{text}",
            })
        else:
            messages.append({"role": "user", "content": text})

        return await self.llm.chat(messages)

    def _build_system_prompt(self) -> str:
        """构建带术语表的 system prompt"""
        base = self.system_prompt or (
            "你是一个专业的中英翻译专家。请将以下英文文本翻译成流畅自然的中文。\n"
            "要求：\n"
            "1. 保持原文的语义准确\n"
            "2. 中文表达流畅自然，符合中文阅读习惯\n"
            "3. 专业术语保持统一\n"
            "4. 只输出译文，不要解释\n"
            "5. 保持原文的段落结构和标点\n"
        )

        if self.glossary:
            terms_str = "\n".join(
                [f"{g['term']} → {g['translation']}" for g in self.glossary]
            )
            base += f"\n\n术语表（请严格遵守）:\n{terms_str}"

        return base
