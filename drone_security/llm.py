"""Claude access through the official Anthropic SDK.

Everything LLM-powered is optional: `llm_available()` gates it, and every caller has a
deterministic fallback. Defaults follow current Claude API guidance:

* model `claude-opus-5-5` (override with DSA_MODEL),
* `output_config.effort` set explicitly (Opus 5.5 defaults to medium; per-frame work
  uses low),
* server-side refusal fallbacks (`fallbacks="default"`), so a safety-classifier decline
  is retried on Anthropic's recommended fallback model instead of failing the call.
"""
from __future__ import annotations

import base64
import os
from typing import Optional, TypeVar

DEFAULT_MODEL = os.getenv("DSA_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T")


class LLMRefusal(RuntimeError):
    """The model (and its fallback) declined the request."""


def llm_available() -> bool:
    """True when the anthropic SDK is installed and a credential is configured.

    DSA_OFFLINE=1 forces offline mode; DSA_LLM=1 forces LLM mode (e.g. when credentials
    come from an `ant auth login` profile rather than an environment variable).
    """
    if os.getenv("DSA_OFFLINE") == "1":
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("DSA_LLM") == "1" or os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


class ClaudeClient:
    def __init__(self, model: str = DEFAULT_MODEL, *, effort: str = "low", client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def parse(self, *, system: str, content, output_format: type[T], max_tokens: int = 8000,
              effort: Optional[str] = None) -> T:
        """Structured output: the response is validated against a Pydantic model."""
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
            output_config={"effort": effort or self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMRefusal(getattr(response.stop_details, "explanation", None) or "request declined")
        return response.parsed_output

    def text(self, *, system: str, content, max_tokens: int = 8000, effort: Optional[str] = None) -> str:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort or self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMRefusal(getattr(response.stop_details, "explanation", None) or "request declined")
        return "".join(block.text for block in response.content if block.type == "text").strip()


def image_block(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(image_bytes).decode()},
    }
