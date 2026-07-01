from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from src.marker_batch import BatchRunResult
from tools.marker_cluster_ui.run import main, parse_args


def test_cluster_ui_runner_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "Book.pdf").write_bytes(b"%PDF-1.4\n")
    captured: dict[str, object] = {}
    console = Console(record=True, width=120)

    def fake_run_batch(**kwargs: object) -> BatchRunResult:
        captured.update(kwargs)
        return BatchRunResult(
            batch_id="batch-1",
            success_count=1,
            failure_count=0,
            manifest_path=tmp_path / "batch" / "manifest.json",
            log_path=tmp_path / "batch" / "logs" / "run.jsonl",
        )

    monkeypatch.setattr("tools.marker_cluster_ui.run.run_batch", fake_run_batch)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "batch"),
            "--profile",
            "quant-books",
            "--tag",
            "gpu",
            "--tag",
            "nightly",
            "--marker-bin",
            "/opt/marker/bin/marker",
        ],
        console=console,
    )

    output = console.export_text()

    assert exit_code == 0
    assert captured == {
        "input_dir": input_dir,
        "output_dir": tmp_path / "batch",
        "profile": "quant-books",
        "tags": ("gpu", "nightly"),
        "marker_bin": "/opt/marker/bin/marker",
        "parallel_files": 1,
        "marker_workers": 1,
        "gpu_devices": (),
        "timeout_seconds": 0,
    }
    assert "batch-1" in output
    assert "success_count" in output
    assert "failure_count" in output
    assert str(tmp_path / "batch" / "manifest.json") in output
    assert str(tmp_path / "batch" / "logs" / "run.jsonl") in output


def test_cluster_ui_runner_returns_nonzero_when_batch_has_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "Broken.pdf").write_bytes(b"%PDF-1.4\n")
    console = Console(record=True, width=120)

    def fake_run_batch(**_: object) -> BatchRunResult:
        return BatchRunResult(
            batch_id="batch-2",
            success_count=0,
            failure_count=1,
            manifest_path=tmp_path / "batch" / "manifest.json",
            log_path=tmp_path / "batch" / "logs" / "run.jsonl",
        )

    monkeypatch.setattr("tools.marker_cluster_ui.run.run_batch", fake_run_batch)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "batch"),
            "--profile",
            "quant-books",
        ],
        console=console,
    )

    output = console.export_text()

    assert exit_code == 1
    assert "failure_count" in output
    assert "1" in output


def test_cluster_ui_runner_handles_runtime_validation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "Broken.pdf").write_bytes(b"%PDF-1.4\n")
    console = Console(record=True, width=120)

    def fake_run_batch(**_: object) -> BatchRunResult:
        raise ValueError("input directory contains no PDF files")

    monkeypatch.setattr("tools.marker_cluster_ui.run.run_batch", fake_run_batch)

    exit_code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "batch"),
            "--profile",
            "quant-books",
        ],
        console=console,
    )

    output = console.export_text()

    assert exit_code == 2
    assert "Marker batch failed" in output
    assert "contains no PDF files" in output


def test_cluster_ui_parse_args_uses_argparse_exit_code_for_usage_errors() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args([])

    assert exc_info.value.code == 2
