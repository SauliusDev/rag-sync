import json
import subprocess
from pathlib import Path

import pytest

from src.import_manifest import load_manifest
from src.marker_batch import run_batch, run_marker_for_file


def test_marker_batch_writes_manifest_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "Book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    def fake_run_marker(*args, **kwargs):
        output_path = tmp_path / "batch" / "outputs" / "Book.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# Book\n", encoding="utf-8")
        return {
            "markdown_path": output_path,
            "returncode": 0,
            "duration_seconds": 1.25,
        }

    monkeypatch.setattr("src.marker_batch.run_marker_for_file", fake_run_marker)

    result = run_batch(
        input_dir=input_dir,
        output_dir=tmp_path / "batch",
        profile="quant-books",
    )

    manifest = json.loads((tmp_path / "batch" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["profile"] == "quant-books"
    assert manifest["parser"] == "marker"
    assert manifest["files"][0]["markdown_relpath"] == "outputs/Book.md"
    assert (tmp_path / "batch" / "logs" / "run.jsonl").exists()
    assert result.success_count == 1


def test_marker_batch_failed_conversion_manifest_loads_with_task1_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "Broken.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    def fake_run_marker(*args, **kwargs):
        return {
            "markdown_path": tmp_path / "batch" / "outputs" / "Broken.md",
            "returncode": 2,
            "duration_seconds": 0.5,
            "stderr": "boom",
        }

    monkeypatch.setattr("src.marker_batch.run_marker_for_file", fake_run_marker)

    run_batch(
        input_dir=input_dir,
        output_dir=tmp_path / "batch",
        profile="quant-books",
    )

    manifest_path = tmp_path / "batch" / "manifest.json"
    manifest = load_manifest(manifest_path)

    assert manifest.files[0].status == "error"
    assert manifest.files[0].markdown_sha256 == "missing"
    assert manifest.files[0].markdown_size_bytes == 0


def test_marker_batch_discovers_nested_pdfs_and_preserves_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    nested_dir = input_dir / "quant" / "books"
    nested_dir.mkdir(parents=True)
    pdf_path = nested_dir / "Book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    seen_paths: list[Path] = []

    def fake_run_marker(*args, **kwargs):
        pdf_path = kwargs["pdf_path"]
        seen_paths.append(pdf_path)
        output_path = kwargs["output_dir"] / "outputs" / "quant" / "books" / "Book.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# Book\n", encoding="utf-8")
        return {
            "markdown_path": output_path,
            "returncode": 0,
            "duration_seconds": 0.5,
        }

    monkeypatch.setattr("src.marker_batch.run_marker_for_file", fake_run_marker)

    run_batch(
        input_dir=input_dir,
        output_dir=tmp_path / "batch",
        profile="quant-books",
    )

    manifest = json.loads((tmp_path / "batch" / "manifest.json").read_text(encoding="utf-8"))
    assert seen_paths == [pdf_path]
    assert manifest["files"][0]["source_relpath"] == "quant/books/Book.pdf"
    assert manifest["files"][0]["markdown_relpath"] == "outputs/quant/books/Book.md"


def test_marker_batch_discovers_uppercase_pdf_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "Book.PDF"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    seen_paths: list[Path] = []

    def fake_run_marker(*args, **kwargs):
        seen_paths.append(kwargs["pdf_path"])
        output_path = kwargs["markdown_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# Book\n", encoding="utf-8")
        return {
            "markdown_path": output_path,
            "returncode": 0,
            "duration_seconds": 0.5,
        }

    monkeypatch.setattr("src.marker_batch.run_marker_for_file", fake_run_marker)

    run_batch(
        input_dir=input_dir,
        output_dir=tmp_path / "batch",
        profile="quant-books",
    )

    manifest = json.loads((tmp_path / "batch" / "manifest.json").read_text(encoding="utf-8"))
    assert seen_paths == [pdf_path]
    assert manifest["files"][0]["source_relpath"] == "Book.PDF"
    assert manifest["files"][0]["markdown_relpath"] == "outputs/Book.md"


def test_marker_batch_duplicate_basenames_do_not_collide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    first_pdf = input_dir / "alpha" / "Book.pdf"
    second_pdf = input_dir / "beta" / "Book.pdf"
    first_pdf.parent.mkdir(parents=True)
    second_pdf.parent.mkdir(parents=True)
    first_pdf.write_bytes(b"%PDF-1.4\nalpha")
    second_pdf.write_bytes(b"%PDF-1.4\nbeta")

    seen_work_dirs: list[Path] = []
    seen_output_paths: list[Path] = []

    def fake_run_marker(*args, **kwargs):
        seen_work_dirs.append(kwargs["work_dir"])
        output_path = kwargs["markdown_path"]
        seen_output_paths.append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"# {kwargs['pdf_path'].parent.name}\n", encoding="utf-8")
        return {
            "markdown_path": output_path,
            "returncode": 0,
            "duration_seconds": 0.5,
        }

    monkeypatch.setattr("src.marker_batch.run_marker_for_file", fake_run_marker)

    run_batch(
        input_dir=input_dir,
        output_dir=tmp_path / "batch",
        profile="quant-books",
    )

    manifest = json.loads((tmp_path / "batch" / "manifest.json").read_text(encoding="utf-8"))
    assert len(set(seen_work_dirs)) == 2
    assert len(set(seen_output_paths)) == 2
    assert {record["markdown_relpath"] for record in manifest["files"]} == {
        "outputs/alpha/Book.md",
        "outputs/beta/Book.md",
    }


def test_marker_batch_rejects_missing_input_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="input directory does not exist"):
        run_batch(
            input_dir=tmp_path / "missing",
            output_dir=tmp_path / "batch",
            profile="quant-books",
        )


def test_marker_batch_rejects_empty_input_dir(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    with pytest.raises(ValueError, match="contains no PDF files"):
        run_batch(
            input_dir=input_dir,
            output_dir=tmp_path / "batch",
            profile="quant-books",
        )


def test_run_marker_for_file_enforces_timeout_and_cleans_up_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "Book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(
        "src.marker_batch.pdf_metadata",
        lambda path: {"pdf_producer": "Test Producer", "page_count": 1},
    )

    class FakeProc:
        pid = 123
        returncode = None

        def communicate(self, timeout: float | None = None):
            raise subprocess.TimeoutExpired(cmd=["marker"], timeout=timeout)

    monkeypatch.setattr(
        "src.marker_batch.subprocess.Popen",
        lambda *args, **kwargs: FakeProc(),
    )
    monkeypatch.setattr(
        "src.marker_batch.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(RuntimeError, match="marker timed out"):
        run_marker_for_file(
            pdf_path=pdf_path,
            output_dir=tmp_path / "batch",
            work_dir=tmp_path / "work",
            markdown_path=tmp_path / "batch" / "outputs" / "Book.md",
            marker_bin="marker",
            timeout_seconds=1,
        )

    assert killed == [(123, 15)]
