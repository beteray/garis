"""Vendor adapters. Add a file, list it in ``build_providers``, and stop."""

from .anthropic import AnthropicProvider
from .fake import FakeProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "FakeProvider",
    "GeminiProvider",
    "OllamaProvider",
    "OpenAIProvider",
]
