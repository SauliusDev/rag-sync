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


def _profile_label(raw: dict[str, Any], index: int) -> str:
    name = raw.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return f"profile #{index + 1}"


def _required_string(raw: dict[str, Any], key: str, profile_name: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"profile {profile_name}: {key} must be a non-empty string")
    return value


def _non_empty_list(raw: dict[str, Any], key: str, profile_name: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"profile {profile_name}: {key} must be a non-empty list")
    return value


def _enabled(raw: dict[str, Any], profile_name: str) -> bool:
    value = raw.get("enabled", True)
    if not isinstance(value, bool):
        raise ValueError(f"profile {profile_name}: enabled must be a bool")
    return value


def _positive_int(raw: dict[str, Any], key: str, default: int, profile_name: str) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"profile {profile_name}: {key} must be a positive integer")
    return value


def _parser_mode(value: str, profile_name: str) -> ParserMode:
    try:
        return ParserMode(value)
    except ValueError as exc:
        raise ValueError(
            f"profile {profile_name}: parser_mode must be one of "
            f"{', '.join(mode.value for mode in ParserMode)}"
        ) from exc


def _string_sequence(
    raw: dict[str, Any], key: str, profile_name: str
) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(
            f"profile {profile_name}: skip_rules.{key} must be a list of strings"
        )
    return tuple(value)


def _skip_rules(raw: dict[str, Any] | None, profile_name: str) -> SkipRules:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError(f"profile {profile_name}: skip_rules must be a table")
    return SkipRules(
        path_parts=_string_sequence(raw, "path_parts", profile_name),
        suffixes=_string_sequence(raw, "suffixes", profile_name),
    )


def load_profiles(path: Path = DEFAULT_PROFILE_PATH) -> list[Profile]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("profiles must be a non-empty list")

    profiles: list[Profile] = []
    for index, raw in enumerate(raw_profiles):
        if not isinstance(raw, dict):
            raise ValueError(f"profile #{index + 1} must be a table")

        profile_name = _profile_label(raw, index)
        name = _required_string(raw, "name", profile_name)
        parser_mode = _required_string(raw, "parser_mode", profile_name)
        target_dataset = _required_string(raw, "target_dataset", profile_name)
        source_type = _required_string(raw, "source_type", profile_name)
        source_paths = _non_empty_list(raw, "source_paths", profile_name)
        file_types = _non_empty_list(raw, "file_types", profile_name)

        profiles.append(
            Profile(
                name=name,
                source_paths=tuple(Path(p) for p in source_paths),
                file_types=tuple(str(x).lower().lstrip(".") for x in file_types),
                parser_mode=_parser_mode(parser_mode, profile_name),
                target_dataset=target_dataset,
                source_type=source_type,
                enabled=_enabled(raw, profile_name),
                output_dir=Path(raw["output_dir"]) if raw.get("output_dir") else None,
                skip_rules=_skip_rules(raw.get("skip_rules"), profile_name),
                max_convert_workers=_positive_int(
                    raw, "max_convert_workers", 1, profile_name
                ),
                max_upload_workers=_positive_int(
                    raw, "max_upload_workers", 4, profile_name
                ),
                max_parse_workers=_positive_int(
                    raw, "max_parse_workers", 2, profile_name
                ),
            )
        )
    return profiles
