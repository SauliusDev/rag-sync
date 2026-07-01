from pathlib import Path

import pytest

from src import ldd


@pytest.fixture
def project_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "rag-sync"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def isolate_ldd_log(tmp_path: Path):
    ldd.set_log_path_for_tests(tmp_path / "test-rag-sync.log")
    try:
        yield
    finally:
        ldd.set_log_path_for_tests(None)
