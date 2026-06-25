from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from rag_sync.models import ParserMode, Profile, SkipRules


DEFAULT_PROFILE_PATH = Path("/home/saulius/atlas-services/rag-sync/config/profiles.toml")
DEFAULT_DATA_DIR = Path("/home/saulius/atlas-services/rag-sync/data")
DEFAULT_RAGFLOW_ENV_FILE = Path("/home/saulius/atlas-services/ragflow/source/docker/.env")
DEFAULT_RAGFLOW_BASE_URL = "http://127.0.0.1:9380"
DEFAULT_RAGFLOW_KEY_VAR = "RAGFLOW_MCP_HOST_API_KEY"


def _skip_rules(raw: dict[str, Any] | None) -> SkipRules:
    raw = raw or {}
    return SkipRules(
        path_parts=list(raw.get("path_parts", [])),
        suffixes=list(raw.get("suffixes", [])),
    )


def load_profiles(path: Path = DEFAULT_PROFILE_PATH) -> list[Profile]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    profiles: list[Profile] = []
    for raw in data.get("profiles", []):
        profiles.append(
            Profile(
                name=raw["name"],
                source_paths=[Path(p) for p in raw["source_paths"]],
                file_types=[str(x).lower().lstrip(".") for x in raw["file_types"]],
                parser_mode=ParserMode(raw["parser_mode"]),
                target_dataset=raw["target_dataset"],
                source_type=raw["source_type"],
                enabled=bool(raw.get("enabled", True)),
                output_dir=Path(raw["output_dir"]) if raw.get("output_dir") else None,
                skip_rules=_skip_rules(raw.get("skip_rules")),
                max_convert_workers=int(raw.get("max_convert_workers", 1)),
                max_upload_workers=int(raw.get("max_upload_workers", 4)),
                max_parse_workers=int(raw.get("max_parse_workers", 2)),
            )
        )
    return profiles
