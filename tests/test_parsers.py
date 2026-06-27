from pathlib import Path
import json

import pytest

from rag_sync import ldd
from rag_sync import parsers
from rag_sync.parsers import MarkerParser, MinerUParser, PassthroughParser, build_marker_command


def _fake_completed_process(
    output_dir: Path,
    markdown_paths: list[str] | None = None,
    *,
    body: str = "body",
):
    if markdown_paths:
        for relative_path in markdown_paths:
            path = output_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    return parsers.subprocess.CompletedProcess(
        args=["parser"],
        returncode=0,
        stdout="ok",
        stderr="",
    )


def test_passthrough_parser_writes_upload_copy(project_tmp: Path):
    source = project_tmp / "note.md"
    source.write_text("hello", encoding="utf-8")
    output = project_tmp / "out.md"

    result = PassthroughParser().convert(source, output, "article", "abc")

    assert result.output_path == output
    assert "hello" in output.read_text(encoding="utf-8")


def test_build_marker_command_uses_output_parent(project_tmp: Path):
    source = project_tmp / "marker-input"
    output = project_tmp / "out" / "book.md"
    cmd = build_marker_command(source, output)

    assert cmd[0].endswith("marker")
    assert str(source) in cmd
    assert "--output_dir" in cmd
    assert str(output.parent) in cmd
    assert "--disable_ocr" in cmd


def test_build_marker_command_can_leave_ocr_enabled(project_tmp: Path):
    source = project_tmp / "marker-input"
    output = project_tmp / "out" / "book.md"

    cmd = build_marker_command(source, output, disable_ocr=False)

    assert "--disable_ocr" not in cmd
    assert "--disable_multiprocessing" in cmd
    assert "--recognition_batch_size" in cmd
    assert "ocr_without_boxes" in cmd
    assert "--drop_repeated_text" in cmd


