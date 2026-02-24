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

    Args:
        model: Ollama model name (e.g. ``"llama3.1:8b"``).
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
    )
