import json
from pathlib import Path

from rag_sync.marker_batch import run_batch


def test_marker_batch_writes_manifest_and_logs(
    tmp_path: Path,
    monkeypatch,
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

    monkeypatch.setattr("rag_sync.marker_batch.run_marker_for_file", fake_run_marker)

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
