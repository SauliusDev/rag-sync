from pathlib import Path

from fastapi.testclient import TestClient

from rag_sync.api import create_app
from rag_sync.db import RagSyncDb


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


def test_scan_endpoint_returns_404_for_unknown_profile(tmp_path: Path):
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
""",
        encoding="utf-8",
    )
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    response = client.post("/api/scan/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown profile: missing"}


def test_scan_and_files_endpoints_use_injected_db(tmp_path: Path):
    source_dir = tmp_path / "articles"
    source_dir.mkdir()
    (source_dir / "example.md").write_text("# Example\n", encoding="utf-8")
    config = tmp_path / "profiles.toml"
    config.write_text(
        f"""
[[profiles]]
name = "quant-articles"
source_paths = ["{source_dir}"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-articles"
source_type = "article"
""",
        encoding="utf-8",
    )
    db = RagSyncDb(tmp_path / "state.sqlite")
    db.migrate()
    client = TestClient(create_app(profile_path=config, db_factory=lambda: db))

    scan_response = client.post("/api/scan/quant-articles")
    files_response = client.get("/api/files")

    assert scan_response.status_code == 200
    assert scan_response.json() == {"count": 1}
    assert files_response.status_code == 200
    files = files_response.json()["files"]
    assert len(files) == 1
    assert files[0]["profile_name"] == "quant-articles"
    assert files[0]["source_path"].endswith("example.md")


def test_retrieval_query_set_endpoint_returns_formula_benchmark():
    client = TestClient(create_app())

    response = client.get("/api/retrieval/query-sets/formula-benchmark")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "formula-benchmark"
    assert len(data["queries"]) == 10
    assert data["queries"][0]["id"] == "Q1"
    assert "fractional differentiation" in data["queries"][0]["question"]
