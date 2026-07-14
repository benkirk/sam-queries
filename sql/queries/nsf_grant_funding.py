#!/usr/bin/env python3
"""
Standalone NSF grant-funding rollup.

Answers: "From START to END we supported N NSF funding grants totalling $M in
research" — overall and per compute resource (Derecho / Casper / ...).

SAM's DB has no award dollar amount, so the amounts are pulled from the public
NSF Awards API (api.nsf.gov) via the shared resolver in nsf_awards.py and cached
in nsf_award_lookups.csv. Only contract_source='NSF' awards resolve; a handful of
very old awards have no public record and are reported as "unresolved".

Reuses the Q5/Q6 CSVs already produced by build_annual_report.sh /
run_nsf_grant_funding.sh — no new SQL required:
    usage_q5_projects_with_nsf__lump.csv   (projcode -> NSF contracts)
    usage_q6_compute_by_project_machine__lump.csv  (projcode -> machine hours)

Usage:
    python3 nsf_grant_funding.py --in-dir <dir> [--maps sql/queries] \\
        [--start 2025-07-01] [--end 2026-06-30] [--csv out.csv] [--no-network]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

# Shared NSF Awards API resolver + funding aggregation.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nsf_awards import (  # noqa: E402
    nsf_award_id, load_cache, resolve_awards, load_university_types,
    is_cooperative_agreement, summarize_tiers, top_awards,
)

try:
    from rich.console import Console
    from rich.table import Table
    _HAVE_RICH = True
except ImportError:                       # portable fallback (sibling scripts
    _HAVE_RICH = False                    # in this dir are stdlib-only)


def _f(v, default=0.0):
    if v is None:
        return default
    s = str(v).strip().strip('"')
    if not s or s.upper() == "NULL":
        return default
    try:
        return float(s)
    except ValueError:
        return default


# ------------------------------- load ---------------------------------------

def load_projects(q5_path, q6_path, university_types=frozenset(),
                  award_cache_path=None, allow_network=True):
    """
    Build {projcode: {nsf_award_ids, is_university, derecho_ch, casper_ch,
    other_ch}} from the Q5/Q6 CSVs, and return it alongside the resolved
    award_cache, an award_id -> contract_title map, and the set of
    cooperative-agreement award ids (NCAR core awards).
    """
    projects = {}
    award_titles = {}      # award_id -> contract title (for top-N + coop detect)
    coop_ids = set()

    # First pass over Q5: gather every NSF award id + title to batch-resolve once.
    award_ids = set()
    with open(q5_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("contract_source") or "").strip().upper() != "NSF":
                continue
            aid = nsf_award_id(row.get("contract_number"))
            if not aid:
                continue
            award_ids.add(aid)
            title = (row.get("contract_title") or "").strip()
            award_titles.setdefault(aid, title)
            if is_cooperative_agreement(title):
                coop_ids.add(aid)

    award_cache = {}
    if award_cache_path:
        if award_ids and allow_network:
            award_cache = resolve_awards(award_ids, award_cache_path)
        else:
            award_cache = load_cache(award_cache_path)

    # Second pass over Q5: attach award ids + university flag per project.
    # projcode is upper-cased on both sides of the Q5/Q6 join: comp_charge_summary
    # stores historical projcodes in lowercase (e.g. 2020 data) while the project
    # table is uppercase. MySQL joins are case-insensitive so Q5 looks fine, but a
    # Python dict lookup is not — without this, old-data compute silently fails to
    # attribute to Derecho/Casper.
    with open(q5_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pc = (row.get("projcode") or "").strip().upper()
            if not pc:
                continue
            p = projects.setdefault(pc, {
                "nsf_award_ids": set(),
                "is_university": (row.get("allocation_type") or "").strip() in university_types,
                "derecho_ch": 0.0,
                "casper_ch": 0.0,
                "other_ch": defaultdict(float),
            })
            if (row.get("contract_source") or "").strip().upper() == "NSF":
                aid = nsf_award_id(row.get("contract_number"))
                if aid:
                    p["nsf_award_ids"].add(aid)

    # Q6: resource attribution (mirrors build_annual_report's per-system pivot).
    with open(q6_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pc = (row.get("projcode") or "").strip().upper()
            if pc not in projects:
                continue
            machine = (row.get("machine") or "").strip().lower()
            ch = _f(row.get("total_core_hours"))
            if machine.startswith("derecho"):
                projects[pc]["derecho_ch"] += ch
            elif machine.startswith("casper"):
                projects[pc]["casper_ch"] += ch
            elif machine:
                projects[pc]["other_ch"][machine] += ch

    return projects, award_cache, award_titles, coop_ids


# ------------------------------- render -------------------------------------

def _money(x):
    return "—" if not x else f"${x:,.0f}"


def _money_compact(x):
    if not x:
        return "$0"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:,.0f}"


TIER_LABELS = [
    ("all", "All NSF grants"),
    ("excl_coop", "Excl. NCAR coop agreements"),
    ("university", "University projects only"),
]


def _resource_rows(summary):
    """Ordered [(label, stats)] with OVERALL first, then Derecho/Casper/other."""
    order = {"Derecho": 0, "Casper": 1}
    labels = sorted(summary["by_resource"], key=lambda s: (order.get(s, 2), s))
    return [("OVERALL (any resource)", summary["overall"])] + \
           [(lbl, summary["by_resource"][lbl]) for lbl in labels]


def render(tiers, tops, start, end):
    span = f"from {start} to {end} " if start and end else ""
    # Headline uses estimated-total (fundsObligated is unreliable in aggregate),
    # excl-cooperative scope (NCAR's own award is circular).
    hl = tiers["excl_coop"]["overall"]
    headline = (f"NSF grant funding {span}(excl. NCAR cooperative agreements): "
                f"{hl['grants']} unique NSF grant(s) totalling "
                f"{_money_compact(hl['est_total'])} in estimated total award value "
                f"(~{_money_compact(hl['annual'])} annualized)")

    def tier_table_rows():
        for key, label in TIER_LABELS:
            r = tiers[key]["overall"]
            yield (label, r)

    cols = ("NSF Grants", "Grants w/ $", "Est. Total Award $",
            "Funds Obligated $", "Annualized $/yr")

    if _HAVE_RICH:
        console = Console()
        console.rule("[bold]NSF Grant Funding[/bold]")
        console.print(headline + "\n")

        t1 = Table(title="By scope (all resources)", show_header=True, header_style="bold")
        t1.add_column("Scope")
        for col in cols:
            t1.add_column(col, justify="right")
        for label, r in tier_table_rows():
            t1.add_row(label, str(r["grants"]), str(r["resolved"]),
                       _money(r["est_total"]), _money(r["obligated"]), _money(r["annual"]))
        console.print(t1)

        t2 = Table(title="By resource (excl. NCAR cooperative agreements)",
                   show_header=True, header_style="bold")
        t2.add_column("Resource")
        for col in cols:
            t2.add_column(col, justify="right")
        for label, r in _resource_rows(tiers["excl_coop"]):
            t2.add_row(label, str(r["grants"]), str(r["resolved"]),
                       _money(r["est_total"]), _money(r["obligated"]), _money(r["annual"]))
        console.print(t2)

        if tops:
            t3 = Table(title="Largest awards (by estimated total, all NSF grants)",
                       show_header=True, header_style="bold")
            t3.add_column("Award")
            t3.add_column("Est. Total $", justify="right")
            t3.add_column("Title")
            for aid, title, est, _obl in tops:
                t3.add_row(aid, _money(est), (title or "")[:52])
            console.print(t3)
    else:
        print("=" * 72)
        print("NSF Grant Funding")
        print("=" * 72)
        print(headline + "\n")
        hdr = (f"{'Scope':<28}{'Grants':>8}{'w/ $':>7}{'Est. Total $':>18}"
               f"{'Obligated $':>18}{'Annualized $/yr':>18}")
        print(hdr); print("-" * len(hdr))
        for label, r in tier_table_rows():
            print(f"{label:<28}{r['grants']:>8}{r['resolved']:>7}"
                  f"{_money(r['est_total']):>18}{_money(r['obligated']):>18}{_money(r['annual']):>18}")
        print(f"\nBy resource (excl. NCAR cooperative agreements):")
        print(hdr.replace("Scope", "Resource")); print("-" * len(hdr))
        for label, r in _resource_rows(tiers["excl_coop"]):
            print(f"{label:<28}{r['grants']:>8}{r['resolved']:>7}"
                  f"{_money(r['est_total']):>18}{_money(r['obligated']):>18}{_money(r['annual']):>18}")
        if tops:
            print("\nLargest awards (by estimated total, all NSF grants):")
            for aid, title, est, _obl in tops:
                print(f"  {aid:<12}{_money(est):>16}  {(title or '')[:52]}")

    print(f"\nSource: api.nsf.gov (NSF-sourced contracts only). A grant funding "
          f"projects on\nseveral systems is counted once overall and once per "
          f"system it supported.")
    unresolved = tiers["all"]["unresolved_ids"]
    if unresolved:
        print(f"Note: {len(unresolved)} NSF award id(s) had no public amount "
              f"(pre-~2000 or withdrawn)\nand are excluded from the dollar totals.")
    suspect = tiers["all"]["suspect_obligated_ids"]
    if suspect:
        print(f"Note: {len(suspect)} award(s) report funds-obligated > estimated-total "
              f"(cumulative\ncooperative-agreement obligations) — prefer the "
              f"estimated-total column.")


def write_csv(tiers, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scope", "resource", "nsf_grants", "grants_with_amount",
                    "estimated_total_award_usd", "funds_obligated_usd",
                    "annualized_usd_per_yr", "grants_annualized"])
        for key, label in TIER_LABELS:
            for res, r in _resource_rows(tiers[key]):
                w.writerow([label, res, r["grants"], r["resolved"],
                            f"{r['est_total']:.0f}", f"{r['obligated']:.0f}",
                            f"{r['annual']:.0f}", r["annualized"]])
    print(f"wrote {path}", file=sys.stderr)


# -------------------------------- main --------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", required=True,
                    help="Directory holding usage_q5/q6 *__lump.csv")
    ap.add_argument("--maps", default=os.path.dirname(os.path.abspath(__file__)),
                    help="Directory holding nsf_award_lookups.csv (the API cache)")
    ap.add_argument("--start", help="YYYY-MM-DD (for the headline sentence only)")
    ap.add_argument("--end", help="YYYY-MM-DD (for the headline sentence only)")
    ap.add_argument("--top", type=int, default=10,
                    help="How many largest awards to list (default 10; 0 to omit)")
    ap.add_argument("--csv", help="Also write a machine-readable summary CSV here")
    ap.add_argument("--no-network", action="store_true",
                    help="Don't hit the NSF API; rely on the existing cache only.")
    args = ap.parse_args()

    q5 = os.path.join(args.in_dir, "usage_q5_projects_with_nsf__lump.csv")
    q6 = os.path.join(args.in_dir, "usage_q6_compute_by_project_machine__lump.csv")
    for required in (q5, q6):
        if not os.path.isfile(required):
            sys.exit(f"ERROR: missing required input: {required}")

    university_types = load_university_types(args.maps)
    projects, award_cache, award_titles, coop_ids = load_projects(
        q5, q6, university_types=university_types,
        award_cache_path=os.path.join(args.maps, "nsf_award_lookups.csv"),
        allow_network=not args.no_network)

    plist = list(projects.values())
    tiers = summarize_tiers(plist, award_cache, coop_ids)
    tops = top_awards(plist, award_cache, award_titles, n=args.top)
    render(tiers, tops, args.start, args.end)
    if args.csv:
        write_csv(tiers, args.csv)


if __name__ == "__main__":
    main()
