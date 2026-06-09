# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
# README.md must be in the early COPY because pyproject.toml's `readme = "README.md"`
# is resolved by hatchling at metadata-validation time, before the rest of the source
# is copied. Without it, `uv sync` fails with "Readme file does not exist: README.md".
COPY pyproject.toml uv.lock* README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps -e .


FROM python:3.12-slim-bookworm AS runtime

# Claude Agent SDK shells out to the @anthropic-ai/claude-code CLI on PATH.
# The SDK package does NOT vendor the binary — install it via npm.
# Node 22 LTS satisfies the SDK's engines.node = ">=18.0.0" pin (Phase 0.3).
# CLAUDE_CODE_VERSION matches claude_agent_sdk._cli_version.__cli_version__ at
# build time. Bump in lockstep when the Python SDK is upgraded.
ARG CLAUDE_CODE_VERSION=2.1.126
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
# Ship .claude/ so the SDK's project-scope skill discovery works at runtime.
COPY .claude /app/.claude

# Writable HOME for the claude CLI's ~/.claude/ session state.
# Pod runs as a non-root user under K8s PodSecurity restricted profile;
# without this, HOME defaults to / (read-only) and `claude` hangs at the SDK's
# initialize control request → "Control request timeout: initialize" in logs.
RUN mkdir -p /app/home && chmod 1777 /app/home

ENV PATH="/app/.venv/bin:$PATH" \
    HOME="/app/home" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000
CMD ["uvicorn", "e2e_test_agent6.app:app", "--host", "0.0.0.0", "--port", "8000"]
