from __future__ import annotations

import json
from pathlib import Path

from tools.parser_benchmark import benchmark_suite


def test_build_mineru_command_uses_high_effort_hybrid_defaults(tmp_path: Path) -> None:
    command = benchmark_suite.build_mineru_command(
        mineru_bin="/tmp/mineru",
        source_pdf=tmp_path / "sample.pdf",
        output_dir=tmp_path / "out",
        backend="hybrid-engine",
        effort="high",
    )

    assert command == [
        "/tmp/mineru",
        "-p",
        str(tmp_path / "sample.pdf"),
        "-o",
        str(tmp_path / "out"),
        "-b",
        "hybrid-engine",
        "--effort",
        "high",
    ]


def test_build_paddle_command_supports_pipeline_and_service_options(tmp_path: Path) -> None:
    command = benchmark_suite.build_paddleocr_command(
        paddleocr_bin="/tmp/paddleocr",
        source_pdf=tmp_path / "sample.pdf",
        output_dir=tmp_path / "out",
        pipeline_version="v1.5",
        device="gpu",
        vl_rec_backend="vllm-server",
        vl_rec_server_url="http://localhost:8118/v1",
        vl_rec_api_model_name="PaddlePaddle/PaddleOCR-VL-1.5",
    )

    assert command == [
        "/tmp/paddleocr",
        "doc_parser",
        "-i",
        str(tmp_path / "sample.pdf"),
        "--save_path",
        str(tmp_path / "out"),
        "--pipeline_version",
        "v1.5",
        "--device",
        "gpu",
        "--vl_rec_backend",
        "vllm-server",
        "--vl_rec_server_url",
        "http://localhost:8118/v1",
        "--vl_rec_api_model_name",
        "PaddlePaddle/PaddleOCR-VL-1.5",
    ]


def test_build_glmocr_command_supports_optional_config(tmp_path: Path) -> None:
    command = benchmark_suite.build_glmocr_command(
        glmocr_bin="/tmp/glmocr",
        source_pdf=tmp_path / "sample.pdf",
        output_dir=tmp_path / "out",
        config_path=tmp_path / "config.yaml",
        layout_device="cuda:1",
    )

    assert command == [
        "/tmp/glmocr",
        "parse",
        str(tmp_path / "sample.pdf"),
        "--output",
        str(tmp_path / "out"),
        "--config",
        str(tmp_path / "config.yaml"),
        "--layout-device",
        "cuda:1",
    ]


def test_scan_output_stats_counts_markdown_and_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "a.md").write_text("alpha\n", encoding="utf-8")
    (output_dir / "nested").mkdir()
    (output_dir / "nested" / "b.md").write_text("beta-beta\n", encoding="utf-8")
    (output_dir / "result.json").write_text("{}", encoding="utf-8")

    stats = benchmark_suite.scan_output_stats(output_dir)

    assert stats["markdown_count"] == 2
    assert stats["json_count"] == 1
    assert stats["markdown_bytes"] == 16
    assert stats["largest_markdown_path"] == "nested/b.md"


def test_estimate_progress_uses_page_count_for_multi_markdown_outputs() -> None:
    stats = {
        "markdown_count": 3,
        "markdown_bytes": 999,
        "json_count": 0,
        "json_bytes": 0,
        "largest_markdown_path": None,
        "largest_markdown_bytes": 0,
    }

    progress = benchmark_suite.estimate_progress_percent(
        parser_name="paddleocr-vl",
        output_stats=stats,
        sample_page_count=10,
    )

    assert progress == 30.0


def test_load_parser_settings_merges_toml_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "benchmark.toml"
    config_path.write_text(
        """
[benchmark]
monitor_interval_seconds = 9

[parsers.marker]
enabled = false

[parsers.paddleocr_vl]
device = "cpu"
pipeline_version = "v1.6"

[parsers.mineru.env]
CUDA_VISIBLE_DEVICES = "1"
VLLM_USE_FLASHINFER_SAMPLER = "0"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    settings = benchmark_suite.load_settings(config_path)

    assert settings["benchmark"]["monitor_interval_seconds"] == 9
    assert settings["parsers"]["marker"]["enabled"] is False
    assert settings["parsers"]["paddleocr_vl"]["device"] == "cpu"
    assert settings["parsers"]["paddleocr_vl"]["pipeline_version"] == "v1.6"
    assert settings["parsers"]["mineru"]["env"]["CUDA_VISIBLE_DEVICES"] == "1"
    assert settings["parsers"]["mineru"]["env"]["VLLM_USE_FLASHINFER_SAMPLER"] == "0"


def test_write_run_summary_writes_json_and_markdown(tmp_path: Path) -> None:
    summary = {
        "source_pdf": "/tmp/source.pdf",
        "sample_pdf": "/tmp/sample.pdf",
        "sample_page_count": 10,
        "parsers": [
            {
                "parser": "mineru",
                "status": "ok",
                "duration_seconds": 12.3,
                "pages_per_minute": 48.8,
                "markdown_count": 1,
                "markdown_bytes": 1200,
            }
        ],
    }

    benchmark_suite.write_run_summary(tmp_path, summary)

    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["sample_page_count"] == 10
    assert "MinerU" in (tmp_path / "summary.md").read_text(encoding="utf-8")
