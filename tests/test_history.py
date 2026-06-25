from rag_sync.history import (
    estimate_from_history,
    estimate_from_live_progress,
    format_eta_seconds,
)


def test_estimate_from_live_progress_uses_elapsed_and_progress():
    assert estimate_from_live_progress(elapsed_seconds=600, progress=0.25) == 1800


def test_estimate_from_live_progress_rejects_zero_and_complete_progress():
    assert estimate_from_live_progress(elapsed_seconds=600, progress=0) is None
    assert estimate_from_live_progress(elapsed_seconds=600, progress=1) == 0


def test_estimate_from_history_uses_matching_group_average():
    rows = [
        {
            "profile_name": "quant-books",
            "source_type": "book",
            "parser": "marker",
            "stage": "convert",
            "duration_seconds": 10.0,
        },
        {
            "profile_name": "quant-books",
            "source_type": "book",
            "parser": "marker",
            "stage": "convert",
            "duration_seconds": 20.0,
        },
        {
            "profile_name": "quant-papers",
            "source_type": "paper",
            "parser": "marker",
            "stage": "convert",
            "duration_seconds": 100.0,
        },
    ]

    assert estimate_from_history(
        rows,
        profile_name="quant-books",
        source_type="book",
        parser="marker",
        stage="convert",
    ) == 15


def test_format_eta_seconds_is_compact():
    assert format_eta_seconds(None) == "unknown"
    assert format_eta_seconds(45) == "45s"
    assert format_eta_seconds(125) == "2m"
    assert format_eta_seconds(3700) == "1h 1m"
