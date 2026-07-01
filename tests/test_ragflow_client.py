import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.ragflow_client import (
    QUANT_DATASET_DEFAULTS,
    RagFlowClient,
    parser_config,
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
    client = RagFlowClient(base_url="http://ragflow.test/", **{"api_key": "test-key"})

    assert client.base_url == "http://ragflow.test"
    assert client.headers == {"Authorization": "Bearer test-key"}


def test_client_env_key_beats_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('RAGFLOW_MCP_HOST_API_KEY="file-secret"\n', encoding="utf-8")
    monkeypatch.setenv("RAGFLOW_API_KEY", "env-secret")

    client = RagFlowClient(base_url="http://ragflow.test", env_file=env)

    assert client.headers == {"Authorization": "Bearer env-secret"}


def test_client_reads_standard_ragflow_api_key_from_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('RAGFLOW_API_KEY="file-secret"\n', encoding="utf-8")

    client = RagFlowClient(base_url="http://ragflow.test", env_file=env)

    assert client.headers == {"Authorization": "Bearer file-secret"}


def test_parser_config_places_ragflow_extension_fields_under_ext():
    config = parser_config(3, 1, 1000, True)

    assert "toc_extraction" not in config
    assert "table_context_size" not in config
    assert "image_context_size" not in config
    assert "llm_id" not in config
    assert config["ext"] == {
        "toc_extraction": True,
        "table_context_size": 0,
        "image_context_size": 0,
    }


def test_quant_books_default_uses_renamed_dataset():
    assert "quant-books" in QUANT_DATASET_DEFAULTS
    assert "quant-books-md" not in QUANT_DATASET_DEFAULTS
    assert QUANT_DATASET_DEFAULTS["quant-books"]["chunk_method"] == "naive"
    assert QUANT_DATASET_DEFAULTS["quant-books"]["parser_config"]["auto_keywords"] == 0
    assert QUANT_DATASET_DEFAULTS["quant-books"]["parser_config"]["auto_questions"] == 0
    assert QUANT_DATASET_DEFAULTS["quant-books"]["parser_config"]["chunk_token_num"] == 1000
    assert QUANT_DATASET_DEFAULTS["quant-books"]["parser_config"]["ext"][
        "toc_extraction"
    ] is False
    assert QUANT_DATASET_DEFAULTS["quant-books"]["parser_config"]["parent_child"][
        "use_parent_child"
    ] is True


def test_quant_papers_default_uses_parent_child():
    assert QUANT_DATASET_DEFAULTS["quant-papers"]["chunk_method"] == "naive"
    assert QUANT_DATASET_DEFAULTS["quant-papers"]["parser_config"]["auto_keywords"] == 0
    assert QUANT_DATASET_DEFAULTS["quant-papers"]["parser_config"]["auto_questions"] == 0
    assert QUANT_DATASET_DEFAULTS["quant-papers"]["parser_config"]["ext"][
        "toc_extraction"
    ] is False
    assert QUANT_DATASET_DEFAULTS["quant-papers"]["parser_config"]["parent_child"][
        "use_parent_child"
    ] is True


def test_quant_articles_and_videos_disable_llm_enrichment():
    for name in ("quant-articles", "quant-videos"):
        parser = QUANT_DATASET_DEFAULTS[name]["parser_config"]
        assert parser["auto_keywords"] == 0
        assert parser["auto_questions"] == 0


def test_list_datasets_sends_auth_and_query_params():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "dataset-1"}]})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
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
    assert request.headers["authorization"] == "Bearer test-key"


def test_list_datasets_raises_for_missing_data_without_leaking_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "unexpected"})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.list_datasets())

    assert "data list" in str(exc_info.value)
    assert "test-key" not in str(exc_info.value)


def test_ensure_dataset_refuses_protected_dataset_before_http_call():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
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
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    dataset = asyncio.run(client.ensure_dataset("quant-videos"))

    assert dataset == {"id": "existing-id", "name": "quant-videos"}
    assert [request.method for request in seen_requests] == ["GET", "GET", "PUT"]
    put_request = seen_requests[2]
    assert put_request.url.path == "/api/v1/datasets/existing-id"
    assert put_request.headers["authorization"] == "Bearer test-key"
    assert json_from_request(put_request) == {
        **QUANT_DATASET_DEFAULTS["quant-videos"],
        "embedding_model": None,
        "pagerank": 0,
    }


