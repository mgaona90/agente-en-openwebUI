"""register_agent.py — read an agent-manifest.yaml and register the agent in LiteLLM.

Invoked from CI. Stdlib + httpx + PyYAML only. No framework dependency.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Literal

import httpx
import yaml


REQUIRED_FIELDS = ("name", "team", "owner", "visibility", "model")
ALLOWED_VISIBILITY = ("team", "org")


@dataclass(frozen=True)
class Manifest:
    name: str
    team: str
    owner: str
    visibility: str
    model: str  # upstream model the agent calls through LiteLLM (e.g. claude-haiku-4-5-20251001)


def load_manifest(path: Path) -> Manifest:
    """Read and validate an agent-manifest.yaml. Raise ValueError on any defect."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"manifest at {path} must be a YAML mapping")
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ValueError(f"manifest at {path} missing required fields: {', '.join(missing)}")
    blank = [f for f in REQUIRED_FIELDS if not raw.get(f)]
    if blank:
        raise ValueError(
            f"manifest at {path} fields must not be empty: {', '.join(blank)}"
        )
    if raw["visibility"] not in ALLOWED_VISIBILITY:
        raise ValueError(
            f"manifest visibility must be one of {ALLOWED_VISIBILITY}, got {raw['visibility']!r}"
        )
    return Manifest(
        name=str(raw["name"]),
        team=str(raw["team"]),
        owner=str(raw["owner"]),
        visibility=str(raw["visibility"]),
        model=str(raw["model"]),
    )


def derive_model_name(manifest: Manifest, env: str) -> str:
    """LiteLLM model name convention: <name>-<env>. Non-overridable in v1."""
    return f"{manifest.name}-{env}"


def derive_api_base(manifest: Manifest, env: str, port: int = 8000) -> str:
    """In-cluster api_base convention: http://<name>.<name>-<env>.svc.cluster.local:<port>/v1."""
    ns = f"{manifest.name}-{env}"
    return f"http://{manifest.name}.{ns}.svc.cluster.local:{port}/v1"


