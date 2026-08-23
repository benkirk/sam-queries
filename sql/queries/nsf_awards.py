#!/usr/bin/env python3
"""
Shared NSF Awards API resolver + on-disk cache.

Resolves an NSF award number -> {division_code, estimated_total_amt,
funds_obligated_amt} via the public NSF Awards API, caching results in a CSV so
repeated runs don't re-hit the API. Used by both build_annual_report.py (division
mapping + funding block) and nsf_grant_funding.py (standalone rollup).

Cache file schema (nsf_award_lookups.csv):
    award_number,division_code,estimated_total_amt,funds_obligated_amt,
    start_date,exp_date

start_date/exp_date (ISO) drive the annualized ($/yr) proration
(estimated_total / award-duration-years).

Backward compatibility: older 2- or 4-column caches are read transparently; rows
missing amounts or dates are treated as stale and re-fetched on the next run.
Leading '#' comment lines are preserved across rewrites.

Only NSF-sourced contracts have a public API. A few very old awards (pre-~2000)
return no record; those resolve to an entry with empty amount fields so callers
can report them as "unresolved" rather than silently dropping them.
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import fcntl
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request


NSF_AWARD_URL = "https://api.nsf.gov/services/v1/awards/{num}.json"

# Request the amount + division + date fields explicitly. Without printFields the
# API returns a default field set that still includes these, but naming them
# keeps the payload small and the contract explicit. start/exp dates drive the
# annualized ($/yr) proration.
NSF_PRINT_FIELDS = "id,divAbbr,estimatedTotalAmt,fundsObligatedAmt,startDate,expDate"

CACHE_COLUMNS = ["award_number", "division_code",
                 "estimated_total_amt", "funds_obligated_amt",
                 "start_date", "exp_date"]


# ----------------------------- award id parsing -----------------------------

def nsf_award_id(contract_number):
    """
    Return the numeric NSF award id used for API lookups, or None.

    NSF award numbers are all-digit (e.g. "2317820", "0830068"). SAM stores
    them two ways:
      * bare numeric        "2317820"      -> "2317820"
      * division-prefixed   "AGS-0830068"  -> "0830068"  (digits after last '-')
    Anything whose final hyphen-segment isn't all digits returns None (e.g. a
    DOE/NASA contract number that happens to live under contract_source='NSF').
    """
    if not contract_number:
        return None
    tail = str(contract_number).strip().rsplit("-", 1)[-1].strip()
    return tail if tail.isdigit() else None


def _num(v):
    """Parse an API amount string ('988935') to float, or None if empty/bad."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso_date(v):
    """Normalize an NSF date ('MM/DD/YYYY') or an ISO date to 'YYYY-MM-DD'/None."""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _duration_years(entry):
    """Award duration in years from start_date/exp_date, or None if unusable."""
    s, e = entry.get("start_date"), entry.get("exp_date")
    if not s or not e:
        return None
    try:
        d0 = datetime.date.fromisoformat(s)
        d1 = datetime.date.fromisoformat(e)
    except (ValueError, TypeError):
        return None
    days = (d1 - d0).days
    return days / 365.25 if days > 0 else None


def prorated_annual(entry):
    """
    Annualized award value: estimated_total_amt / duration_years, or None if the
    estimate or dates are missing. Sums to the annual run-rate of NSF research
    funding the active grants represent.
    """
    yrs = _duration_years(entry)
    est = entry.get("estimated_total_amt")
    if yrs and est is not None:
        return est / yrs
    return None


# ------------------------------- cache i/o ----------------------------------

def load_cache(path):
    """
    award_number (str) -> {division_code, estimated_total_amt (float|None),
                           funds_obligated_amt (float|None)}.

    Missing file -> empty dict. Rows from an older 2-column cache load with
    None amounts (callers re-resolve them).
    """
    cache = {}
    if not os.path.isfile(path):
        return cache
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return cache
    reader = csv.DictReader(lines)
    for row in reader:
        a = (row.get("award_number") or "").strip()
        if not a:
            continue
        cache[a] = {
            "division_code": (row.get("division_code") or "").strip(),
            "estimated_total_amt": _num(row.get("estimated_total_amt")),
            "funds_obligated_amt": _num(row.get("funds_obligated_amt")),
            "start_date": _iso_date(row.get("start_date")),
            "exp_date": _iso_date(row.get("exp_date")),
        }
    return cache


