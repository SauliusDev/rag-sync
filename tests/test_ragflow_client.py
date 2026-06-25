from pathlib import Path

from rag_sync.ragflow_client import read_env_value, redact_secret


def test_read_env_value_does_not_print_secret(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('RAGFLOW_MCP_HOST_API_KEY="secret-value"\n', encoding="utf-8")

    assert read_env_value(env, "RAGFLOW_MCP_HOST_API_KEY") == "secret-value"


def test_redact_secret_replaces_secret():
    assert redact_secret("abc secret-value def", "secret-value") == "abc [REDACTED] def"
