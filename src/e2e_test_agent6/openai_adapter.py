"""OpenAI ↔ Claude Agent SDK message translation.

Pure functions only — no SDK or HTTP imports here. Routes wire this up.
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    role: Role
    # OpenAI accepts str OR list[content-block]; we normalize at use-time
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    user: str | None = None
    # We accept and ignore other OpenAI fields (top_p, max_tokens, etc.) — pydantic extra=allow
    model_config = {"extra": "allow"}

    @field_validator("messages")
    @classmethod
    def _last_must_be_user(cls, msgs: list[ChatMessage]) -> list[ChatMessage]:
        # Defer the "last is user" check to build_prompt so we can raise 400 with a clear msg
        return msgs


def _flatten_content(content: str | list[dict[str, Any]] | None) -> str:
    """Normalize OpenAI content (str or content-block list) to a plain string. Drops images."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        # image_url, input_audio, etc. → silently dropped in v1
    return "\n".join(parts)


def build_prompt(req: ChatCompletionRequest, default_system: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the SDK.

    Uses the OpenAI-style stitched-history strategy: system message wins if present,
    prior turns are flattened into a context block prepended to the latest user message.
    """
    msgs = req.messages
    if msgs[-1].role != "user":
        raise ValueError("last message must have role=user")

    system_msg = next((m for m in msgs if m.role == "system"), None)
    system = _flatten_content(system_msg.content) if system_msg else default_system

    last_text = _flatten_content(msgs[-1].content)
    turns = [m for m in msgs[:-1] if m.role in ("user", "assistant")]

    if not turns:
        return system, last_text

    history = "\n\n".join(
        f"<{m.role}>{_flatten_content(m.content)}</{m.role}>" for m in turns
    )
    prompt = (
        "Prior conversation (for context only — do not repeat):\n"
        f"{history}\n\n"
        f"Latest user message:\n{last_text}"
    )
    return system, prompt


def _base_chunk(model: str, request_id: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
    }


def text_chunk(text: str, *, model: str, request_id: str) -> dict[str, Any]:
    c = _base_chunk(model, request_id)
    c["choices"][0]["delta"] = {"role": "assistant", "content": text}
    return c


def tool_use_chunk(name: str, args: dict[str, Any], *, model: str, request_id: str) -> dict[str, Any]:
    rendered = f"\n```tool\n{name}({json.dumps(args, ensure_ascii=False)})\n```\n"
    c = _base_chunk(model, request_id)
    c["choices"][0]["delta"] = {"content": rendered}
    return c


def tool_result_chunk(content: str, *, model: str, request_id: str) -> dict[str, Any]:
    rendered = f"\n```tool-result\n{content}\n```\n"
    c = _base_chunk(model, request_id)
    c["choices"][0]["delta"] = {"content": rendered}
    return c


def final_chunk(
    *,
    model: str,
    request_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    total_cost_usd: float = 0.0,
) -> dict[str, Any]:
    c = _base_chunk(model, request_id)
    c["choices"][0]["finish_reason"] = "stop"
    # cache_* and total_cost_usd are OpenAI-extension fields: ignored by chat
    # clients (OpenWebUI), picked up by Langfuse via the OTel translator in
    # tracing.py. total_cost_usd is the CLI's own cost figure — authoritative
    # because it knows which Claude variant was actually called.
    c["usage"] = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "total_cost_usd": total_cost_usd,
    }
    return c
