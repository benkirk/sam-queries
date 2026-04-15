# new_tests/ — Planned test suite replacement

Incremental replacement for `tests/`. See `docs/plans/REFACTOR_TESTING.md` for
the full migration design. Short version:

- Runs against the **isolated `mysql-test` container** (host port `3307`),
  not the shared dev DB on port 3306.
- `conftest.py` has a hard safety guard that refuses to run against any
  other database.
- Ported files delete their old-suite counterpart in the same PR. No
  long-lived duplication.

## How to run

```bash
# 1. Start the test container (first time takes ~2 min for the dump restore)
docker compose --profile test up -d mysql-test

# 2. Wait for it to be actually ready (the compose healthcheck lies during init)
until mysqladmin ping -h 127.0.0.1 -P 3307 -u root -proot --silent 2>/dev/null; do
    sleep 2
done

# 3. Run the new suite (requires SAM_TEST_DB_URL so the allowlist guard passes)
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
pytest -c new_tests/pytest.ini new_tests/
```

If you forget `SAM_TEST_DB_URL`, or if it points at something other than the
approved `(127.0.0.1, 3307)` test container, the whole session aborts with
`REFUSING TO RUN`. That is the whole point.

## Layout (incremental; will fill in over time)

```
new_tests/
├── README.md             # this file
├── conftest.py           # safety guard, engine fixture
├── pytest.ini            # opts out of root pytest config
├── fixtures/
│   └── db.py             # engine + session factories
└── unit/
    └── test_smoke.py     # smoke test — proves infrastructure works
```

Future additions (see plan): `factories/`, `integration/`, `webapp/`, `perf/`.

## Invocation notes

- Always pass `-c new_tests/pytest.ini`. Without it, pytest picks up the
  root `pytest.ini` which has `testpaths=tests/`, hooking you up to the
  wrong conftest and running everything against the wrong DB.
- Parallel (`-n auto`) is enabled by default in `new_tests/pytest.ini`.
- Coverage is off by default for inner-loop speed; add `--cov=src` for CI.
