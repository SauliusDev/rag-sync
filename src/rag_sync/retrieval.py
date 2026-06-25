from __future__ import annotations

FORMULA_BENCHMARK_QUERIES: list[tuple[str, str]] = [
    (
        "Q1",
        "In AFML, give the exact fractional differentiation binomial expansion for (1-B)^d.",
    ),
    (
        "Q2",
        "In AFML, give the exact formula for fractional differentiation weights omega "
        "and the iterative omega_k formula.",
    ),
    (
        "Q3",
        "In AFML section 5.5.1, give the exact relative weight-loss lambda_l formula "
        "and the condition defining l star.",
    ),
    (
        "Q4",
        "In Shreve II section 4.5.4, give the exact Black-Scholes-Merton call "
        "solution c(t,x).",
    ),
    (
        "Q5",
        "In Shreve II section 4.5.4, give the exact d plus and d minus definitions "
        "and the boundary condition at x equals infinity.",
    ),
    (
        "Q6",
        "In Hull chapter 15 section 15.8, give the exact Black-Scholes-Merton call "
        "and put pricing formulas.",
    ),
    ("Q7", "In Hull chapter 15 section 15.8, give exact d1 and d2 definitions."),
    ("Q8", "In Matrix Cookbook section 2, give the exact differential identity for X inverse."),
    (
        "Q9",
        "In Matrix Cookbook section 2, give exact identities for differential of "
        "determinant and log determinant.",
    ),
    (
        "Q10",
        "In Matrix Cookbook section 6.2, give formulas for E[AXB+C], Var[Ax], "
        "and Cov[Ax,By].",
    ),
]


def query_set(name: str) -> list[tuple[str, str]]:
    if name == "formula-benchmark":
        return FORMULA_BENCHMARK_QUERIES
    raise KeyError(f"unknown query set: {name}")
