from pathlib import Path

import pytest

from rag_sync.config import DEFAULT_PROFILE_PATH, load_profiles
from rag_sync.models import ParserMode


def write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "profiles.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path


def test_load_profiles_reads_quant_defaults(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "quant-books-md"
source_paths = ["/home/saulius/atlas/notes/quant/books"]
file_types = ["pdf"]
parser_mode = "marker"
target_dataset = "quant-books-md"
source_type = "book"
enabled = true

[profiles.skip_rules]
path_parts = []
suffixes = []
""",
    )

    profiles = load_profiles(config_path)

    assert len(profiles) == 1
    assert profiles[0].name == "quant-books-md"
    assert profiles[0].source_paths == (Path("/home/saulius/atlas/notes/quant/books"),)
    assert profiles[0].file_types == ("pdf",)
    assert profiles[0].parser_mode == ParserMode.MARKER
    assert profiles[0].source_type == "book"


def test_skip_rules_have_defaults(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "videos"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
enabled = true
""",
    )

    profile = load_profiles(config_path)[0]

    assert profile.skip_rules.path_parts == ()
    assert profile.skip_rules.suffixes == ()


def test_load_profiles_reads_committed_config():
    profiles = load_profiles(DEFAULT_PROFILE_PATH)

    assert len(profiles) == 4
    quant_videos = next(profile for profile in profiles if profile.name == "quant-videos")
    assert quant_videos.source_paths == (Path("/home/saulius/atlas/notes/quant/videos"),)
    assert quant_videos.file_types == ("md",)
    assert quant_videos.parser_mode == ParserMode.PASSTHROUGH
    assert quant_videos.skip_rules.path_parts == ("_meta",)
    assert quant_videos.skip_rules.suffixes == (".excalidraw.md",)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "profiles must be a non-empty list"),
        ("profiles = []", "profiles must be a non-empty list"),
        (
            """
[[profiles]]
name = "empty-source-paths"
source_paths = []
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
            "source_paths must be a non-empty list",
        ),
        (
            """
[[profiles]]
name = "non-string-source-path"
source_paths = [1]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
            "non-string-source-path.*source_paths.*non-empty strings",
        ),
        (
            """
[[profiles]]
name = "empty-string-source-path"
source_paths = [""]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
            "empty-string-source-path.*source_paths.*non-empty strings",
        ),
        (
            """
[[profiles]]
name = "empty-file-types"
source_paths = ["/x/videos"]
file_types = []
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
            "file_types must be a non-empty list",
        ),
        (
            """
[[profiles]]
name = "non-string-file-type"
source_paths = ["/x/videos"]
file_types = [1]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
            "non-string-file-type.*file_types.*non-empty strings",
        ),
        (
            """
[[profiles]]
name = "empty-string-file-type"
source_paths = ["/x/videos"]
file_types = [""]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
            "empty-string-file-type.*file_types.*non-empty strings",
        ),
        (
            """
[[profiles]]
name = "bad-enabled"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
enabled = "false"
""",
            "enabled must be a bool",
        ),
        (
            """
[[profiles]]
name = "bad-workers"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
max_upload_workers = 0
""",
            "max_upload_workers must be a positive integer",
        ),
    ],
)
def test_load_profiles_rejects_invalid_config(
    tmp_path: Path, content: str, message: str
):
    config_path = write_config(tmp_path, content)

    with pytest.raises(ValueError, match=message):
        load_profiles(config_path)


def test_load_profiles_rejects_missing_required_scalar_with_value_error(
    tmp_path: Path,
):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "missing-parser"
source_paths = ["/x/videos"]
file_types = ["md"]
target_dataset = "quant-videos"
source_type = "video"
""",
    )

    with pytest.raises(ValueError, match="missing-parser.*parser_mode"):
        load_profiles(config_path)


def test_load_profiles_rejects_non_string_required_scalar(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = 1
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
""",
    )

    with pytest.raises(ValueError, match="profile #1.*name"):
        load_profiles(config_path)


def test_load_profiles_rejects_skip_rule_path_parts_string(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "bad-skip-path-parts"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"

[profiles.skip_rules]
path_parts = "_meta"
""",
    )

    with pytest.raises(ValueError, match="bad-skip-path-parts.*skip_rules.path_parts"):
        load_profiles(config_path)


def test_load_profiles_rejects_skip_rules_non_table(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "bad-skip-rules"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
skip_rules = []
""",
    )

    with pytest.raises(ValueError, match="bad-skip-rules.*skip_rules must be a table"):
        load_profiles(config_path)


def test_load_profiles_rejects_skip_rule_empty_suffix(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "bad-empty-suffix"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"

[profiles.skip_rules]
suffixes = [""]
""",
    )

    with pytest.raises(ValueError, match="bad-empty-suffix.*skip_rules.suffixes"):
        load_profiles(config_path)


def test_load_profiles_rejects_skip_rule_suffixes_non_string(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "bad-skip-suffixes"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"

[profiles.skip_rules]
suffixes = [1]
""",
    )

    with pytest.raises(ValueError, match="bad-skip-suffixes.*skip_rules.suffixes"):
        load_profiles(config_path)


def test_load_profiles_rejects_invalid_output_dir(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "bad-output-dir"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "passthrough"
target_dataset = "quant-videos"
source_type = "video"
output_dir = 123
""",
    )

    with pytest.raises(ValueError, match="bad-output-dir.*output_dir"):
        load_profiles(config_path)


def test_load_profiles_reports_invalid_parser_mode_with_profile_name(tmp_path: Path):
    config_path = write_config(
        tmp_path,
        """
[[profiles]]
name = "bad-parser"
source_paths = ["/x/videos"]
file_types = ["md"]
parser_mode = "unknown"
target_dataset = "quant-videos"
source_type = "video"
""",
    )

    with pytest.raises(ValueError, match="bad-parser.*parser_mode"):
        load_profiles(config_path)
