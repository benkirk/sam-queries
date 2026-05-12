#!/usr/bin/env python3
"""
Assemble the annual NCAR/NSF usage report CSV from the per-project CSVs
emitted by usage_q5/q6/q7 and the two mapping files
(nsf_directorate_map.csv, allocation_type_buckets.csv).

The output mirrors the layout of sample_annual_usae_report.csv:

    Section 1  Non-NSF Sponsored University Projects   (Sponsor)
    Section 2  NSF-Sponsored University Projects       (Directorate, Division)
    Section 3  NCAR Strategic Capability/ASD/Div/etc   (Type, Lab)
    Section 4  CSL & WNA Projects                      (Facility)

Usage:
    python3 build_annual_report.py \\
        --in-dir   path/to/usage_reports_<ts>/ \\
        --start    2024-10-01  --end 2025-08-14 \\
        --out      annual_report.csv \\
        --maps     sql/queries/

Input CSVs expected in --in-dir:
    usage_q5_projects_with_nsf__lump.csv
    usage_q6_compute_by_project_machine__lump.csv
    usage_q7_disk_by_project_resource__lump.csv   (optional but recommended)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date


# ----------------------------- helpers --------------------------------------

def _open_csv(path):
    if not os.path.isfile(path):
        return None
    return open(path, newline="", encoding="utf-8")


def _f(v, default=0.0):
    """Parse a number that may be empty / NULL / quoted."""
    if v is None:
        return default
    s = str(v).strip().strip('"')
    if not s or s.upper() == "NULL":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _i(v, default=0):
    return int(_f(v, default))


def _read_mapping(path, required_keys, label):
    """Read a 2- or 3-column mapping CSV. Lines starting with '#' are ignored."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        # strip comment lines before passing to csv.reader
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    missing = [k for k in required_keys if k not in (reader.fieldnames or [])]
    if missing:
        sys.exit(f"ERROR: {path} missing required column(s): {missing}")
    for row in reader:
        key = row[required_keys[0]].strip()
        if not key:
            continue
        out[key] = {k: (row.get(k) or "").strip() for k in required_keys[1:]}
    if not out:
        sys.exit(f"ERROR: {path} contains no usable rows. {label}")
    return out


def _facility_set(facility_names_field):
    """Parse the pipe-separated facility list emitted by Q5."""
    if not facility_names_field:
        return set()
    return {f.strip() for f in facility_names_field.split("|") if f.strip()}


# ----------------------- NSF award API resolver -----------------------------

NSF_AWARD_URL = "https://api.nsf.gov/services/v1/awards/{num}.json"


def _load_award_lookup_cache(path):
    """award_number (str) -> division_code (str). Missing file -> empty dict."""
    cache = {}
    if not os.path.isfile(path):
        return cache
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    for row in reader:
        a = (row.get("award_number") or "").strip()
        d = (row.get("division_code") or "").strip()
        if a and d:
            cache[a] = d
    return cache


def _save_award_lookup_cache(path, cache):
    """Rewrite the cache file, preserving leading comment lines if present."""
    header = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                if ln.lstrip().startswith("#"):
                    header.append(ln.rstrip("\n"))
                else:
                    break
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for ln in header:
            fh.write(ln + "\n")
        w = csv.writer(fh)
        w.writerow(["award_number", "division_code"])
        for k in sorted(cache):
            w.writerow([k, cache[k]])


def _fetch_nsf_division(award_number, timeout=10):
    """Hit the NSF awards API for one award. Return divAbbr (or '' if missing)."""
    url = NSF_AWARD_URL.format(num=award_number)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            payload = json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        print(f"    NSF API error for {award_number}: {e}", file=sys.stderr)
        return ""
    try:
        awards = payload["response"]["award"]
    except (KeyError, TypeError):
        return ""
    if not awards:
        return ""
    return (awards[0].get("divAbbr") or "").strip()


def resolve_unknown_awards(award_numbers, cache_path, sleep_between=0.3):
    """
    Fill in division_code for any award_numbers not yet in the cache file.
    Returns the merged cache. Writes any newly-resolved entries back to disk.
    """
    cache = _load_award_lookup_cache(cache_path)
    todo = sorted({a for a in award_numbers if a and a not in cache})
    if not todo:
        return cache
    print(f"  Resolving {len(todo)} NSF award number(s) via api.nsf.gov ...", file=sys.stderr)
    new_count = 0
    for a in todo:
        div = _fetch_nsf_division(a)
        if div:
            cache[a] = div
            new_count += 1
            print(f"    {a} -> {div}", file=sys.stderr)
        time.sleep(sleep_between)
    if new_count:
        _save_award_lookup_cache(cache_path, cache)
        print(f"  Cached {new_count} new lookup(s) in {cache_path}", file=sys.stderr)
    return cache


