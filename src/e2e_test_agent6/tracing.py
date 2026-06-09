"""Langfuse tracing setup for the Claude Agent SDK.

Near-verbatim port of:
  ~/repos/mlops-agent/experiments/test-agents-sdk/radar.py:_configure_tracing
  ~/repos/mlops-agent/experiments/test-agents-sdk/radar.py:_install_usage_translator

Critical: configure() must run BEFORE any import of claude_agent_sdk anywhere
in the process. The langsmith integration patches ClaudeSDKClient at the time
configure_claude_agent_sdk() runs, not at import.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_configured = False


def configure() -> bool:
    """Wire claude-agent-sdk OTel instrumentation into Langfuse.

    Returns True if tracing is set up, False if Langfuse env vars are missing
    (in which case the agent runs untraced).
    """
    global _configured
    if _configured:
        return True

    # New convention (Vault field names from mlops/services/langfuse via VSO):
    #   agents_project_api_public_key / agents_project_api_secret_key
    # Legacy: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY (pre-Vault deploys).
    pk = (
        os.environ.get("agents_project_api_public_key")
        or os.environ.get("LANGFUSE_PUBLIC_KEY")
    )
    sk = (
        os.environ.get("agents_project_api_secret_key")
        or os.environ.get("LANGFUSE_SECRET_KEY")
    )
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
    if not (pk and sk and host):
        logger.warning(
            "Langfuse keys / host not all set — running without traces. "
            "Looked for (agents_project_api_public_key | LANGFUSE_PUBLIC_KEY), "
            "(agents_project_api_secret_key | LANGFUSE_SECRET_KEY), and "
            "(LANGFUSE_HOST | LANGFUSE_BASE_URL)."
        )
        return False

    # Export under the SDK-expected names so langfuse.get_client() auto-init
    # picks them up. setdefault: don't clobber a value the caller explicitly set.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", pk)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", sk)
    os.environ.setdefault("LANGFUSE_BASE_URL", host)
    os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "true")
    os.environ.setdefault("LANGSMITH_OTEL_ONLY", "true")
    os.environ.setdefault("LANGSMITH_TRACING", "true")

    from langfuse import get_client
    from langsmith.integrations.claude_agent_sdk import configure_claude_agent_sdk

    configure_claude_agent_sdk()
    lf = get_client()
    try:
        lf.auth_check()
    except Exception as e:
        logger.info(
            "langfuse auth_check skipped (%s); traces still export via OTLP.",
            type(e).__name__,
        )

    _install_usage_translator()
    _configured = True
    return True


def _install_usage_translator() -> None:
    """Patch the active span processor's on_end hook to translate usage attrs."""
    from opentelemetry import trace

    tp = trace.get_tracer_provider()
    sp_proc = tp._active_span_processor._span_processors[0]  # type: ignore[attr-defined]

    orig_on_end = sp_proc.on_end

    def patched_on_end(span: Any) -> None:
        _translate(span)
        return orig_on_end(span)

    sp_proc.on_end = patched_on_end  # type: ignore[method-assign]


def _translate(span: Any) -> None:
    """Map langsmith usage metadata onto OTel gen_ai.usage.* attributes.

    Langfuse reads gen_ai.usage.* to compute token counts and cost; the
    langsmith[claude-agent-sdk] integration emits langsmith.metadata.usage_metadata
    instead, so without this translation Langfuse shows blank usage.
    """
    attrs = span.attributes or {}
    blob = attrs.get("langsmith.metadata.usage_metadata")
    if not blob:
        return
    try:
        u = json.loads(blob) if isinstance(blob, str) else blob
    except (TypeError, ValueError):
        return

    in_tok = int(u.get("input_tokens") or 0)
    out_tok = int(u.get("output_tokens") or 0)
    tot_tok = int(u.get("total_tokens") or (in_tok + out_tok))

    d = span._attributes._dict
    d.setdefault("gen_ai.usage.input_tokens", in_tok)
    d.setdefault("gen_ai.usage.output_tokens", out_tok)
    d.setdefault("gen_ai.usage.total_tokens", tot_tok)

    details = u.get("input_token_details") or {}
    if details.get("cache_read"):
        d.setdefault("gen_ai.usage.cache_read_input_tokens", int(details["cache_read"]))
    if details.get("cache_creation"):
        d.setdefault("gen_ai.usage.cache_creation_input_tokens", int(details["cache_creation"]))


def flush() -> None:
    """Flush pending traces (call on shutdown)."""
    if not _configured:
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception as e:
        logger.warning("langfuse flush failed: %s", e)
