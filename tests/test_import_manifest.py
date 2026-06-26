import json
from pathlib import Path

import pytest

from rag_sync.import_manifest import load_manifest


def test_manifest_parser_rejects_missing_required_fields(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"batch_id": "b1", "files": [{}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="source_relpath"):
        load_manifest(manifest)
