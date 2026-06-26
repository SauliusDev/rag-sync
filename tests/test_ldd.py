import json
from pathlib import Path

from rag_sync import ldd


def test_log_event_appends_structured_jsonl(project_tmp: Path):
    log_path = project_tmp / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)

    try:
        ldd.log_event(
            "parser.command.started",
            "ok",
            parser="marker",
            source_file_id=42,
        )
    finally:
        ldd.set_log_path_for_tests(None)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "parser.command.started"
    assert record["status"] == "ok"
    assert record["parser"] == "marker"
    assert record["source_file_id"] == 42
    assert isinstance(record["ts"], str)
