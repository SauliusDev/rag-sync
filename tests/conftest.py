from pathlib import Path

import pytest


@pytest.fixture
def project_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "rag-sync"
    root.mkdir()
    return root
