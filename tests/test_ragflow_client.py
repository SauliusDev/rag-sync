import asyncio
import json
from pathlib import Path

import httpx
import pytest

from rag_sync.ragflow_client import (
    QUANT_DATASET_DEFAULTS,
    RagFlowClient,
    read_env_value,
    redact_secret,
)


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


def test_ensure_dataset_refuses_protected_dataset_before_http_call():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.ensure_dataset("quant-books-legacy"))

    assert "refusing to modify protected dataset: quant-books-legacy" in str(exc_info.value)
    assert seen_requests == []


def test_ensure_dataset_configures_existing_dataset_without_posting():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "existing-id", "name": "quant-videos"},
                        {"id": "other-id", "name": "other"},
                    ]
                },
            )
        if request.method == "PUT":
            return httpx.Response(200, json={"data": {"id": "existing-id"}})
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    dataset = asyncio.run(client.ensure_dataset("quant-videos"))

    assert dataset == {"id": "existing-id", "name": "quant-videos"}
    assert [request.method for request in seen_requests] == ["GET", "PUT"]
    put_request = seen_requests[1]
    assert put_request.url.path == "/api/v1/datasets/existing-id"
    assert put_request.headers["authorization"] == "Bearer secret-value"
    assert json_from_request(put_request) == {
        **QUANT_DATASET_DEFAULTS["quant-videos"],
        "pagerank": 0,
    }


def test_ensure_dataset_creates_missing_dataset_with_defaults_and_embedding_model():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": "created-id", "name": "quant-papers"}})
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    dataset = asyncio.run(client.ensure_dataset("quant-papers"))

    assert dataset == {"id": "created-id", "name": "quant-papers"}
    assert [request.method for request in seen_requests] == ["GET", "POST"]
    post_request = seen_requests[1]
    assert post_request.url.path == "/api/v1/datasets"
    payload = json_from_request(post_request)
    assert payload == {
        "name": "quant-papers",
        **QUANT_DATASET_DEFAULTS["quant-papers"],
        "embedding_model": "qwen/qwen3-embedding-8b@openrouter-embed@OpenAI-API-Compatible",
    }


def test_upload_document_posts_multipart_to_dataset_documents_endpoint(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("# Title\n\nBody\n", encoding="utf-8")
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "document-id", "name": "sample.md"}]})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    uploaded = asyncio.run(client.upload_document("dataset-id", document))

    assert uploaded == {"id": "document-id", "name": "sample.md"}
    assert len(seen_requests) == 1
    request = seen_requests[0]
    body = request.read()
    assert request.method == "POST"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="sample.md"' in body
    assert b"Content-Type: text/markdown" in body
    assert b"# Title\n\nBody\n" in body


def test_parse_documents_posts_document_ids_to_parse_endpoint():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": True})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client.parse_documents("dataset-id", ["doc-1", "doc-2"]))

    assert response == {"code": 0, "data": True}
    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents/parse"
    assert json_from_request(request) == {"document_ids": ["doc-1", "doc-2"]}


def test_configure_dataset_refuses_protected_dataset():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        api_key="secret-value",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.configure_dataset("protected-id", "quant-books-legacy"))

    assert "refusing to modify protected dataset: quant-books-legacy" in str(exc_info.value)
    assert seen_requests == []


def json_from_request(request: httpx.Request) -> object:
    return json.loads(request.read().decode("utf-8"))
