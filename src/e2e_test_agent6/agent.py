"""Single-request Claude Agent SDK runner.

Imports of `claude_agent_sdk` happen at function call time, NOT module load,
so that tracing.configure() (which patches ClaudeSDKClient via langsmith) has
a chance to run first.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator, Any

logger = logging.getLogger(__name__)


# These get replaced by tests via monkeypatch and resolved lazily otherwise.
_ClaudeSDKClient = None
_ClaudeAgentOptions = None
_AssistantMessage = None
_TextBlock = None
_ToolUseBlock = None
_ToolResultBlock = None
_ResultMessage = None


def _lazy_import_sdk() -> None:
    global _ClaudeSDKClient, _ClaudeAgentOptions
    global _AssistantMessage, _TextBlock, _ToolUseBlock, _ToolResultBlock, _ResultMessage
    if _ClaudeSDKClient is not None:
        return
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        AssistantMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
        ResultMessage,
    )
    _ClaudeSDKClient = ClaudeSDKClient
    _ClaudeAgentOptions = ClaudeAgentOptions
    _AssistantMessage = AssistantMessage
    _TextBlock = TextBlock
    _ToolUseBlock = ToolUseBlock
    _ToolResultBlock = ToolResultBlock
    _ResultMessage = ResultMessage


# Enumerate skills bundled into <repo>/.claude/skills/<name>/.
# Passing this explicit list to `skills=` instead of "all" filters out the
# built-in skills shipped by @anthropic-ai/claude-code (init, review, simplify,
# etc.) which leak in when skills="all" is set. Built-ins are meta-CLI tools,
# not part of a deployed agent's user-facing surface.
_skills_root = Path(__file__).parent.parent.parent / ".claude" / "skills"
_bundled_skills = [
    p.name for p in _skills_root.iterdir()
    if p.is_dir() and (p / "SKILL.md").exists()
] if _skills_root.is_dir() else []


async def run_agent(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    max_turns: int = 40,
    expose_tool_results: bool = False,
    mcp_servers: dict | None = None,
) -> AsyncIterator[tuple]:
    """Run one agent turn and yield typed events.

    Yields:
        ("text", str)                   — assistant text segment
        ("tool_use", name, args_dict)   — agent invoked a tool
        ("tool_result", str)            — tool returned (only if expose_tool_results)
        ("done", usage_dict)            — final result; keys: input_tokens,
                                          output_tokens, cache_read_input_tokens,
                                          cache_creation_input_tokens, total_cost_usd
    """
    _lazy_import_sdk()
    # Bash is intentionally NOT in allowed_tools: this agent is exposed via
    # OpenWebUI to multiple users, and Bash + cluster network access is a
    # universal pivot (curl with stolen creds, /proc/1/environ exfil, k8s SA
    # token reuse). Read/Glob/WebFetch are the assistant's actual product
    # surface. permission_mode="bypassPermissions": deployed agents have no human
    # to approve prompts; "default" makes the SDK block on permission requests and
    # the call hangs. The allow-list above is the actual safety boundary.
    #
    # mcp__<server-alias> whitelists every tool from that MCP server connection;
    # per-tool authorization is enforced upstream at LiteLLM's virtual-key
    # MCP allowlist. Adding/removing tools becomes a LiteLLM config change,
    # not a redeploy of this service. NOTE: the SDK does NOT interpret
    # "mcp__*" as a glob — it's a literal tool name and matches nothing.
    # cwd anchors the SDK's project-scope skill search at the repo root.
    # File is at <root>/src/<pkg>/agent.py, so three .parent calls reach the root.
    # In the container, root is /app (see Dockerfile WORKDIR).
    options = _ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        allowed_tools=["Read", "Glob", "WebFetch"],
        permission_mode="bypassPermissions",
        cwd=Path(__file__).parent.parent.parent,
        setting_sources=["project"],
        skills=_bundled_skills,
        max_turns=max_turns,
        mcp_servers=mcp_servers or {},
    )

    async with _ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, _AssistantMessage):
                for block in message.content:
                    if isinstance(block, _TextBlock):
                        yield ("text", block.text)
                    elif isinstance(block, _ToolUseBlock):
                        yield ("tool_use", block.name, dict(block.input))
                    elif isinstance(block, _ToolResultBlock) and expose_tool_results:
                        # SDK ToolResultBlock has .content (list of dicts/strs)
                        text = _stringify_tool_result(block)
                        yield ("tool_result", text)
            elif isinstance(message, _ResultMessage):
                raw = getattr(message, "usage", None) or {}
                usage = {
                    "input_tokens": int(raw.get("input_tokens") or 0),
                    "output_tokens": int(raw.get("output_tokens") or 0),
                    "cache_read_input_tokens": int(raw.get("cache_read_input_tokens") or 0),
                    "cache_creation_input_tokens": int(raw.get("cache_creation_input_tokens") or 0),
                    "total_cost_usd": float(getattr(message, "total_cost_usd", None) or 0.0),
                }
                yield ("done", usage)


def _stringify_tool_result(block: Any) -> str:
    raw = getattr(block, "content", "")
    if isinstance(raw, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
    return str(raw)
