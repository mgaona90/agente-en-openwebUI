"""POST /v1/chat/completions — OpenAI-compatible streaming + non-streaming."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..agent import run_agent
from ..config import get_settings
from ..openai_adapter import (
    ChatCompletionRequest,
    build_prompt,
    final_chunk,
    text_chunk,
    tool_use_chunk,
    tool_result_chunk,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    settings = get_settings()
    request_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    try:
        system_prompt, prompt = build_prompt(req, default_system=settings.default_system_prompt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _maybe_enrich_trace(req, request)

    if req.stream:
        return StreamingResponse(
            _stream_sse(req, request_id, prompt, system_prompt, settings),
            media_type="text/event-stream",
        )
    return JSONResponse(
        await _collect_non_streaming(req, request_id, prompt, system_prompt, settings)
    )


def _maybe_enrich_trace(req: ChatCompletionRequest, request: Request) -> None:
    settings = get_settings()
    if not settings.tracing_enabled:
        return
    try:
        from langfuse import get_client

        get_client().update_current_trace(
            name="chat-completion",
            user_id=req.user,
            session_id=request.headers.get("x-openwebui-chat-id"),
            metadata={
                "model": req.model,
                "stream": req.stream,
                "n_messages": len(req.messages),
            },
            tags=["claude-sdk-agent"],
        )
    except Exception as e:
        logger.debug("trace enrichment failed: %s", e)


def _mcp_servers_from_settings(settings) -> dict:
    """Wire LiteLLM's MCP gateway as an HTTP MCP server for the SDK.

    Returns {} when no virtual key is set, in which case the agent runs with
    only its built-in tools (Read/Glob/WebFetch).
    """
    if not settings.mcp_enabled:
        return {}
    return {
        "scanntech": {
            "type": "http",
            "url": settings.litellm_mcp_url,
            "headers": {"Authorization": f"Bearer {settings.litellm_virtual_key}"},
        }
    }


async def _stream_sse(req, request_id, prompt, system_prompt, settings) -> AsyncIterator[bytes]:
    try:
        async for ev in run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            model=settings.agent_model,
            max_turns=settings.max_turns,
            expose_tool_results=settings.expose_tool_results,
            mcp_servers=_mcp_servers_from_settings(settings),
        ):
            chunk = _event_to_chunk(ev, model=req.model, request_id=request_id)
            if chunk is None:
                continue
            yield f"data: {json.dumps(chunk)}\n\n".encode()
    except Exception as e:
        logger.exception("agent run failed")
        err = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": req.model,
            "choices": [{"index": 0, "delta": {"content": f"\n[error] {e}"}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(err)}\n\n".encode()
    yield b"data: [DONE]\n\n"


def _event_to_chunk(ev: tuple, *, model: str, request_id: str) -> dict | None:
    kind = ev[0]
    if kind == "text":
        return text_chunk(ev[1], model=model, request_id=request_id)
    if kind == "tool_use":
        return tool_use_chunk(ev[1], ev[2], model=model, request_id=request_id)
    if kind == "tool_result":
        return tool_result_chunk(ev[1], model=model, request_id=request_id)
    if kind == "done":
        return final_chunk(model=model, request_id=request_id, **ev[1])
    return None


async def _collect_non_streaming(req, request_id, prompt, system_prompt, settings) -> dict:
    parts: list[str] = []
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_cost_usd": 0.0,
    }
    try:
        async for ev in run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            model=settings.agent_model,
            max_turns=settings.max_turns,
            expose_tool_results=settings.expose_tool_results,
            mcp_servers=_mcp_servers_from_settings(settings),
        ):
            if ev[0] == "text":
                parts.append(ev[1])
            elif ev[0] == "tool_use":
                args_json = json.dumps(ev[2], ensure_ascii=False)
                parts.append(f"\n```tool\n{ev[1]}({args_json})\n```\n")
            elif ev[0] == "tool_result":
                parts.append(f"\n```tool-result\n{ev[1]}\n```\n")
            elif ev[0] == "done":
                usage = ev[1]
    except Exception as e:
        logger.exception("agent run failed")
        raise HTTPException(status_code=502, detail=f"agent run failed: {e}")

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(parts)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage["input_tokens"],
            "completion_tokens": usage["output_tokens"],
            "total_tokens": usage["input_tokens"] + usage["output_tokens"],
            "cache_read_input_tokens": usage["cache_read_input_tokens"],
            "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
            "total_cost_usd": usage["total_cost_usd"],
        },
    }
