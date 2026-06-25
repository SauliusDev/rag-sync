import asyncio
from pathlib import Path

import httpx
import pytest

from rag_sync.ragflow_client import RagFlowClient, read_env_value, redact_secret


def test_read_env_value_does_not_print_secret(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    env = tmp_path / ".env"
    env.write_text('RAGFLOW_MCP_HOST_API_KEY = "secret-value"\n', encoding="utf-8")

    assert read_env_value(env, "RAGFLOW_MCP_HOST_API_KEY") == "secret-value"
    captured = capsys.readouterr()
    assert "secret-value" not in captured.out
    assert "secret-value" not in captured.err


def test_redact_secret_replaces_secret():
    assert redact_secret("abc secret-value def", "secret-value") == "abc [REDACTED] def"


def test_client_explicit_api_key_builds_auth_header():
    client = RagFlowClient(base_url="http://ragflow.test/", api_key="secret-value")

    assert client.base_url == "http://ragflow.test"
    assert client.headers == {"Authorization": "Bearer secret-value"}


def test_client_env_key_beats_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('RAGFLOW_MCP_HOST_API_KEY="file-secret"\n', encoding="utf-8")
    monkeypatch.setenv("RAGFLOW_API_KEY", "env-secret")

    client = RagFlowClient(base_url="http://ragflow.test", env_file=env)

    assert client.headers == {"Authorization": "Bearer env-secret"}


def test_list_datasets_sends_auth_and_query_params():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "dataset-1"}]})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    datasets = asyncio.run(client.list_datasets())

    assert datasets == [{"id": "dataset-1"}]
    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/datasets"
    assert request.url.params["page"] == "1"
    assert request.url.params["page_size"] == "100"
    assert request.headers["authorization"] == "Bearer secret-value"


def test_list_datasets_raises_for_missing_data_without_leaking_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "unexpected"})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.list_datasets())

    assert "data list" in str(exc_info.value)
    assert "secret-value" not in str(exc_info.value)
