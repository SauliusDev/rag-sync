from pathlib import Path

from fastapi.testclient import TestClient

from rag_sync.api import create_app


def test_health_endpoint():
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_profiles_endpoint_serializes_profile_config(tmp_path: Path):
    config = tmp_path / "profiles.toml"
    config.write_text(
        """
[[profiles]]
name = "quant-articles"
source_paths = ["/atlas/articles"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-articles"
source_type = "article"
enabled = true
output_dir = "/tmp/rag-sync/articles"
max_convert_workers = 1
max_upload_workers = 4
max_parse_workers = 2

[profiles.skip_rules]
path_parts = ["_meta"]
suffixes = [".excalidraw.md"]
""",
        encoding="utf-8",
    )
    client = TestClient(create_app(profile_path=config))

    response = client.get("/api/profiles")

    assert response.status_code == 200
    profiles = response.json()["profiles"]
    assert profiles == [
        {
            "name": "quant-articles",
            "source_paths": ["/atlas/articles"],
            "file_types": ["md"],
            "parser_mode": "passthrough",
            "target_dataset": "quant-articles",
            "source_type": "article",
            "enabled": True,
            "output_dir": "/tmp/rag-sync/articles",
            "skip_rules": {
                "path_parts": ["_meta"],
                "suffixes": [".excalidraw.md"],
            },
            "max_convert_workers": 1,
            "max_upload_workers": 4,
            "max_parse_workers": 2,
        }
    ]


def test_profiles_endpoint_returns_empty_list_for_missing_config(tmp_path: Path):
    client = TestClient(create_app(profile_path=tmp_path / "missing.toml"))

    response = client.get("/api/profiles")

    assert response.status_code == 200
    assert response.json() == {"profiles": []}


def test_profiles_endpoint_returns_stable_error_for_bad_config(tmp_path: Path):
    config = tmp_path / "profiles.toml"
    config.write_text("profiles = []", encoding="utf-8")
    client = TestClient(create_app(profile_path=config))

    response = client.get("/api/profiles")

    assert response.status_code == 500
    assert response.json()["detail"].startswith("failed to load profiles:")