def _has_amount(entry):
    return entry.get("estimated_total_amt") is not None or \
        entry.get("funds_obligated_amt") is not None


def _has_dates(entry):
    return bool(entry.get("start_date")) and bool(entry.get("exp_date"))


def _needs_fetch(entry):
    """
    True if this cache entry should be (re)fetched from the API.

      * None                    -> not cached yet                  -> fetch
      * division/amount but no
        amount+dates            -> stale (pre-amount or pre-date)  -> fetch
      * empty div, no amount    -> already-fetched "no NSF record" -> keep
      * amount AND dates present -> fully resolved                 -> keep

    Migrates older caches forward: a resolved award (has a division) that lacks
    amounts or start/exp dates predates those columns and is re-fetched. An
    empty-division/no-amount row is a settled "no public record" and is kept as
    is (so those don't re-hit the API every run).
    """
    if entry is None:
        return True
    if not _is_resolved(entry):
        return False   # settled "no NSF record"
    return not (_has_amount(entry) and _has_dates(entry))


def _is_resolved(entry):
    """Whether we managed to pull anything useful (division or amount)."""
    return bool(entry.get("division_code")) or _has_amount(entry)


def _read_header(path):
    """Return the leading '#' comment lines of the cache file (no newlines)."""
    header = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                if ln.lstrip().startswith("#"):
                    header.append(ln.rstrip("\n"))
                else:
                    break
    return header


def _write_rows(fh, header, cache):
    for ln in header:
        fh.write(ln + "\n")
    w = csv.writer(fh)
    w.writerow(CACHE_COLUMNS)
    for k in sorted(cache):
        e = cache[k]
        w.writerow([
            k,
            e.get("division_code") or "",
            "" if e.get("estimated_total_amt") is None else int(e["estimated_total_amt"]),
            "" if e.get("funds_obligated_amt") is None else int(e["funds_obligated_amt"]),
            e.get("start_date") or "",
            e.get("exp_date") or "",
        ])


@contextlib.contextmanager
def _cache_lock(path):
    """Advisory exclusive lock on a sidecar file — serializes concurrent
    resolver saves so two runs can't clobber each other's cache writes."""
    lock_path = path + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def save_cache(path, cache):
    """
    Atomically rewrite the cache file (temp file + os.replace), preserving the
    leading '#' comment header. Atomic replace means a concurrent reader never
    sees a partially-written file. Use save_cache_merge() for concurrency-safe
    updates that must not lose another process's entries.
    """
    header = _read_header(path)
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nsf_cache_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            _write_rows(fh, header, cache)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def save_cache_merge(path, entries):
    """
    Concurrency-safe cache update: under an exclusive lock, re-read the current
    on-disk cache, merge `entries` into it (our values win), and atomically
    rewrite. This preserves entries written by a concurrent resolver between our
    initial load and this save — the failure mode that silently drops rows when
    two runs write the file at once. Returns the merged cache.
    """
    with _cache_lock(path):
        merged = load_cache(path)
        merged.update(entries)
        save_cache(path, merged)
    return merged


# ------------------------------- API fetch ----------------------------------

def _fetch_nsf_award(award_number, timeout=10):
    """
    Hit the NSF awards API for one award. Return
    {division_code, estimated_total_amt, funds_obligated_amt}. On any error or
    missing record, return an entry with empty division + None amounts.
    """
    empty = {"division_code": "", "estimated_total_amt": None,
             "funds_obligated_amt": None, "start_date": None, "exp_date": None}
    url = NSF_AWARD_URL.format(num=award_number) + "?printFields=" + NSF_PRINT_FIELDS
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            payload = json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, json.JSONDecodeError) as e:
        print(f"    NSF API error for {award_number}: {e}", file=sys.stderr)
        return empty
    try:
        awards = payload["response"]["award"]
    except (KeyError, TypeError):
        return empty
    if not awards:
        return empty
    a = awards[0]
    return {
        "division_code": (a.get("divAbbr") or "").strip(),
        "estimated_total_amt": _num(a.get("estimatedTotalAmt")),
        "funds_obligated_amt": _num(a.get("fundsObligatedAmt")),
        "start_date": _iso_date(a.get("startDate")),
        "exp_date": _iso_date(a.get("expDate")),
    }