class LiteLLMClient:
    """Thin wrapper around LiteLLM's admin API. Methods are small on purpose."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
            transport=transport,
        )

    def find_model_id(self, model_name: str) -> str | None:
        """Return the LiteLLM model_info.id for model_name, or None if absent."""
        r = self._client.get("/model/info")
        r.raise_for_status()
        for entry in r.json().get("data", []):
            if entry.get("model_name") == model_name:
                return entry.get("model_info", {}).get("id")
        return None

    def register_model(self, *, model_name: str, api_base: str, team: str) -> str | None:
        """POST /model/new. Returns the new model_info.id, or None if the response omitted it.

        Note: `team` is accepted for forward-compat but not sent — `model_info.team_id` is
        a LiteLLM Enterprise-gated field, and the OSS deployment 403s on it. When/if
        Enterprise is acquired, restore `"model_info": {"team_id": <uuid>}` and look up
        the team UUID from /team/list (the slug is not what the API expects).
        """
        payload = {
            "model_name": model_name,
            "litellm_params": {
                "model": f"openai/{model_name}",
                "api_base": api_base,
                "api_key": "ignored-by-agent",
            },
        }
        r = self._client.post("/model/new", json=payload)
        r.raise_for_status()
        return r.json().get("model_info", {}).get("id")

    def update_model(self, *, model_id: str, api_base: str, team: str) -> None:
        """POST /model/update. No return value — idempotent. See note in register_model re: team_id."""
        payload = {
            "model_info": {"id": model_id},
            "litellm_params": {"api_base": api_base},
        }
        r = self._client.post("/model/update", json=payload)
        r.raise_for_status()


def register_or_update(
    client: LiteLLMClient,
    manifest: Manifest,
    *,
    env: str,
    port: int = 8000,
) -> Literal["created", "updated"]:
    """Register model if absent, update if present. Returns 'created' or 'updated'."""
    model_name = derive_model_name(manifest, env)
    api_base = derive_api_base(manifest, env, port)
    existing = client.find_model_id(model_name)
    if existing is None:
        # register_model returns the new id; v1 doesn't need it — CLI just reports outcome.
        client.register_model(model_name=model_name, api_base=api_base, team=manifest.team)
        return "created"
    client.update_model(model_id=existing, api_base=api_base, team=manifest.team)
    return "updated"


def _default_transport() -> httpx.BaseTransport | None:
    """Seam for tests to inject a MockTransport via monkeypatching."""
    return None


def publish_to_openwebui(*, base_url: str, api_key: str, model_name: str) -> str:
    """Persist a public model record in OpenWebUI so it shows up in the picker.

    OpenWebUI 0.8+ uses access_grants (flat list) instead of the old access_control dict.
    A wildcard user read grant (principal_id="*") makes the model public to all users.

    Idempotent: if the record already exists, falls through to /update.
    Returns 'created' or 'updated'.
    """
    # principal_id="*" = public read for all authenticated users (OpenWebUI 0.8+)
    public_grant = {"principal_type": "user", "principal_id": "*", "permission": "read"}
    payload = {
        "id": model_name,
        "name": model_name,
        "base_model_id": None,
        "meta": {"profile_image_url": "/static/favicon.png", "description": "", "capabilities": {}},
        "params": {},
        "is_active": True,
        "access_grants": [public_grant],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=15.0) as c:
        r = c.post("/api/v1/models/create", json=payload)
        if r.status_code == 200:
            return "created"
        # 400 here typically means "already exists" — try update.
        r2 = c.post(f"/api/v1/models/model/update?id={model_name}", json=payload)
        r2.raise_for_status()
        return "updated"


def lookup_team_id_by_alias(*, base_url: str, admin_key: str, team_alias: str) -> str:
    """Resolve a LiteLLM team_alias (e.g. "mlops") to its UUID team_id.

    LiteLLM stores teams under random UUIDs; `team_alias` is the human-readable name.
    Passing the alias verbatim to /key/generate stores a key under team_id=<alias>,
    which LiteLLM accepts at mint time but rejects on subsequent /key/update calls
    because the team lookup fails. Always resolve to a UUID first.

    Raises ValueError if no team matches the alias.
    """
    headers = {"Authorization": f"Bearer {admin_key}"}
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=15.0) as c:
        r = c.get("/team/list")
        r.raise_for_status()
        teams = r.json()
        if isinstance(teams, dict):  # some LiteLLM responses wrap the list
            teams = teams.get("data", [])
    for t in teams:
        if t.get("team_alias") == team_alias:
            return str(t["team_id"])
    known = sorted({t.get("team_alias") for t in teams if t.get("team_alias")})
    raise ValueError(
        f"team_alias {team_alias!r} not registered in LiteLLM. "
        f"Known aliases: {known}. Create the team via the LiteLLM admin UI first."
    )


def mint_virtual_key(
    *,
    base_url: str,
    admin_key: str,
    team_id: str,
    models: list[str],
    key_alias: str,
) -> str:
    """Mint a LiteLLM virtual key via /key/generate.

    Args:
        base_url: LiteLLM admin endpoint (e.g. https://litellm-mlops.scanntech.com).
        admin_key: LITELLM_ADMIN_KEY — has /key/generate authority.
        team_id: LiteLLM team UUID (resolve from team_alias via lookup_team_id_by_alias).
            Passing a literal alias string stores the key under team_id=<alias>, which
            LiteLLM accepts at mint time but rejects on subsequent /key/update calls.
        models: Allow-list of model groups this key may call. Must include both the
            agent's own LiteLLM model_name AND the upstream model the agent calls
            (manifest.model), e.g. ["my-agent-dev", "claude-haiku-4-5-20251001"].
        key_alias: Human-readable name shown in LiteLLM's Models UI (use <slug>-<env>).

    Returns: the minted sk-... key string.
    Raises: httpx.HTTPStatusError on non-2xx response.
    """
    payload = {
        "team_id": team_id,
        "models": models,
        "key_alias": key_alias,
    }
    headers = {"Authorization": f"Bearer {admin_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=15.0) as c:
        r = c.post("/key/generate", json=payload)
        r.raise_for_status()
        data = r.json()
        return str(data["key"])


def vault_jwt_login(*, vault_addr: str, role: str, jwt: str) -> str:
    """Authenticate to Vault via the JWT auth method; return a client token.

    Raises: httpx.HTTPStatusError on non-2xx response.
    """
    payload = {"role": role, "jwt": jwt}
    with httpx.Client(base_url=vault_addr.rstrip("/"), timeout=15.0) as c:
        r = c.post("/v1/auth/jwt/login", json=payload)
        r.raise_for_status()
        return str(r.json()["auth"]["client_token"])


def vault_read_secret(
    *, vault_addr: str, token: str, engine: str, path: str
) -> dict | None:
    """Read a KV v2 secret. Returns the .data.data dict, or None if 404."""
    url = f"/v1/{engine}/data/{path}"
    headers = {"X-Vault-Token": token}
    with httpx.Client(base_url=vault_addr.rstrip("/"), headers=headers, timeout=15.0) as c:
        r = c.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("data", {}).get("data") or {}


def vault_write_secret(
    *, vault_addr: str, token: str, engine: str, path: str, data: dict
) -> None:
    """Write to a KV v2 secret. Overwrites any existing fields with the same names.

    Raises: httpx.HTTPStatusError on non-2xx response.
    """
    url = f"/v1/{engine}/data/{path}"
    headers = {"X-Vault-Token": token, "Content-Type": "application/json"}
    payload = {"data": data}
    with httpx.Client(base_url=vault_addr.rstrip("/"), headers=headers, timeout=15.0) as c:
        r = c.post(url, json=payload)
        r.raise_for_status()


def resolve_admin_key_and_vault_token(
    *, vault_path: str = "services/litellm", field: str = "admin_key"
) -> tuple[str, str | None]:
    """Resolve the LiteLLM admin key.

    Vault is primary: if VAULT_ADDR + VAULT_JWT_ROLE + VAULT_ID_TOKEN are all
    set in env, JWT-login to Vault and read `field` from `mlops/<vault_path>`.

    Fallback: LITELLM_ADMIN_KEY env var (local-dev path).

    Returns:
        (admin_key, vault_token). vault_token is None when the fallback path
        was used; the caller should reuse the token (when non-None) for any
        subsequent Vault operations rather than logging in again.

    Raises:
        RuntimeError: when neither source produces a value.
    """
    vault_addr = os.environ.get("VAULT_ADDR")
    vault_role = os.environ.get("VAULT_JWT_ROLE")
    vault_jwt = os.environ.get("VAULT_ID_TOKEN")

    vault_token: str | None = None
    admin_key: str | None = None

    if all((vault_addr, vault_role, vault_jwt)):
        try:
            vault_token = vault_jwt_login(
                vault_addr=vault_addr, role=vault_role, jwt=vault_jwt
            )
            data = vault_read_secret(
                vault_addr=vault_addr,
                token=vault_token,
                engine="mlops",
                path=vault_path,
            )
            admin_key = (data or {}).get(field)
        except httpx.HTTPError as e:
            print(
                f"WARN: Vault flow for admin_key failed: {e}; "
                "falling back to LITELLM_ADMIN_KEY env var",
                file=sys.stderr,
            )
            vault_token = None

    if not admin_key:
        admin_key = os.environ.get("LITELLM_ADMIN_KEY")

    if not admin_key:
        raise RuntimeError(
            "LiteLLM admin key not found. Tried: Vault path "
            f"mlops/{vault_path} (field {field}) and LITELLM_ADMIN_KEY env var. "
            "Set one of them."
        )

    return admin_key, vault_token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register an agent in LiteLLM from agent-manifest.yaml.")
    parser.add_argument("--manifest", required=True, type=Path, help="Path to agent-manifest.yaml")
    parser.add_argument("--env", required=True, help="Deployment environment, e.g. dev, prd")
    parser.add_argument("--port", type=int, default=8000, help="Agent HTTP port (default 8000)")
    args = parser.parse_args(argv)

    # Base URL stays in env (it's an endpoint, not a secret).
    base_url = os.environ.get("LITELLM_BASE_URL") or os.environ.get("LITELLM_ADMIN_URL")
    if not base_url:
        print(
            "ERROR: LITELLM_BASE_URL (or LITELLM_ADMIN_URL) must be set.",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    # Admin key from Vault (primary) or env var (fallback).
    # Capture the Vault token if one was minted — reuse it later for the
    # per-agent KV operations rather than logging in twice.
    try:
        api_key, vault_token = resolve_admin_key_and_vault_token()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    client = LiteLLMClient(base_url=base_url, api_key=api_key, transport=_default_transport())
    try:
        outcome = register_or_update(client, manifest, env=args.env, port=args.port)
    except httpx.HTTPError as e:
        print(f"ERROR: LiteLLM admin API call failed: {e}", file=sys.stderr)
        return 4

    model_name = derive_model_name(manifest, args.env)
    print(f"{outcome} {model_name} (team={manifest.team}, visibility={manifest.visibility})")

    # ── Vault: ensure LITELLM_VIRTUAL_KEY is present at mlops/apps/<slug>/<env>.
    # Reuses the Vault token from resolve_admin_key_and_vault_token() above.
    # If that returned None (local-dev or Vault unreachable earlier), skip.
    # Idempotent: if the key already exists at the path, skip mint+write.
    if vault_token:
        vault_addr = os.environ["VAULT_ADDR"]  # guaranteed set when vault_token is non-None
        try:
            vault_path = f"apps/{manifest.name}/{args.env}"
            existing = vault_read_secret(
                vault_addr=vault_addr, token=vault_token,
                engine="mlops", path=vault_path,
            )
            if existing and existing.get("LITELLM_VIRTUAL_KEY"):
                print(f"vault key already present at mlops/{vault_path} — skipping mint")
            else:
                # Resolve team_alias → team_id (UUID) so /key/update calls work later.
                team_id = lookup_team_id_by_alias(
                    base_url=base_url, admin_key=api_key, team_alias=manifest.team,
                )
                # The key needs scope on BOTH the agent's own LiteLLM model_name (so
                # clients can call it) AND the upstream model the agent calls through
                # LiteLLM (so the agent's outbound request doesn't 401).
                minted = mint_virtual_key(
                    base_url=base_url,
                    admin_key=api_key,
                    team_id=team_id,
                    models=[model_name, manifest.model],
                    key_alias=model_name,
                )
                # Preserve any other fields the user put at this path (e.g.
                # custom secrets) by merging rather than overwriting.
                merged = dict(existing or {})
                merged["LITELLM_VIRTUAL_KEY"] = minted
                vault_write_secret(
                    vault_addr=vault_addr, token=vault_token,
                    engine="mlops", path=vault_path, data=merged,
                )
                print(f"vault minted+wrote LITELLM_VIRTUAL_KEY at mlops/{vault_path}")
        except httpx.HTTPError as e:
            print(f"ERROR: Vault per-agent flow failed: {e}", file=sys.stderr)
            return 1
    else:
        # No Vault token — local-dev mode (no VAULT_ID_TOKEN) or Vault was
        # unreachable earlier and we fell back to env-var admin key.
        print("vault token not available — skipping per-agent mint+write")

    # Best-effort: also publish to OpenWebUI so the model shows up in the picker.
    # Skipped quietly if the OpenWebUI vars aren't set; non-fatal if it fails (the
    # LiteLLM registration is what gates the actual chat routing).
    owui_url = os.environ.get("OPENWEBUI_URL")
    owui_key = os.environ.get("OPENWEBUI_ADMIN_KEY")
    if owui_url and owui_key:
        try:
            owui_outcome = publish_to_openwebui(base_url=owui_url, api_key=owui_key, model_name=model_name)
            print(f"openwebui {owui_outcome} {model_name}")
        except httpx.HTTPError as e:
            print(f"WARN: OpenWebUI publish failed (non-fatal): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
