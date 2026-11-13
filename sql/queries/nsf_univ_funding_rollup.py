#!/usr/bin/env python3
"""Per-year NSF funding profile from nsf_univ_funding_cy.sql output (TSV).

    mysql --batch sam < nsf_univ_funding_cy.sql > rows.tsv
    python3 nsf_univ_funding_rollup.py rows.tsv [--scope univ|all] [--exclude FILE] [--no-network]

An award counts once per year; NCAR's cooperative agreements are excluded; annualized
dollars are api.nsf.gov estimated total / award years (nsf_awards.py, cached in
nsf_award_lookups.csv). --scope all keeps every facility, which reproduces the
October 2026 EC slide; univ (the default) keeps the University facility only.
--exclude drops the awards listed in a CSV (award,reason), e.g. nsf_award_exclusions.csv.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nsf_awards import (nsf_award_id, load_cache, resolve_awards,  # noqa: E402
                        prorated_annual, is_cooperative_agreement)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tsv')
    ap.add_argument('--scope', choices=('univ', 'all'), default='univ')
    ap.add_argument('--no-network', action='store_true')
    ap.add_argument('--cache', default=os.path.join(HERE, 'nsf_award_lookups.csv'))
    ap.add_argument('--exclude', help='CSV of award,reason rows to drop')
    a = ap.parse_args()

    excluded = set()
    if a.exclude:
        with open(a.exclude, newline='', encoding='utf-8') as fh:
            excluded = {nsf_award_id(r['award']) for r in csv.DictReader(fh)} - {None}

    awards_by_year = defaultdict(set)
    with open(a.tsv, newline='', encoding='utf-8') as fh:
        for r in csv.DictReader(fh, delimiter='\t'):
            aid = nsf_award_id(r['contract_number'])
            if not aid or aid in excluded or is_cooperative_agreement(r['contract_title']):
                continue
            if a.scope == 'univ' and r['facility'] != 'UNIV':
                continue
            awards_by_year[int(r['cy'])].add(aid)

    every = set().union(*awards_by_year.values())
    cache = load_cache(a.cache) if a.no_network else resolve_awards(every, a.cache)

    print('year,annualized_nsf_musd,grants,grants_annualized')
    for y in sorted(awards_by_year):
        ids = awards_by_year[y]
        amounts = [v for v in (prorated_annual(cache.get(aid) or {}) for aid in ids) if v is not None]
        print(f'{y},{sum(amounts) / 1e6:.0f},{len(ids)},{len(amounts)}')


if __name__ == '__main__':
    main()
