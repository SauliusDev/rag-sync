from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from rag_sync.config import (
    DEFAULT_RAGFLOW_BASE_URL,
    DEFAULT_RAGFLOW_ENV_FILE,
    DEFAULT_RAGFLOW_KEY_VAR,
)

PROTECTED_DATASETS = {"quant-books-legacy"}

RAPTOR_PROMPT = (
    "Please summarize the following paragraphs. Be careful with the numbers, "
    "do not make things up. Paragraphs as following:\n      {cluster_content}\n"
    "The above is the content you need to summarize."
)


def parser_config(
    auto_keywords: int,
    auto_questions: int,
    chunk_token_num: int,
    toc: bool,
    parent_child: bool = False,
) -> dict[str, Any]:
    return {
        "auto_keywords": auto_keywords,
        "auto_questions": auto_questions,
        "chunk_token_num": chunk_token_num,
        "delimiter": "\n",
        "html4excel": False,
        "layout_recognize": "DeepDOC",
        "topn_tags": 3,
        "filename_embd_weight": 0.1,
        "parent_child": {
            "use_parent_child": parent_child,
            "children_delimiter": "\n\n" if parent_child else "\n",
        },
        "raptor": {
            "use_raptor": False,
            "max_cluster": 64,
            "max_token": 256,
            "threshold": 0.1,
            "random_seed": 0,
            "prompt": RAPTOR_PROMPT,
        },
        "graphrag": {"use_graphrag": False},
        "ext": {
            "toc_extraction": toc,
            "table_context_size": 0,
            "image_context_size": 0,
        },
    }


QUANT_DATASET_DEFAULTS: dict[str, dict[str, Any]] = {
    "quant-books": {
        "description": "Marker-converted quant books for formula-aware Markdown ingestion.",
        "permission": "me",
        "chunk_method": "naive",
        "parser_config": parser_config(0, 0, 1000, True, parent_child=True),
    },
    "quant-videos": {
        "description": "Markdown YouTube notes and transcripts.",
        "permission": "me",
        "chunk_method": "naive",
        "parser_config": parser_config(0, 0, 800, False),
    },
    "quant-articles": {
        "description": "Clean Markdown quant articles and web references.",
        "permission": "me",
        "chunk_method": "naive",
        "parser_config": parser_config(0, 0, 800, False),
    },
    "quant-papers": {
        "description": "Quant papers; prefer Markdown converted with Marker/Docling/MinerU.",
        "permission": "me",
        "chunk_method": "naive",
        "parser_config": parser_config(0, 0, 900, True, parent_child=True),
    },
}

DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b@openrouter-embed@OpenAI-API-Compatible"


def read_env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, value = stripped.split("=", 1)
        if k.strip() == key:
            return value.strip().strip('"').strip("'")
    raise RuntimeError(f"{key} not found in {path}")


