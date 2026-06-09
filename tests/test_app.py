"""Smoke test — verifies FastAPI wiring without hitting Anthropic.

Mocks `agent.run_agent` (the SDK wrapper) so the test runs offline.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import e2e_test_agent6.app as app_mod


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LITELLM_VIRTUAL_KEY", "sk-fake-test-key")

    async def _fake_run_agent(**kwargs):
        yield ("text", "pong")
        yield ("done", {
            "input_tokens": 1, "output_tokens": 1,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "total_cost_usd": 0.0,
        })

    monkeypatch.setattr("e2e_test_agent6.routes.chat.run_agent", _fake_run_agent)
    return TestClient(app_mod.app)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_readyz(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200


def test_models_list(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["id"] == "e2e-test-agent6"


def test_chat_completions_smoke(client):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "e2e-test-agent6", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "choices" in body
    assert "usage" in body
