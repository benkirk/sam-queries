#!/usr/bin/env bash
# Regenerate the frozen data under data/ (and the charts drawn from it).
# Hand-run only: rendering the decks never runs it.
#
#   ./refresh_data.sh        # SAMUEL_REPO defaults to this checkout
#
# Reads the checkout's origin/main (fetch first) for the LOC chart, and its
# working tree for the SAM models. Needs the sam-queries python3 (matplotlib,
# the models) and the obfuscated test DB on 127.0.0.1:3307 (count_tables.py).
set -euo pipefail
cd "$(dirname "$0")"
repo=${SAMUEL_REPO:-$(git rev-parse --show-toplevel)}
ref=${SAMUEL_REF:-origin/main}

# LOC = every text line in the tree (code, tests, docs, fixtures): the same
# method as the March 2026 "Project SAMuel Progression" slide, which it
# reproduces exactly (8,791 / 51,829 / 64,202 / 75,420).
# Split: tests = tests/ dirs, test_*.py, conftest.py; other = docs/ and every non-code file;
# source = code files everywhere else.
split() {
    git -C "$repo" grep -I -c '' "$1" -- . | awk -F: '
        { p = $2; n = $NF; all += n }
        p ~ /\.py$/ { py += n }
        p ~ /(^|\/)tests?\// || p ~ /(^|\/)(test_[^\/]*|conftest)\.py$/ { t += n; next }
        p ~ /^docs\// || p !~ /\.(py|html|js|css|sh|lua|j2|jinja|ipynb)$/ { o += n; next }
        { s += n }
        END { printf "%d\t%d\t%d\t%d\t%d", all, s, t, o, py }'
}

out=data/loc_progression.tsv
printf 'date\tcommits\tlines\tsource_lines\ttest_lines\tother_lines\tpython_lines\n' > "$out"
first=$(git -C "$repo" log --reverse --format=%ad --date=short "$ref" | awk 'NR == 1')
# Month-ends from the first commit through today (today closes the series).
month_ends() {
    python3 -c 'import sys, datetime as dt
d, today = dt.date.fromisoformat(sys.argv[1]).replace(day=1), dt.date.today()
while True:
    n = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    print(min(n - dt.timedelta(days=1), today))
    if n > today: break
    d = n' "$1"
}
for end in $(month_ends "$first"); do
    rev=$(git -C "$repo" rev-list -1 --before="$end 23:59:59" "$ref")
    printf '%s\t%s\t%s\n' "$end" "$(git -C "$repo" rev-list --count "$rev")" "$(split "$rev")" >> "$out"
done
cat "$out"

python3 plot_progression.py

# Table counts, live vs ORM (count_tables.py refuses anything but port 3307).
SAMUEL_REPO=$repo python3 count_tables.py

# Part 2 (Concepts): SCSG0001's accounts, users and ledger; two SAM-side trees; the tree audit.
SAMUEL_REPO=$repo python3 concepts_data.py

# Part 4: the ncar-hpc-deploy schedule, verbatim (comments dropped but the header). Read from the ref, no DB.
{ printf '```\n'
  git -C "$repo" show "$ref:containers/ncar-hpc-deploy/etc/schedule" | awk '/^# cadence/ || (!/^#/ && NF)'
  printf '```\n'
} > _out_schedule.qmd

# ER fragments from the ORM metadata, one ```{dot} cell each.
# er <name> <er_diagram.py args...>, then optional extra dot lines on stdin.
er() {
    local name=$1; shift
    { printf '```{dot}\n//| fig-width: 10\n'
      python3 "$repo/scripts/er_diagram.py" "$@" | sed '$d'
      cat
      printf '}\n```\n'
    } > "_er_$name.qmd"
}
er core project:projcode account allocation resources:resource_name \
    account_user users:username < /dev/null
# Invisible edges stack the five summaries in two rows (one row runs ~5:1).
er balance --rankdir TB allocation:amount,start_date,end_date account \
    comp_charge_summary:activity_date,charges dav_charge_summary:activity_date,charges \
    hpc_charge_summary:activity_date,charges \
    disk_charge_summary:activity_date,charges archive_charge_summary:activity_date,charges \
    charge_adjustment:adjustment_date,amount <<'EOF_DOT'
  "comp_charge_summary" -> "disk_charge_summary" [style=invis];
  "dav_charge_summary" -> "archive_charge_summary" [style=invis];
  "hpc_charge_summary" -> "allocation" [style=invis];
EOF_DOT