def redact_secret(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


class RagFlowClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        env_file: Path = DEFAULT_RAGFLOW_ENV_FILE,
        key_var: str = DEFAULT_RAGFLOW_KEY_VAR,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = (
            base_url or os.environ.get("RAGFLOW_BASE_URL") or DEFAULT_RAGFLOW_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("RAGFLOW_API_KEY")
            or read_env_value(env_file, key_var)
        )
        self._transport = transport

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _raise_for_ragflow_error(self, payload: dict[str, Any]) -> None:
        code = payload.get("code", 0)
        if code in (0, "0", None):
            return
        message = str(payload.get("message") or payload.get("msg") or "RAGFlow API error")
        raise RuntimeError(f"RAGFlow API error code={code}: {redact_secret(message, self.api_key)}")

    def _response_json(self, resp: httpx.Response) -> dict[str, Any]:
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("RAGFlow response was not a JSON object")
        self._raise_for_ragflow_error(payload)
        return payload

    @staticmethod
    def _data_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("RAGFlow response missing data list")
        return data

    async def list_datasets(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/datasets",
                params={"page": 1, "page_size": 100},
                headers=self.headers,
            )
            return self._data_list(self._response_json(resp))

    async def find_dataset(self, name: str) -> dict[str, Any] | None:
        datasets = await self.list_datasets()
        return next((dataset for dataset in datasets if dataset.get("name") == name), None)

    async def ensure_dataset(self, name: str) -> dict[str, Any]:
        if name in PROTECTED_DATASETS:
            raise RuntimeError(f"refusing to modify protected dataset: {name}")

        existing = await self.find_dataset(name)
        if existing:
            await self.configure_dataset(str(existing["id"]), name)
            return existing

        payload = {"name": name, **QUANT_DATASET_DEFAULTS.get(name, {"chunk_method": "naive"})}
        payload.setdefault("embedding_model", DEFAULT_EMBEDDING_MODEL)
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/datasets",
                json=payload,
                headers={**self.headers, "Content-Type": "application/json"},
            )
            data = self._response_json(resp)
            return data.get("data") or data

    async def configure_dataset(self, dataset_id: str, name: str) -> None:
        if name in PROTECTED_DATASETS:
            raise RuntimeError(f"refusing to modify protected dataset: {name}")

        payload = dict(QUANT_DATASET_DEFAULTS.get(name, {}))
        if not payload:
            return
        await self._guard_protected_dataset_id(dataset_id)
        payload["pagerank"] = 0
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
            resp = await client.put(
                f"{self.base_url}/api/v1/datasets/{dataset_id}",
                json=payload,
                headers={**self.headers, "Content-Type": "application/json"},
            )
            self._response_json(resp)

    async def upload_document(self, dataset_id: str, path: Path) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
            files = {"file": (path.name, path.read_bytes(), "text/markdown")}
            resp = await client.post(
                f"{self.base_url}/api/v1/datasets/{dataset_id}/documents",
                files=files,
                headers=self.headers,
            )
            data = self._response_json(resp)
            uploaded = data.get("data") or []
            return uploaded[0] if isinstance(uploaded, list) and uploaded else data

    async def list_documents(self, dataset_id: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/datasets/{dataset_id}/documents",
                params={"page": 1, "page_size": 100},
                headers=self.headers,
            )
            payload = self._response_json(resp)
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("docs"), list):
                return data["docs"]
            if isinstance(data, list):
                return data
            raise RuntimeError("RAGFlow response missing documents list")

    async def parse_documents(self, dataset_id: str, document_ids: list[str]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/datasets/{dataset_id}/documents/parse",
                json={"document_ids": document_ids},
                headers={**self.headers, "Content-Type": "application/json"},
            )
            return self._response_json(resp)

    async def stop_documents(self, dataset_id: str, document_ids: list[str]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/datasets/{dataset_id}/documents/stop",
                json={"document_ids": document_ids},
                headers={**self.headers, "Content-Type": "application/json"},
            )
            return self._response_json(resp)

    async def delete_documents(self, dataset_id: str, document_ids: list[str]) -> dict[str, Any]:
        await self._guard_protected_dataset_id(dataset_id)
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
            resp = await client.request(
                "DELETE",
                f"{self.base_url}/api/v1/datasets/{dataset_id}/documents",
                json={"ids": document_ids},
                headers={**self.headers, "Content-Type": "application/json"},
            )
            return self._response_json(resp)

    async def _guard_protected_dataset_id(self, dataset_id: str) -> None:
        datasets = await self.list_datasets()
        for dataset in datasets:
            if str(dataset.get("id")) == dataset_id and dataset.get("name") in PROTECTED_DATASETS:
                raise RuntimeError(
                    f"refusing to modify protected dataset: {dataset.get('name')}"
                )

    async def connection_status(self) -> dict[str, Any]:
        datasets = await self.list_datasets()
        return {"ok": True, "dataset_count": len(datasets), "base_url": self.base_url}
