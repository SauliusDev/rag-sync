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


def read_env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, value = stripped.split("=", 1)
        if k == key:
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
    ):
        self.base_url = (
            base_url or os.environ.get("RAGFLOW_BASE_URL") or DEFAULT_RAGFLOW_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("RAGFLOW_API_KEY")
            or read_env_value(env_file, key_var)
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def list_datasets(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/datasets",
                params={"page": 1, "page_size": 100},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])

    async def connection_status(self) -> dict[str, Any]:
        datasets = await self.list_datasets()
        return {"ok": True, "dataset_count": len(datasets), "base_url": self.base_url}