def test_marker_parser_enables_ocr_when_pdf_has_no_extractable_text(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"

    def fake_run(cmd: list[str], **kwargs: object):
        assert "--disable_ocr" not in cmd
        output_dir = Path(cmd[cmd.index("--output_dir") + 1])
        return _fake_completed_process(output_dir, ["book.md"], body="ocr marker body")

    monkeypatch.setattr(parsers, "_run_parser_command", lambda cmd, **kwargs: fake_run(cmd, **kwargs))
    monkeypatch.setattr(parsers, "_should_disable_marker_ocr_for_pdf", lambda **kwargs: False)

    result = MarkerParser().convert(source, output, "book", "abc")

    assert result.output_path == output
    assert "ocr marker body" in output.read_text(encoding="utf-8")


def test_should_disable_marker_ocr_for_pdf_detects_extractable_text(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")

    class FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, path: Path):
            assert path == source
            self.pages = [FakePage("hello world " * 8)]

    monkeypatch.setattr(parsers, "PdfReader", FakeReader)

    assert parsers._should_disable_marker_ocr_for_pdf(source_path=source, pdf_producer="") is True


def test_should_disable_marker_ocr_for_pdf_keeps_ocr_for_image_pdf(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")

    class FakePage:
        def extract_text(self) -> str:
            return ""

    class FakeReader:
        def __init__(self, path: Path):
            assert path == source
            self.pages = [FakePage()]

    monkeypatch.setattr(parsers, "PdfReader", FakeReader)

    assert (
        parsers._should_disable_marker_ocr_for_pdf(
            source_path=source,
            pdf_producer="CVISION Technologies' PDFCompressor 2.0",
        )
        is False
    )


def test_should_disable_marker_ocr_for_pdf_keeps_ocr_for_text_cover_image_body(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "jstor.pdf"
    source.write_bytes(b"pdf")

    class FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, path: Path):
            assert path == source
            self.pages = [FakePage("Continuous Auctions and Insider Trading " * 2)]
            self.pages.extend(FakePage("") for _ in range(22))

    monkeypatch.setattr(parsers, "PdfReader", FakeReader)

    assert parsers._should_disable_marker_ocr_for_pdf(source_path=source, pdf_producer="") is False


def test_should_disable_marker_ocr_for_pdf_keeps_ocr_for_repeated_banner_text(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "heston.pdf"
    source.write_bytes(b"pdf")
    repeated = "at Univ of Wisconsin-Madison General Library System on September 27, 2012 http://"

    class FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, path: Path):
            assert path == source
            self.pages = [FakePage(repeated) for _ in range(17)]

    monkeypatch.setattr(parsers, "PdfReader", FakeReader)

    assert parsers._should_disable_marker_ocr_for_pdf(source_path=source, pdf_producer="") is False


def test_marker_parser_preserves_original_source_and_ignores_stale_output(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"
    output.parent.mkdir()
    output.write_text("stale final output", encoding="utf-8")

    def fake_run(cmd: list[str], **kwargs: object):
        input_dir = Path(cmd[1])
        assert input_dir.is_dir()
        assert (input_dir / "book.pdf").read_bytes() == b"pdf"
        output_dir = Path(cmd[cmd.index("--output_dir") + 1])
        return _fake_completed_process(output_dir, ["book.md"], body="fresh marker body")

    monkeypatch.setattr(parsers, "_run_parser_command", lambda cmd, **kwargs: fake_run(cmd, **kwargs))

    result = MarkerParser().convert(source, output, "book", "abc")

    text = output.read_text(encoding="utf-8")
    assert result.output_path == output
    assert 'source_path: "' + str(source) + '"' in text
    assert 'parser: "marker"' in text
    assert "fresh marker body" in text
    assert "stale final output" not in text


def test_mineru_parser_preserves_original_source(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "paper.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "paper.md"

    def fake_run(cmd: list[str], **kwargs: object):
        output_dir = Path(cmd[cmd.index("--output") + 1])
        return _fake_completed_process(output_dir, ["nested/paper.md"], body="fresh mineru body")

    monkeypatch.setattr(parsers, "_run_parser_command", lambda cmd, **kwargs: fake_run(cmd, **kwargs))

    result = MinerUParser().convert(source, output, "paper", "abc")

    text = output.read_text(encoding="utf-8")
    assert result.output_path == output
    assert 'source_path: "' + str(source) + '"' in text
    assert 'parser: "mineru"' in text
    assert "fresh mineru body" in text


def test_marker_parser_fails_when_multiple_markdown_outputs(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"

    def fake_run(cmd: list[str], **kwargs: object):
        output_dir = Path(cmd[cmd.index("--output_dir") + 1])
        return _fake_completed_process(output_dir, ["a.md", "b.md"])

    monkeypatch.setattr(parsers, "_run_parser_command", lambda cmd, **kwargs: fake_run(cmd, **kwargs))

    with pytest.raises(RuntimeError, match="multiple markdown files"):
        MarkerParser().convert(source, output, "book", "abc")


def test_mineru_parser_fails_when_no_markdown_output(
    monkeypatch: pytest.MonkeyPatch, project_tmp: Path
):
    source = project_tmp / "paper.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "paper.md"

    def fake_run(cmd: list[str], **kwargs: object):
        output_dir = Path(cmd[cmd.index("--output") + 1])
        return _fake_completed_process(output_dir)

    monkeypatch.setattr(parsers, "_run_parser_command", lambda cmd, **kwargs: fake_run(cmd, **kwargs))

    with pytest.raises(RuntimeError, match="produced no markdown"):
        MinerUParser().convert(source, output, "paper", "abc")


def test_marker_parser_wraps_nonzero_exit(monkeypatch: pytest.MonkeyPatch, project_tmp: Path):
    source = project_tmp / "book.pdf"
    source.write_bytes(b"pdf")
    output = project_tmp / "out" / "book.md"
    log_path = project_tmp / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)

    def fake_run(cmd: list[str], **kwargs: object):
        return parsers.subprocess.CompletedProcess(
            args=cmd,
            returncode=2,
            stdout="",
            stderr="boom",
        )

    monkeypatch.setattr(parsers, "_run_parser_command", lambda cmd, **kwargs: fake_run(cmd, **kwargs))

    try:
        with pytest.raises(RuntimeError, match="marker failed"):
            MarkerParser().convert(source, output, "book", "abc")
    finally:
        ldd.set_log_path_for_tests(None)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    failed = [record for record in records if record["event"] == "parser.failed"]
    assert failed
    assert failed[-1]["status"] == "error"
    assert failed[-1]["parser"] == "marker"
    assert failed[-1]["source_path"] == str(source)


def test_run_parser_command_logs_start_and_finish(monkeypatch: pytest.MonkeyPatch, project_tmp: Path):
    log_path = project_tmp / "rag-sync.log"
    ldd.set_log_path_for_tests(log_path)

    class FakeProc:
        pid = 456
        returncode = 0

        def communicate(self, timeout: float | None = None):
            return "stdout text", "stderr text"

    monkeypatch.setattr(parsers.subprocess, "Popen", lambda *args, **kwargs: FakeProc())

    try:
        result = parsers._run_parser_command(
            ["marker", "input.pdf"],
            parser_name="marker",
            timeout_seconds=120,
        )
    finally:
        ldd.set_log_path_for_tests(None)

    assert result.returncode == 0
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == [
        "parser.command.started",
        "parser.command.finished",
    ]
    assert records[0]["status"] == "ok"
    assert records[0]["parser"] == "marker"
    assert records[1]["status"] == "ok"
    assert records[1]["returncode"] == 0
    assert records[1]["stdout_bytes"] == len("stdout text")
    assert records[1]["stderr_bytes"] == len("stderr text")


def test_run_parser_command_times_out_and_kills_process(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[int, int]] = []

    class FakeProc:
        pid = 123
        returncode = None

        def communicate(self, timeout: float | None = None):
            raise parsers.subprocess.TimeoutExpired(cmd=["marker"], timeout=timeout)

        def kill(self):
            self.returncode = -9

        def poll(self):
            return None

    monkeypatch.setattr(parsers.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(parsers.os, "killpg", lambda pid, sig: calls.append((pid, sig)))

    with pytest.raises(RuntimeError, match="marker timed out after 120s"):
        parsers._run_parser_command(["marker"], parser_name="marker", timeout_seconds=120)

    assert calls == [(123, 15)]