# --------------------------- core algorithm ---------------------------------

def load_projects(q5_path, q6_path, q7_path,
                  nsf_map, bucket_map, campaign_resource_name,
                  award_cache_path=None, allow_network=True):
    """
    Return projects = { projcode: {
        title, facilities, allocation_type, lead_org_acronym,
        nsf_division_codes (set), nsf_directorates (set),
        derecho_ch, casper_ch, campaign_tby,
    } }
    Also return unmapped sets so the caller can abort if non-empty.
    """
    projects = {}
    unmapped_alloc_types = set()
    unmapped_divisions = set()

    # First pass: collect every numeric-looking NSF division_code that isn't
    # already in nsf_directorate_map.csv. These are bare NSF award numbers
    # (e.g. "2317820") from post-~2020 awards that no longer carry a
    # division prefix. We'll batch-resolve them via the NSF API once.
    numeric_codes_to_resolve = set()
    with open(q5_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            src = (row.get("contract_source") or "").strip().upper()
            div = (row.get("nsf_division_code") or "").strip().upper()
            if src == "NSF" and div and div.isdigit() and div not in nsf_map:
                numeric_codes_to_resolve.add(div)

    award_cache = {}
    if numeric_codes_to_resolve and allow_network and award_cache_path:
        award_cache = resolve_unknown_awards(numeric_codes_to_resolve, award_cache_path)
    elif award_cache_path:
        award_cache = _load_award_lookup_cache(award_cache_path)

    # --- Q5: project metadata + NSF contracts ---
    with open(q5_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pc = (row["projcode"] or "").strip()
            if not pc:
                continue
            p = projects.setdefault(pc, {
                "title": (row.get("project_title") or "").strip(),
                "facilities": set(),
                "allocation_type": (row.get("allocation_type") or "").strip(),
                "lead_org_acronym": (row.get("lead_org_acronym") or "").strip(),
                "lead_org_name": (row.get("lead_org_name") or "").strip(),
                "lab_acronym": (row.get("lab_acronym") or row.get("lead_org_acronym") or "").strip(),
                "nsf_division_codes": set(),
                "nsf_directorates": set(),
                "_div_to_dir": {},   # per-project: div -> directorate
                "derecho_ch": 0.0,
                "casper_ch": 0.0,
                "other_ch": defaultdict(float),
                "campaign_tby": 0.0,
            })
            p["facilities"].update(_facility_set(row.get("facility_names")))
            src = (row.get("contract_source") or "").strip().upper()
            div = (row.get("nsf_division_code") or "").strip().upper()
            if src == "NSF" and div:
                # If div is a bare numeric award number, try the cached
                # NSF API lookup to translate it into a real division code.
                if div.isdigit() and div in award_cache:
                    div = award_cache[div]
                p["nsf_division_codes"].add(div)
                if div in nsf_map:
                    directorate = nsf_map[div]["directorate"]
                    p["nsf_directorates"].add(directorate)
                    p["_div_to_dir"][div] = directorate
                else:
                    unmapped_divisions.add(div)

    # --- Q6: compute totals, pivoted by machine ---
    with open(q6_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pc = (row["projcode"] or "").strip()
            if pc not in projects:
                continue
            machine = (row.get("machine") or "").strip().lower()
            ch = _f(row.get("total_core_hours"))
            # Treat derecho-gpu / casper-gpu as part of Derecho / Casper —
            # the sample report's columns are per-system, not per-partition.
            if machine.startswith("derecho"):
                projects[pc]["derecho_ch"] += ch
            elif machine.startswith("casper"):
                projects[pc]["casper_ch"] += ch
            elif machine:
                projects[pc]["other_ch"][machine] += ch

    # --- Q7: disk totals, pick Campaign Store ---
    if q7_path and os.path.isfile(q7_path):
        with open(q7_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                pc = (row["projcode"] or "").strip()
                if pc not in projects:
                    continue
                rn = (row.get("resource_name") or "").strip()
                if rn.lower() == campaign_resource_name.lower():
                    projects[pc]["campaign_tby"] += _f(row.get("total_terabyte_years"))

    # --- check allocation_type coverage ---
    for pc, p in projects.items():
        at = p["allocation_type"]
        if at not in bucket_map:
            unmapped_alloc_types.add(at)

    return projects, unmapped_alloc_types, unmapped_divisions


def _projcode_facility_hint(projcode):
    """
    Map a projcode's first character to its likely facility. More reliable
    than facility_resource (which can be many-to-many). Used to disambiguate
    UNIV-class allocation_types ("Small") that may actually live on WNA or
    CSL.
    """
    if not projcode:
        return None
    c = projcode[0].upper()
    if c == "U":
        return "UNIV"
    if c == "N":
        return "NCAR"
    if c == "W":
        return "WNA"
    if c == "C":
        return "CSL"
    if c == "A":
        return "NCAR"   # ASD-NCAR projects start with A
    if c == "S":
        return "NCAR"   # staff / CISL admin
    return None


def classify_section(p, bucket_map, projcode=""):
    """
    Return (section, bucket_label) for a project.

    Primary classifier is the bucket file's `section`. UNIV-class
    allocation_types are disambiguated by NSF-contract presence
    (non_nsf_univ vs nsf_univ). A final override uses projcode prefix
    to redirect "Small"-style allocations that happen to live on WNA or
    CSL into the right section (the sample report does this implicitly).
    """
    at = p["allocation_type"]
    info = bucket_map.get(at, {"bucket": at, "section": ""})
    declared = info["section"]
    bucket = info["bucket"]

    if declared in ("ncar_strategic", "csl_wna"):
        return declared, bucket

    if declared in ("non_nsf_univ", "nsf_univ"):
        # Projcode-prefix override: a Small-allocation_type project on a
        # WNA-prefixed projcode belongs in the CSL/WNA section, not Univ.
        hint = _projcode_facility_hint(projcode)
        if hint == "WNA":
            return "csl_wna", "WNA"
        if hint == "CSL":
            return "csl_wna", "CSL"

        if p["nsf_directorates"]:
            return "nsf_univ", bucket
        # No NSF contract: force into Non-NSF Univ. If the original
        # bucket was the NSF-sponsored placeholder, relabel as Exploratory.
        if declared == "nsf_univ":
            return "non_nsf_univ", "Exploratory (No NSF Award)"
        return "non_nsf_univ", bucket

    if declared == "skip":
        return "skip", bucket

    return "unmapped", bucket


# --------------------------- output emission --------------------------------

def _fmt_num(x):
    if x is None or x == 0:
        return ""
    return f"{x:,.2f}"


def _fmt_int(x):
    if x is None:
        return ""
    return f"{x}"


def emit_report(projects, bucket_map, out_path, start, end):
    """Write the section-banner-style CSV mirroring sample_annual_usae_report.csv."""

    # bucket each project
    sections = defaultdict(list)   # section_key -> list of (projcode, p, bucket_label)
    for pc, p in projects.items():
        section, label = classify_section(p, bucket_map, projcode=pc)
        sections[section].append((pc, p, label))

    rows = []

    def push(*cols):
        rows.append(list(cols))

    def blank():
        rows.append([""] * 6)

    def section_subtotal(label_left, label_right, items):
        push(label_left, label_right,
             _fmt_int(len(items)),
             _fmt_num(sum(p["derecho_ch"] for _, p, _ in items)),
             _fmt_num(sum(p["casper_ch"] for _, p, _ in items)),
             _fmt_num(sum(p["campaign_tby"] for _, p, _ in items)))

    # ----- Section 1: Non-NSF Sponsored University -----
    push("", "Non-NSF Sponsored University Projects", "", "", "", "")
    blank()
    push(f"Data from {start.strftime('%-m/%-d/%Y')} through {end.strftime('%-m/%-d/%Y')}",
         "", "", "", "", "")
    push("", "Sponsor", "Projects", "Derecho Core-Hours", "Casper Core-Hours",
         "Campaign TB-yrs")
    by_bucket = defaultdict(list)
    for pc, p, label in sections.get("non_nsf_univ", []):
        by_bucket[label or "(uncategorized)"].append((pc, p, label))
    for bucket_label in sorted(by_bucket):
        items = by_bucket[bucket_label]
        push("", bucket_label,
             _fmt_int(len(items)),
             _fmt_num(sum(p["derecho_ch"] for _, p, _ in items)),
             _fmt_num(sum(p["casper_ch"] for _, p, _ in items)),
             _fmt_num(sum(p["campaign_tby"] for _, p, _ in items)))
    blank()
    section_subtotal("", "TOTAL", sections.get("non_nsf_univ", []))
    blank(); blank()

    # ----- Section 2: NSF-Sponsored University -----
    push("", "NSF-Sponsored University Projects", "", "", "", "")
    blank()
    push("Directorate", "NSF Division", "Projects",
         "Derecho Core-Hours", "Casper Core-Hours", "Campaign TB-yrs")
    # Group by directorate -> division -> projects.
    # A project may have multiple NSF contracts in different divisions; the
    # whole project is attributed to each (division, directorate) pair, which
    # is how the sample report appears to do it.
    dir_to_div = defaultdict(lambda: defaultdict(list))
    for pc, p, _ in sections.get("nsf_univ", []):
        pairs = {(div, p["_div_to_dir"][div])
                 for div in p["nsf_division_codes"]
                 if div in p["_div_to_dir"]}
        for div, directorate in pairs:
            dir_to_div[directorate][div].append((pc, p))
    nsf_grand_projs = 0
    nsf_grand_d, nsf_grand_c, nsf_grand_cs = 0.0, 0.0, 0.0
    for directorate in sorted(dir_to_div):
        first = True
        sub_projects = set()
        sub_d, sub_c, sub_cs = 0.0, 0.0, 0.0
        for div in sorted(dir_to_div[directorate]):
            items = dir_to_div[directorate][div]
            push(directorate if first else "", div,
                 _fmt_int(len(items)),
                 _fmt_num(sum(p["derecho_ch"] for _, p in items)),
                 _fmt_num(sum(p["casper_ch"] for _, p in items)),
                 _fmt_num(sum(p["campaign_tby"] for _, p in items)))
            first = False
            for pc, _p in items:
                sub_projects.add(pc)
            sub_d += sum(p["derecho_ch"] for _, p in items)
            sub_c += sum(p["casper_ch"] for _, p in items)
            sub_cs += sum(p["campaign_tby"] for _, p in items)
        push("", "Subtotal", _fmt_int(len(sub_projects)),
             _fmt_num(sub_d), _fmt_num(sub_c), _fmt_num(sub_cs))
        blank()
        nsf_grand_projs += len(sub_projects)
        nsf_grand_d += sub_d
        nsf_grand_c += sub_c
        nsf_grand_cs += sub_cs
    push("", "TOTAL", _fmt_int(nsf_grand_projs),
         _fmt_num(nsf_grand_d), _fmt_num(nsf_grand_c), _fmt_num(nsf_grand_cs))
    blank(); blank()

    # ----- Section 3: NCAR Strategic / DIRRS -----
    push("", "NCAR Strategic Capability/ASD, Division, and Reserve Projects",
         "", "", "", "")
    blank()
    push("Type", "Lab/Division", "Projects",
         "Derecho Core-Hours", "Casper Core-Hours", "Campaign TB-yrs")
    type_to_lab = defaultdict(lambda: defaultdict(list))
    for pc, p, label in sections.get("ncar_strategic", []):
        type_to_lab[label or "(uncategorized)"][p["lab_acronym"] or p["lead_org_acronym"] or "(unknown)"].append((pc, p))
    ncar_grand = 0
    ncar_d, ncar_c, ncar_cs = 0.0, 0.0, 0.0
    for tlabel in sorted(type_to_lab):
        first = True
        sub_d, sub_c, sub_cs = 0.0, 0.0, 0.0
        sub_n = 0
        for lab in sorted(type_to_lab[tlabel]):
            items = type_to_lab[tlabel][lab]
            push(tlabel if first else "", lab,
                 _fmt_int(len(items)),
                 _fmt_num(sum(p["derecho_ch"] for _, p in items)),
                 _fmt_num(sum(p["casper_ch"] for _, p in items)),
                 _fmt_num(sum(p["campaign_tby"] for _, p in items)))
            first = False
            sub_n += len(items)
            sub_d += sum(p["derecho_ch"] for _, p in items)
            sub_c += sum(p["casper_ch"] for _, p in items)
            sub_cs += sum(p["campaign_tby"] for _, p in items)
        push("", "Subtotal", _fmt_int(sub_n),
             _fmt_num(sub_d), _fmt_num(sub_c), _fmt_num(sub_cs))
        blank()
        ncar_grand += sub_n
        ncar_d += sub_d; ncar_c += sub_c; ncar_cs += sub_cs
    push("", "NCAR TOTAL", _fmt_int(ncar_grand),
         _fmt_num(ncar_d), _fmt_num(ncar_c), _fmt_num(ncar_cs))
    blank(); blank()

    # ----- Section 4: CSL + WNA -----
    push("", "CSL and Wyoming-NCAR Allocation (WNA) Projects", "", "", "", "")
    blank()
    push("", "", "Projects", "Derecho Core-Hours", "Casper Core-Hours",
         "Campaign TB-yrs")
    csl_wna_items = sections.get("csl_wna", [])
    by_fac = defaultdict(list)
    for pc, p, label in csl_wna_items:
        # use the label assigned in classify_section (CSL / WNA)
        by_fac[label].append((pc, p))
    for fac in sorted(by_fac):
        items = by_fac[fac]
        push("", fac, _fmt_int(len(items)),
             _fmt_num(sum(p["derecho_ch"] for _, p in items)),
             _fmt_num(sum(p["casper_ch"] for _, p in items)),
             _fmt_num(sum(p["campaign_tby"] for _, p in items)))
    blank()
    push("", "CSL & WNA TOTAL", _fmt_int(len(csl_wna_items)),
         _fmt_num(sum(p["derecho_ch"] for _, p, _ in csl_wna_items)),
         _fmt_num(sum(p["casper_ch"] for _, p, _ in csl_wna_items)),
         _fmt_num(sum(p["campaign_tby"] for _, p, _ in csl_wna_items)))

    # ----- diagnostic: unmapped projects -----
    if sections.get("unmapped"):
        blank(); blank()
        push("", "UNMAPPED PROJECTS (please review allocation_type_buckets.csv)",
             "", "", "", "")
        push("projcode", "allocation_type / facility", "Projects",
             "Derecho Core-Hours", "Casper Core-Hours", "Campaign TB-yrs")
        for pc, p, _ in sorted(sections["unmapped"]):
            push(pc,
                 f"{p['allocation_type']} | {','.join(sorted(p['facilities']))}",
                 "1",
                 _fmt_num(p["derecho_ch"]),
                 _fmt_num(p["casper_ch"]),
                 _fmt_num(p["campaign_tby"]))

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerows(rows)
    print(f"wrote {out_path} ({len(rows)} rows)")


# ------------------------------- main ---------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", required=True, help="Directory holding Q5/Q6/Q7 CSVs")
    ap.add_argument("--out", required=True, help="Output annual report CSV")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--maps", default=os.path.dirname(os.path.abspath(__file__)),
                    help="Directory holding nsf_directorate_map.csv and allocation_type_buckets.csv")
    ap.add_argument("--campaign-resource", default="Campaign_Store",
                    help='resources.resource_name to treat as Campaign TB-yrs (default: "Campaign_Store")')
    ap.add_argument("--no-network", action="store_true",
                    help="Don't hit the NSF API; rely on existing nsf_award_lookups.csv cache only.")
    args = ap.parse_args()

    nsf_map = _read_mapping(
        os.path.join(args.maps, "nsf_directorate_map.csv"),
        ["division_code", "directorate"],
        "(needs at least one division_code,directorate row)")
    bucket_map = _read_mapping(
        os.path.join(args.maps, "allocation_type_buckets.csv"),
        ["allocation_type", "bucket", "section"],
        "Populate this from the output of usage_q0_allocation_types.sql.")

    q5 = os.path.join(args.in_dir, "usage_q5_projects_with_nsf__lump.csv")
    q6 = os.path.join(args.in_dir, "usage_q6_compute_by_project_machine__lump.csv")
    q7 = os.path.join(args.in_dir, "usage_q7_disk_by_project_resource__lump.csv")
    for required in (q5, q6):
        if not os.path.isfile(required):
            sys.exit(f"ERROR: missing required input: {required}")
    if not os.path.isfile(q7):
        print(f"WARNING: {q7} not found; Campaign TB-yrs column will be 0", file=sys.stderr)
        q7 = None

    projects, unmapped_alloc, unmapped_div = load_projects(
        q5, q6, q7, nsf_map, bucket_map, args.campaign_resource,
        award_cache_path=os.path.join(args.maps, "nsf_award_lookups.csv"),
        allow_network=not args.no_network)

    errors = []
    if unmapped_alloc:
        errors.append("Unmapped allocation_type values (add to allocation_type_buckets.csv):")
        for v in sorted(unmapped_alloc):
            errors.append(f"    {v!r}")
    if unmapped_div:
        errors.append("Unmapped NSF division codes (add to nsf_directorate_map.csv):")
        for v in sorted(unmapped_div):
            errors.append(f"    {v!r}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit(1)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    emit_report(projects, bucket_map, args.out, start, end)


if __name__ == "__main__":
    main()
