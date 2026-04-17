"""Performance & query-count regression tests.

These tests are gated behind the ``perf`` marker and excluded from the
default ``pytest`` run (``-m "not perf"`` in pytest.ini addopts).

Run them explicitly::

    pytest -m perf -n 0          # serial — required for pytest-benchmark
    make perf                    # convenience target

See ``baselines.json`` for the expected query counts and the re-baseline
workflow.
"""
