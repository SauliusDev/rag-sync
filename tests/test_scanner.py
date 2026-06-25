from pathlib import Path

from rag_sync.models import ParserMode, Profile, SkipRules
from rag_sync.scanner import discover_files, scan_profile, sha256_file


def test_discover_files_skips_meta_folder(project_tmp: Path):
    source = project_tmp / "videos"
    source.mkdir()
    (source / "Good - Video.md").write_text("ok", encoding="utf-8")
    meta = source / "_meta"
    meta.mkdir()
    (meta / "raw.json").write_text("{}", encoding="utf-8")
    profile = Profile(
        name="videos",
        source_paths=(source,),
        file_types=("md",),
        parser_mode=ParserMode.PASSTHROUGH,
        target_dataset="quant-videos",
        source_type="video",
        skip_rules=SkipRules(path_parts=("_meta",), suffixes=()),
    )

    files = list(discover_files(profile))

    assert [f.source_path.name for f in files] == ["Good - Video.md"]


def test_scan_profile_marks_changed(project_tmp: Path):
    source = project_tmp / "articles"
    source.mkdir()
    article = source / "A - B.md"
    article.write_text("first", encoding="utf-8")
    profile = Profile(
        name="articles",
        source_paths=(source,),
        file_types=("md",),
        parser_mode=ParserMode.PASSTHROUGH,
        target_dataset="quant-articles",
        source_type="article",
    )

    first = scan_profile(profile, existing_hashes={})
    article.write_text("second", encoding="utf-8")
    second = scan_profile(profile, existing_hashes={str(article): first[0].sha256})

    assert second[0].state == "changed"


def test_discover_files_matches_extensions_case_insensitively(project_tmp: Path):
    source = project_tmp / "books"
    source.mkdir()
    pdf = source / "Book.PDF"
    pdf.write_bytes(b"pdf")
    (source / "notes.txt").write_text("skip", encoding="utf-8")
    profile = Profile(
        name="books",
        source_paths=(source,),
        file_types=("pdf",),
        parser_mode=ParserMode.MARKER,
        target_dataset="quant-books",
        source_type="book",
    )

    files = list(discover_files(profile))

    assert [item.source_path for item in files] == [pdf]
    assert files[0].extension == "pdf"
    assert files[0].sha256 == sha256_file(pdf)


def test_scan_profile_marks_new_and_unchanged(project_tmp: Path):
    source = project_tmp / "papers"
    source.mkdir()
    paper = source / "paper.pdf"
    paper.write_bytes(b"paper")
    profile = Profile(
        name="papers",
        source_paths=(source,),
        file_types=("pdf",),
        parser_mode=ParserMode.MARKER,
        target_dataset="quant-papers-md",
        source_type="paper",
    )

    first = scan_profile(profile, existing_hashes={})
    second = scan_profile(profile, existing_hashes={str(paper): first[0].sha256})

    assert first[0].state == "new"
    assert second[0].state == "unchanged"
