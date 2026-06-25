from pathlib import Path

from rag_sync.config import load_profiles
from rag_sync.models import ParserMode


def test_load_profiles_reads_quant_defaults(tmp_path: Path):
    config_path = tmp_path / "profiles.toml"
    config_path.write_text(
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
        encoding="utf-8",
    )

    profiles = load_profiles(config_path)

    assert len(profiles) == 1
    assert profiles[0].name == "quant-books-md"
    assert profiles[0].parser_mode == ParserMode.MARKER
    assert profiles[0].source_type == "book"


def test_skip_rules_have_defaults(tmp_path: Path):
    config_path = tmp_path / "profiles.toml"
    config_path.write_text(
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
        encoding="utf-8",
    )

    profile = load_profiles(config_path)[0]

    assert profile.skip_rules.path_parts == []
    assert profile.skip_rules.suffixes == []
