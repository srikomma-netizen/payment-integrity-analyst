from analyst.evals.run_evals import load_golden, rows_equal, run_suite, summarize


def test_rows_equal_semantics():
    assert rows_equal([[1, 2.0]], [[1, 2.00004]], ordered=False)  # 1e-4 tol
    assert rows_equal([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], ordered=False)
    assert not rows_equal([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], ordered=True)
    assert not rows_equal([[1]], [[1], [2]], ordered=False)


def test_golden_suite_passes_offline():
    results = run_suite(provider="fake")
    failures = [(r.id, r.notes) for r in results if not r.passed]
    assert not failures, failures
    summary = summarize(results)
    assert summary["passed"] == len(load_golden())