def test_ensure_dataset_creates_missing_dataset_with_defaults():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET":
            if len(seen_requests) == 1:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(
                200,
                json={"data": [{"id": "created-id", "name": "quant-papers"}]},
            )
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": "created-id", "name": "quant-papers"}})
        if request.method == "PUT":
            return httpx.Response(200, json={"data": {"id": "created-id", "name": "quant-papers"}})
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    dataset = asyncio.run(client.ensure_dataset("quant-papers"))

    assert dataset == {"id": "created-id", "name": "quant-papers"}
    assert [request.method for request in seen_requests] == ["GET", "POST", "GET", "PUT", "GET"]
    post_request = seen_requests[1]
    assert post_request.url.path == "/api/v1/datasets"
    payload = json_from_request(post_request)
    assert payload == {
        "name": "quant-papers",
        **QUANT_DATASET_DEFAULTS["quant-papers"],
    }
    put_request = seen_requests[3]
    assert put_request.url.path == "/api/v1/datasets/created-id"
    assert json_from_request(put_request) == {
        **QUANT_DATASET_DEFAULTS["quant-papers"],
        "embedding_model": None,
        "pagerank": 0,
    }


def test_configure_dataset_resets_embedding_model_to_ragflow_default():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "dataset-id", "name": "quant-books"}]},
            )
        if request.method == "PUT":
            return httpx.Response(200, json={"data": {"id": "dataset-id"}})
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    asyncio.run(client.configure_dataset("dataset-id", "quant-books"))

    put_request = seen_requests[1]
    assert put_request.method == "PUT"
    assert put_request.url.path == "/api/v1/datasets/dataset-id"
    assert json_from_request(put_request) == {
        **QUANT_DATASET_DEFAULTS["quant-books"],
        "embedding_model": None,
        "pagerank": 0,
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
        **{"api_key": "test-key"},
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
        return httpx.Response(200, json={"code": 0, "data": {"success_count": 2}})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client.parse_documents("dataset-id", ["doc-1", "doc-2"]))

    assert response == {"code": 0, "data": {"success_count": 2}}
    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents/parse"
    assert json_from_request(request) == {"document_ids": ["doc-1", "doc-2"]}


def test_list_documents_sends_dataset_documents_get():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"data": {"docs": [{"id": "doc-1", "progress": 0.8}]}})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    documents = asyncio.run(client.list_documents("dataset-id"))

    assert documents == [{"id": "doc-1", "progress": 0.8}]
    request = seen_requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents"
    assert request.url.params["page_size"] == "100"


def test_list_chunks_sends_document_chunks_get():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={"data": {"chunks": [{"id": "chunk-1", "content": "Volatility cluster"}]}},
        )

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    chunks = asyncio.run(client.list_chunks("dataset-id", "doc-1"))

    assert chunks == [{"id": "chunk-1", "content": "Volatility cluster"}]
    request = seen_requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents/doc-1/chunks"
    assert request.url.params["page_size"] == "100"


def test_stop_documents_posts_document_ids_to_stop_endpoint():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": True})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client.stop_documents("dataset-id", ["doc-1"]))

    assert response == {"code": 0, "data": True}
    request = seen_requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents/stop"
    assert json_from_request(request) == {"document_ids": ["doc-1"]}


def test_delete_documents_sends_delete_body():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "dataset-id", "name": "quant-videos"}]},
            )
        return httpx.Response(200, json={"code": 0, "data": True})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client.delete_documents("dataset-id", ["doc-1"]))

    assert response == {"code": 0, "data": True}
    request = seen_requests[1]
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/datasets/dataset-id/documents"
    assert json_from_request(request) == {"ids": ["doc-1"]}


def test_configure_dataset_refuses_protected_dataset():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.configure_dataset("protected-id", "quant-books-legacy"))

    assert "refusing to modify protected dataset: quant-books-legacy" in str(exc_info.value)
    assert seen_requests == []


def test_configure_dataset_refuses_protected_dataset_id():
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "legacy-id", "name": "quant-books-legacy"}]},
            )
        return httpx.Response(500)

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.configure_dataset("legacy-id", "quant-videos"))

    assert "refusing to modify protected dataset: quant-books-legacy" in str(exc_info.value)
    assert [request.method for request in seen_requests] == ["GET"]


def test_ensure_dataset_raises_on_ragflow_application_error_without_leaking_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            200,
            json={"code": 102, "message": "create failed for test-key"},
        )

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(client.ensure_dataset("quant-papers"))

    assert "code=102" in str(exc_info.value)
    assert "test-key" not in str(exc_info.value)


def test_configure_dataset_raises_on_ragflow_application_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "dataset-id", "name": "quant-videos"}]},
            )
        return httpx.Response(200, json={"code": 102, "message": "configure failed"})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="code=102"):
        asyncio.run(client.configure_dataset("dataset-id", "quant-videos"))


def test_upload_document_raises_on_ragflow_application_error(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("# Title\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 102, "message": "upload failed"})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="code=102"):
        asyncio.run(client.upload_document("dataset-id", document))


def test_parse_documents_raises_on_ragflow_application_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 102, "message": "parse failed"})

    client = RagFlowClient(
        base_url="http://ragflow.test",
        **{"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="code=102"):
        asyncio.run(client.parse_documents("dataset-id", ["doc-1"]))


def json_from_request(request: httpx.Request) -> object:
    return json.loads(request.read().decode("utf-8"))
