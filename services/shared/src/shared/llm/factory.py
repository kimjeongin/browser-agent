"""Factory function for creating Ollama-backed LLM instances."""

from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from shared.llm.settings import LLMSettings


def create_ollama_llm(
    model: str,
    settings: LLMSettings,
    *,
    streaming: bool = True,
) -> BaseChatModel:
    """Create a ``ChatOllama`` instance with shared configuration.

    Important:
        ``format="json"`` is intentionally **never** set because it
        conflicts with tool-calling functionality.

        ``think=False`` is set globally to disable extended-thinking mode
        for qwen3 series models. Thinking mode adds latency and can produce
        large intermediate tokens that interfere with tool-calling and
        multi-agent message passing. Non-qwen3 models safely ignore this
        parameter.

    Args:
        model: Ollama model name (e.g. ``"qwen3:8b"``).
        settings: Shared LLM settings.
        streaming: Whether to enable streaming output. Defaults to ``True``.

    Returns:
        A ready-to-use ``BaseChatModel`` instance.
    """
    return ChatOllama(
        model=model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        num_ctx=settings.llm_num_ctx,
        streaming=streaming,
        think=False,
    )
