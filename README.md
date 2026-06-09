# e2e-test-agent6

es una prueba

Generated from [`mlops/claude-agent-template`](https://gitlab.scanntech.com/mlops/claude-agent-template) — the canonical scaffold for **agentic** agents on the Scanntech agent platform (per ADR 0005).

**Trigger:** `api`
**Owner:** mlops (manuel.murto@scanntech.com)
**Upstream model:** claude-haiku-4-5-20251001

## Local dev

```bash
cp .env.example .env
# Fetch the virtual key from Vault for local dev:
#   vault kv get -field=LITELLM_VIRTUAL_KEY mlops/apps/e2e-test-agent6/dev
# Fetch the shared Langfuse keys for local dev:
#   vault kv get -field=agents_project_api_public_key mlops/services/langfuse
#   vault kv get -field=agents_project_api_secret_key mlops/services/langfuse
# (requires `vault login` against https://vault-mlops.scanntech.com first.)

uv sync
uv run uvicorn e2e_test_agent6.app:app --reload
curl localhost:8000/healthz
```

## Tools

- **Built-in (allow-listed in `src/e2e_test_agent6/agent.py`):** `Read`, `Glob`, `WebFetch`. **Bash is excluded** for multi-tenant safety. Adding tools requires a code change in `agent.py` — review carefully.
- **MCP tools:** declared in `agent-manifest.yaml`'s `mcp_servers` list and authorised at LiteLLM per [governance §4.7](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/llm-gateway/governance.md). Adding/revoking MCP tools is a LiteLLM config change, not a redeploy.

## Auth

`LITELLM_VIRTUAL_KEY` is the only LLM-auth secret the agent needs. It's **minted automatically by CI on first deploy** — the `register:litellm` stage calls LiteLLM's `/key/generate` and writes the resulting key to Vault at `mlops/apps/e2e-test-agent6/<env>`. At runtime VSO materialises it as the `e2e-test-agent6-secrets` K8s Secret and the pod gets it via `envFrom`. At startup the app mirrors `LITELLM_VIRTUAL_KEY` into `ANTHROPIC_API_KEY` because the Claude Agent SDK is hardcoded to read that env-var name.

**Langfuse keys** (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`) come from the shared Vault path `mlops/services/langfuse` (fields `agents_project_api_public_key` / `agents_project_api_secret_key`). MLOps maintains them; rotations propagate to every agent within ~60s without redeploys. `tracing.py` reads the descriptive field names and exports them under the SDK-expected env vars at startup.

- LLM calls: SDK routes through `ANTHROPIC_BASE_URL` to LiteLLM's `/v1/messages` endpoint (model-routed, allow-list enforced).
- MCP attachment: read directly from `LITELLM_VIRTUAL_KEY` to gate MCP tool access.
- **Adding custom per-agent secrets:** write them as additional fields under the same Vault path (`mlops/apps/e2e-test-agent6/<env>`) via the Vault UI (`https://vault-mlops.scanntech.com`). They land in the same K8s Secret with no manifest changes.
- **Adding shared cross-agent secrets:** if a credential needs to be shared across all agents (like the Langfuse keys), MLOps adds it under `mlops/services/<service>`. To make agents read it, declare the path in `.mlops/secrets.yaml` and attach `agents-shared-readonly` to the agent's per-app K8s-auth Vault role.
- **First-deploy ordering:** the agent pod will land before CI has minted the key (`deploy:dev` runs before `register:litellm`). The deployment may CrashLoopBackOff once or twice until VSO syncs the Secret; this is expected. Subsequent deploys are clean since the key is already in Vault.

This keeps cost tracking, budgets, and audit consistent with [ADR 0002](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/adr/0002-use-litellm-as-llm-gateway.md), and aligns the agent with [ADR 0009 (Vault as secrets backend)](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/adr/0009-vault-as-secrets-backend.md).

## Bundling a Claude skill

Your agent's `.claude/skills/` directory is auto-discovered by the Claude Agent SDK at startup. To give this agent a skill:

1. Drop the skill folder at `.claude/skills/<skill-name>/`. The folder must contain at minimum a `SKILL.md` with frontmatter (`name:`, `description:`).
   - From the Scanntech IA Registry (Verdaccio): `iatool pull <skill-name>`. Move the result into `.claude/skills/`.
   - Local skill you're authoring: `cp -r path/to/your-skill .claude/skills/<skill-name>/`.
2. Commit and push.
3. On next deploy, the SDK picks it up. The model sees the skill's `name` + `description` in its context and invokes the `Skill` tool when the user's request matches.

No code changes needed in this agent — `setting_sources=["project"]` plus a startup-time enumeration of `.claude/skills/<name>/` directories (passed to `skills=`) is already wired in `src/<pkg>/agent.py`. Built-in CLI skills (`init`, `review`, etc.) are filtered out; only your bundled skills surface to the model.

For the platform-level recipe see [`agent-platform/developer-layer/bundling-a-skill.md`](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/developer-layer/bundling-a-skill.md).

## Deploy

Push to `dev`. CI builds the image; `scaffold.py` already rendered the K8s manifests into `gitops-k8s-mlops`; ArgoCD syncs.

## Reference

- [Agent types — three orthogonal axes](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/developer-layer/agent-types.md)
- [ADR 0005 — Claude Agent SDK as the canonical agentic-pattern runtime](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/adr/0005-claude-agent-sdk-as-agentic-runtime.md)
- [ADR 0006 — Two Copier templates](https://gitlab.scanntech.com/mlops/agent-platform/-/blob/main/adr/0006-two-copier-templates-workflow-and-agentic.md)
