from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_openrouter import ChatOpenRouter

from rag.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_REASONING_VISIBLE,
    validate_llm_config,
)


def create_chat_model(
    *,
    max_tokens: int,
    temperature: float = 0,
    max_retries: int = 2,
) -> BaseChatModel:
    validate_llm_config()

    common_arguments = {
        "model": LLM_MODEL,
        "api_key": LLM_API_KEY,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_retries": max_retries,
    }

    if LLM_PROVIDER == "openrouter":
        return ChatOpenRouter(
            **common_arguments,
        )

    if LLM_PROVIDER == "groq":
        model_name = LLM_MODEL.lower()

        # Qwen 3 models can disable thinking completely.
        if "qwen3" in model_name:
            if LLM_REASONING_VISIBLE:
                return ChatGroq(
                    **common_arguments,
                    reasoning_effort="default",
                    reasoning_format="parsed",
                )

            return ChatGroq(
                **common_arguments,
                reasoning_effort="none",
            )

        # GPT-OSS controls whether reasoning is included
        # in the returned response.
        if model_name.startswith("openai/gpt-oss"):
            return ChatGroq(
                **common_arguments,
                include_reasoning=LLM_REASONING_VISIBLE,
            )

        # Normal Groq models without reasoning controls.
        return ChatGroq(
            **common_arguments,
        )

    if LLM_PROVIDER == "openai_compatible":
        return ChatOpenAI(
            **common_arguments,
            base_url=LLM_BASE_URL,
        )

    raise RuntimeError(
        f"Unsupported LLM provider: {LLM_PROVIDER}"
    )