def resolve_awards(award_numbers, cache_path, sleep_between=0.3):
    """
    Ensure every award number in `award_numbers` has a cache entry, fetching
    (and persisting) any that are missing. Returns the merged cache dict.

    Amount-aware, and resolves ALL requested awards (old-style included), not
    just numeric ones absent from the directorate map.
    """
    cache = load_cache(cache_path)
    todo = sorted({str(a).strip() for a in award_numbers
                   if a and str(a).strip() and _needs_fetch(cache.get(str(a).strip()))})
    if not todo:
        return cache
    print(f"  Resolving {len(todo)} NSF award number(s) via api.nsf.gov ...",
          file=sys.stderr)
    fetched = {}
    for a in todo:
        entry = _fetch_nsf_award(a)
        fetched[a] = entry
        if _is_resolved(entry):
            est = entry["estimated_total_amt"]
            print(f"    {a} -> {entry['division_code'] or '(no div)'} "
                  f"est={'' if est is None else int(est)}", file=sys.stderr)
        else:
            print(f"    {a} -> (no NSF record)", file=sys.stderr)
        time.sleep(sleep_between)
    if fetched:
        # Merge under lock against the latest on-disk cache so a concurrent
        # resolver run (e.g. a second date range) can't clobber our writes.
        cache = save_cache_merge(cache_path, fetched)
        print(f"  Cached {len(fetched)} new lookup(s) in {cache_path}", file=sys.stderr)
    return cache


# --------------------------- funding aggregation ----------------------------

def project_resources(p):
    """
    Resource-bucket labels a project touched (core-hours > 0), given the
    per-project fields build_annual_report / nsf_grant_funding populate:
    `derecho_ch`, `casper_ch`, and `other_ch` (machine -> core-hours).

    Derecho/Casper mirror the combiner's per-system pivot; other machines pass
    through title-cased. Used to attribute grants to Derecho vs Casper vs other.
    """
    labels = []
    if p.get("derecho_ch", 0):
        labels.append("Derecho")
    if p.get("casper_ch", 0):
        labels.append("Casper")
    for machine, ch in (p.get("other_ch") or {}).items():
        if ch:
            labels.append(machine.title())
    return labels


# Titles of NCAR's own NSF Cooperative Agreement(s) — the award(s) that fund the
# operation of NCAR/Derecho itself. Summed into a grant-funding headline they are
# circular ("we supported the award that funds us") and dominate the total, so
# the excl-cooperative / university tiers drop them. Matched case-insensitively
# as a substring of the contract title.
COOPERATIVE_AGREEMENT_TITLE_PATTERNS = (
    "management and operation of the national center",
)

# allocation_type_buckets.csv sections that denote university projects.
UNIVERSITY_SECTIONS = frozenset({"non_nsf_univ", "nsf_univ"})


def is_cooperative_agreement(title):
    """True if a contract title looks like NCAR's core NSF cooperative agreement."""
    t = (title or "").strip().lower()
    return any(pat in t for pat in COOPERATIVE_AGREEMENT_TITLE_PATTERNS)


def load_university_types(maps_dir):
    """
    Set of allocation_type values that map to a university section in
    allocation_type_buckets.csv. Empty set if the file is absent/unreadable
    (callers then skip the university tier).
    """
    path = os.path.join(maps_dir, "allocation_type_buckets.csv")
    types = set()
    if not os.path.isfile(path):
        return types
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        at = (row.get("allocation_type") or "").strip()
        if at and (row.get("section") or "").strip() in UNIVERSITY_SECTIONS:
            types.add(at)
    return types


