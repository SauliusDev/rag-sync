import pytest

from src.retrieval import FORMULA_BENCHMARK_QUERIES, query_set


def test_formula_benchmark_query_set_contract():
    queries = query_set("formula-benchmark")

    assert len(queries) == 10
    assert [query_id for query_id, _ in queries] == [f"Q{index}" for index in range(1, 11)]
    assert queries[0] == FORMULA_BENCHMARK_QUERIES[0]


def test_query_set_returns_copy():
    queries = query_set("formula-benchmark")
    queries.clear()

    assert len(query_set("formula-benchmark")) == 10


def test_query_set_rejects_unknown_name():
    with pytest.raises(KeyError, match="unknown query set: missing"):
        query_set("missing")
