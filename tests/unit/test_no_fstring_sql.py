"""Regression guard: no request-derived SQL string interpolation (SQLi).

The codebase hits the DB via SQLAlchemy ORM / bound params. A handful of
`text(f"...")` builders exist, but each interpolates ONLY structural tokens
(bind-placeholder names like `:ak{i}`, hardcoded `__tablename__` / column names,
server-derived int ids) — never request data. Verified end-to-end in the 2026-06
ZAP review; see docs/nrit-review-2026-05/10_zap_baseline-2026-06.md (§ Active scan).

This test fails when a NEW or changed `text(f"...")` appears, forcing a reviewer
to confirm it interpolates no request-derived value before allowlisting it — that
is exactly where a real SQL injection would be introduced.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# text( — optionally spanning whitespace/newlines — then an f-string quote.
_FSTRING_SQL = re.compile(r'\btext\s*\(\s*f["\']')

# Audited-safe f-string SQL: path (relative to src/) -> occurrence count.
# Each interpolates ONLY structural/server-derived tokens. Change a count only
# after re-auditing the statement(s); see the module docstring.
_ALLOWLIST = {
    "sam/base.py": 4,                   # MPTT tree-shift UPDATEs: {table}/{root_col}
    "sam/projects/projects.py": 4,      # charge VALUES-CTE: placeholder names + __tablename__
    "sam/queries/rolling_usage.py": 4,  # rolling charge VALUES-CTE: same pattern
}


def _scan():
    found = {}
    for py in SRC.rglob("*.py"):
        n = len(_FSTRING_SQL.findall(py.read_text(encoding="utf-8")))
        if n:
            found[py.relative_to(SRC).as_posix()] = n
    return found


def test_no_unreviewed_fstring_sql():
    found = _scan()
    drift = {f: (n, _ALLOWLIST.get(f, 0))
             for f, n in found.items() if _ALLOWLIST.get(f) != n}
    assert not drift, (
        "Unreviewed f-string-built SQL (possible SQL-injection vector):\n"
        + "\n".join(f"  {f}: found {n}, allowlisted {a}" for f, (n, a) in drift.items())
        + "\n\nConfirm each new text(f\"...\") interpolates ONLY structural tokens "
          "(placeholder names, hardcoded table/column names) and NO request-derived "
          "data, then update _ALLOWLIST. See "
          "docs/nrit-review-2026-05/10_zap_baseline-2026-06.md."
    )