def _sum_awards(award_ids, award_cache):
    """Aggregate stats over a set of award ids: grant count, count with a dollar
    amount, summed estimated-total / obligated / annualized ($/yr), and the count
    that could be annualized (had usable start/exp dates)."""
    grants = len(award_ids)
    resolved = 0
    est_total = 0.0
    obligated = 0.0
    annual = 0.0
    annualized = 0
    for aid in award_ids:
        entry = award_cache.get(aid)
        if not entry:
            continue
        est = entry.get("estimated_total_amt")
        obl = entry.get("funds_obligated_amt")
        if est is not None or obl is not None:
            resolved += 1
            est_total += est or 0.0
            obligated += obl or 0.0
        pa = prorated_annual(entry)
        if pa is not None:
            annual += pa
            annualized += 1
    return {"grants": grants, "resolved": resolved, "est_total": est_total,
            "obligated": obligated, "annual": annual, "annualized": annualized}


def summarize_funding(projects, award_cache):
    """
    Aggregate NSF grant funding across projects.

    `projects` is an iterable of per-project dicts each carrying `nsf_award_ids`
    (set of numeric award ids) plus the resource core-hour fields consumed by
    project_resources(). Grants are deduped by award id, so a grant funding
    several projects is counted once overall and once per resource it touched
    (a grant spanning Derecho + Casper counts under each — intentional).

    Returns:
        {
          "overall":      {grants, resolved, est_total, obligated},
          "by_resource":  {label: {grants, resolved, est_total, obligated}},
          "unresolved_ids": set(award ids with no usable amount),
        }
    """
    all_ids = set()
    per_resource_ids = {}
    for p in projects:
        aids = p.get("nsf_award_ids") or set()
        if not aids:
            continue
        all_ids |= aids
        for label in project_resources(p):
            per_resource_ids.setdefault(label, set()).update(aids)

    def _resolved(aid):
        e = award_cache.get(aid)
        return bool(e) and (e.get("estimated_total_amt") is not None
                            or e.get("funds_obligated_amt") is not None)

    def _suspect(aid):
        e = award_cache.get(aid) or {}
        est, obl = e.get("estimated_total_amt"), e.get("funds_obligated_amt")
        return est is not None and obl is not None and obl > est

    return {
        "overall": _sum_awards(all_ids, award_cache),
        "by_resource": {label: _sum_awards(ids, award_cache)
                        for label, ids in per_resource_ids.items()},
        "unresolved_ids": {aid for aid in all_ids if not _resolved(aid)},
        "suspect_obligated_ids": {aid for aid in all_ids if _suspect(aid)},
    }


def _filtered(projects, drop_award_ids=frozenset(), university_only=False):
    """Shallow project copies with cooperative-agreement (and, optionally,
    non-university) award ids removed — for tiered summaries."""
    out = []
    for p in projects:
        if university_only and not p.get("is_university"):
            continue
        out.append({**p, "nsf_award_ids": (p.get("nsf_award_ids") or set()) - drop_award_ids})
    return out


def summarize_tiers(projects, award_cache, coop_ids=frozenset()):
    """
    Three scoping tiers of summarize_funding():
      * all         — every NSF grant
      * excl_coop   — drop NCAR core cooperative agreements
      * university  — university-class projects only, coop agreements dropped
    `projects` must be a re-iterable sequence (list), since it is scanned 3x.
    """
    coop_ids = frozenset(coop_ids)
    return {
        "all": summarize_funding(projects, award_cache),
        "excl_coop": summarize_funding(_filtered(projects, coop_ids), award_cache),
        "university": summarize_funding(
            _filtered(projects, coop_ids, university_only=True), award_cache),
    }


def top_awards(projects, award_cache, award_titles, n=10, drop_ids=frozenset()):
    """
    Largest awards by estimated total across `projects`, as
    [(award_id, title, est_total, obligated)]. `drop_ids` are excluded (e.g.
    cooperative agreements). Unresolved awards are skipped.
    """
    ids = set()
    for p in projects:
        ids |= (p.get("nsf_award_ids") or set())
    ids -= set(drop_ids)
    ranked = []
    for aid in ids:
        e = award_cache.get(aid)
        if not e:
            continue
        est = e.get("estimated_total_amt")
        obl = e.get("funds_obligated_amt")
        if est is None and obl is None:
            continue
        ranked.append((aid, award_titles.get(aid, ""), est or 0.0, obl or 0.0))
    ranked.sort(key=lambda r: r[2], reverse=True)
    return ranked[:n]
