"""Deterministic input payloads for every chart, used by the fingerprint gate.

One entry per (chart, interesting variant). "Interesting" means a branch the
refactor could plausibly break: the empty short-circuit, a metric that changes
the y-label AND the cache key, the log_y fallback that abandons stacking, the
link_kind gates, and the palette-reversal path.

Everything here is fixed — no ``date.today()``, no randomness — because the
fingerprints are checked in.
"""

from datetime import date, datetime
from decimal import Decimal

from webapp.dashboards import charts

# A fixed 10-day window. Naive datetimes, per the repo convention.
_DAYS = [date(2026, 3, d) for d in range(1, 11)]
_STAMPS = [datetime(2026, 3, 1, h, 0, 0) for h in range(10)]
_PACE_NOW = datetime(2026, 3, 15, 12, 0, 0)


def _usage_daily():
    return {'dates': list(_DAYS),
            'values': [10.0, 0.0, 35.5, 62.0, 41.25, 0.0, 88.0, 12.5, 70.0, 5.0]}


def _usage_stacked():
    return {
        'dates': list(_DAYS),
        'series': [
            {'label': 'Others', 'values': [1.0, 0.0, 2.0, 3.0, 1.5, 0.0, 4.0, 0.5, 2.0, 1.0]},
            {'label': 'alice', 'values': [5.0, 0.0, 20.0, 30.0, 20.0, 0.0, 40.0, 6.0, 35.0, 2.0]},
            {'label': 'bob', 'values': [4.0, 0.0, 13.5, 29.0, 19.75, 0.0, 44.0, 6.0, 33.0, 2.0]},
        ],
    }


def _disk_timeseries():
    # >1 TiB but <1 PiB, so the two-rung byte ladder picks TiB.
    tib = 1024 ** 4
    return {
        'dates': list(_DAYS),
        'series': [
            {'username': 'Others', 'values': [0.4 * tib] * 10},
            {'username': 'alice', 'values': [(2.0 + i * 0.1) * tib for i in range(10)]},
            {'username': 'bob', 'values': [(1.0 + i * 0.05) * tib for i in range(10)]},
        ],
    }


def _user_proj_timeseries():
    """Exercises the deliberately REVERSED palette index (UNITY_STACK_20).

    Three named series so the reversal is visible in the fill order: the
    highest-rank entry must get UNITY_STACK_20[0] (gold), not the lowest.
    """
    return {
        'dates': list(_STAMPS),
        'series': [
            {'label': 'Others', 'values': [2, 2, 3, 3, 4, 4, 3, 3, 2, 2]},
            {'label': 'PROJ0003', 'values': [5, 6, 7, 8, 9, 9, 8, 7, 6, 5]},
            {'label': 'PROJ0002', 'values': [10, 11, 12, 13, 14, 14, 13, 12, 11, 10]},
            {'label': 'PROJ0001', 'values': [20, 22, 24, 26, 28, 28, 26, 24, 22, 20]},
        ],
        'metric_label': 'Jobs',
        'group_by_label': 'project',
    }


def _distribution_hist():
    gib = 1024 ** 3
    def owners(n, base):
        return {f'u{i}': {'data': (base + i) * gib, 'files': (base + i) * 100}
                for i in range(n)}
    labels = ['< 30d', '30-90d', '90-180d', '> 180d']
    return {
        'bucket_labels': labels,
        'buckets': {
            '< 30d':    {'data': 55 * gib, 'files': 5500, 'owners': owners(3, 10)},
            '30-90d':   {'data': 180 * gib, 'files': 18000, 'owners': owners(12, 5)},
            '90-180d':  {'data': 12 * gib, 'files': 1200, 'owners': {}},
            '> 180d':   {'data': 400 * gib, 'files': 40000, 'owners': owners(4, 90)},
        },
        'reference_scan_date': '2026-03-01',
    }


def _nodetype_history():
    return [
        {'timestamp': ts,
         'nodes_available': 100 - i * 3,
         'nodes_down': i,
         'nodes_allocated': 20 + i * 2,
         'utilization_percent': 40.0 + i * 2,
         'memory_utilization_percent': 30.0 + i}
        for i, ts in enumerate(_STAMPS)
    ]


def _queue_history(with_gpus):
    return [
        {'timestamp': ts,
         'running_jobs': 10 + i, 'pending_jobs': 5 + i, 'held_jobs': i,
         'active_users': 3 + (i % 4),
         'cores_allocated': 1000 + i * 10, 'cores_pending': 500 + i * 5,
         'gpus_allocated': (40 + i) if with_gpus else 0,
         'gpus_pending': (10 + i) if with_gpus else 0}
        for i, ts in enumerate(_STAMPS)
    ]


def _facility_data():
    return [{'facility': n, 'annualized_rate': v, 'count': 3, 'percent': 10}
            for n, v in [('UNIV', 5_000_000), ('WNA', 2_500_000), ('NCAR', 1_250_000)]]


def _alloc_type_data():
    # 12 entries so _pie_trim's fixed-cap-10 + "Others (2)" path is exercised.
    return [{'allocation_type': f'Type{i:02d}', 'total_amount': 10_000 * (13 - i),
             'count': 2, 'avg_amount': 5000}
            for i in range(1, 13)]


def _disk_entities():
    # Decimal on purpose: scan rollups arrive as decimal.Decimal from Postgres
    # and must be coerced at the chart boundary, or `cum += v` raises TypeError.
    return [{'id': 1000 + i, 'name': (f'user{i}' if i % 3 else None),
             'value': Decimal(str(10_000_000 * (12 - i)))}
            for i in range(12)]


def _user_usage_rows():
    return [{'username': f'user{i}', 'charges': 1000.0 * (12 - i),
             'jobs': 50 * (12 - i), 'core_hours': 700.0 * (12 - i)}
            for i in range(12)]


def _jobs_hist(with_owners=True):
    def owners(n, base):
        return {f'u{i}': {'job_count': base + i,
                          'cpu_hours': float(10 * (base + i)),
                          'gpu_hours': 0.0,
                          'cpu_charges': float(5 * (base + i)),
                          'gpu_charges': 0.0}
                for i in range(n)}
    labels = ['0-1m', '1-10m', '10m-1h', '1-6h', '> 6h']
    buckets = []
    for i, lbl in enumerate(labels):
        jc = [120, 340, 90, 0, 15][i]
        buckets.append({
            'label': lbl, 'lo': i, 'hi': i + 1,
            'job_count': jc,
            'cpu_hours': float(jc * 3),
            'gpu_hours': float(jc),
            'cpu_charges': float(jc * 2),
            'gpu_charges': 0.0,
            'owners': (owners(4, i + 1) if (with_owners and jc) else {}),
        })
    return {'dimension': 'walltime', 'buckets': buckets,
            'null_count': 7, 'total_count': 565}


def _jobs_timeseries():
    owner_names = ['alice', 'bob', 'carol']
    bands = []
    for i, d in enumerate(_DAYS):
        jc = [10, 0, 25, 40, 30, 0, 55, 12, 44, 8][i]
        bands.append({
            'label': d.isoformat(), 'start': d.isoformat(), 'end': d.isoformat(),
            'job_count': jc,
            'cpu_hours': float(jc * 4), 'gpu_hours': 0.0,
            'cpu_charges': float(jc * 2), 'gpu_charges': 0.0,
            'owners': {n: {'job_count': jc // (j + 3),
                           'cpu_hours': float(jc * 4) / (j + 3),
                           'gpu_hours': 0.0,
                           'cpu_charges': float(jc * 2) / (j + 3),
                           'gpu_charges': 0.0}
                       for j, n in enumerate(owner_names)},
        })
    return {'period': 'day', 'bands': bands, 'total_count': 224,
            'totals': {'job_count': 224, 'cpu_hours': 896.0, 'gpu_hours': 0.0}}


def _jobs_usage(with_unknown=True):
    rows = [{'value': (f'user{i}' if i or not with_unknown else None),
             'job_count': 100 - i * 5,
             'cpu_hours': float(1000 - i * 60),
             'gpu_hours': 0.0,
             'cpu_charges': float(500 - i * 30),
             'gpu_charges': 0.0}
            for i in range(12)]
    return {'rows': rows,
            'totals': {'job_count': 1500, 'cpu_hours': 15000.0, 'gpu_hours': 0.0,
                       'cpu_charges': 7500.0, 'gpu_charges': 0.0}}


def _pace_allocations(n=25):
    """25 projects so the top_n cut, the Other group and the >10 palette
    switch (UNITY_STACK_10 -> UNITY_STACK_20) are all exercised."""
    out = []
    for i in range(n):
        out.append({
            'projcode': f'PROJ{i:04d}',
            'start_date': datetime(2026, 1, 1) if i % 2 else datetime(2025, 11, 15),
            'end_date': datetime(2026, 9, 30) if i % 2 else datetime(2026, 6, 30),
            'total_amount': float(100_000 * (n - i)),
            'total_used': float(40_000 * (n - i)),
        })
    return out


#: ``(case_id, callable, args, kwargs)``. The id is the snapshot key, so it is
#: stable and descriptive; renaming one is a snapshot diff.
CASES = [
    # --- 1. usage timeseries (flat) -------------------------------------
    ('usage_timeseries.charges', charts.generate_usage_timeseries_matplotlib,
     (_usage_daily(),), {'metric': 'charges'}),
    ('usage_timeseries.linked_jobs', charts.generate_usage_timeseries_matplotlib,
     (_usage_daily(),), {'link_to_day_rows': True, 'metric': 'jobs'}),
    ('usage_timeseries.empty', charts.generate_usage_timeseries_matplotlib,
     ({},), {}),

    # --- 2. usage timeseries (stacked by user) --------------------------
    ('usage_stacked.core_hours', charts.generate_usage_timeseries_stacked_by_user,
     (_usage_stacked(),), {'metric': 'core_hours'}),
    ('usage_stacked.empty', charts.generate_usage_timeseries_stacked_by_user,
     ({'dates': [], 'series': []},), {}),

    # --- 3. disk usage stacked area -------------------------------------
    ('disk_area.bytes_linked', charts.generate_disk_usage_stacked_area,
     (_disk_timeseries(),), {'link_kind': 'user'}),
    ('disk_area.files', charts.generate_disk_usage_stacked_area,
     (_disk_timeseries(),), {'metric': 'files'}),
    ('disk_area.empty', charts.generate_disk_usage_stacked_area, ({},), {}),

    # --- 4. user/proj stacked area (REVERSED palette) -------------------
    ('user_proj_area.project_current', charts.generate_user_proj_stacked_area,
     (_user_proj_timeseries(),), {'link_kind': 'project'}),
    ('user_proj_area.peak_unlinked', charts.generate_user_proj_stacked_area,
     (_user_proj_timeseries(),), {'rank_by': 'peak'}),
    ('user_proj_area.empty', charts.generate_user_proj_stacked_area, ({},), {}),

    # --- 5. distribution histogram --------------------------------------
    ('distribution.data', charts.generate_distribution_histogram,
     (_distribution_hist(),), {'metric': 'data'}),
    ('distribution.files', charts.generate_distribution_histogram,
     (_distribution_hist(),), {'metric': 'files'}),
    ('distribution.log_y', charts.generate_distribution_histogram,
     (_distribution_hist(),), {'log_y': True}),
    ('distribution.empty', charts.generate_distribution_histogram, ({},), {}),

    # --- 6. nodetype history (dual panel) -------------------------------
    ('nodetype.normal', charts.generate_nodetype_history_matplotlib,
     (_nodetype_history(),), {}),
    ('nodetype.empty', charts.generate_nodetype_history_matplotlib, ([],), {}),

    # --- 7. queue history (dual panel) ----------------------------------
    ('queue.cores', charts.generate_queue_history_matplotlib,
     (_queue_history(with_gpus=False),), {}),
    ('queue.gpus', charts.generate_queue_history_matplotlib,
     (_queue_history(with_gpus=True),), {}),
    ('queue.empty', charts.generate_queue_history_matplotlib, ([],), {}),

    # --- 8. facility pie -------------------------------------------------
    ('facility_pie.normal', charts.generate_facility_pie_chart_matplotlib,
     (_facility_data(),), {}),
    ('facility_pie.empty', charts.generate_facility_pie_chart_matplotlib, ([],), {}),

    # --- 9. allocation-type pie (exercises _pie_trim "Others (N)") ------
    ('alloc_type_pie.trimmed', charts.generate_allocation_type_pie_chart_matplotlib,
     (_alloc_type_data(),), {}),
    ('alloc_type_pie.empty', charts.generate_allocation_type_pie_chart_matplotlib,
     ([],), {}),

    # --- 10. disk entity pie (Decimal input, cumulative keep) -----------
    ('disk_entity_pie.owner', charts.generate_disk_entity_pie_chart,
     (_disk_entities(), 'owner'), {}),
    ('disk_entity_pie.group', charts.generate_disk_entity_pie_chart,
     (_disk_entities(), 'group'), {}),
    ('disk_entity_pie.empty', charts.generate_disk_entity_pie_chart, ([], 'owner'), {}),

    # --- 11. user usage pie ----------------------------------------------
    ('user_usage_pie.charges', charts.generate_user_usage_pie_chart,
     (_user_usage_rows(),), {'metric': 'charges'}),
    ('user_usage_pie.jobs', charts.generate_user_usage_pie_chart,
     (_user_usage_rows(),), {'metric': 'jobs'}),
    # No metric= on purpose: exercises the defaulted call shape, which used to
    # raise TypeError in the key function. See test_chart_cache_key_signatures.
    ('user_usage_pie.default_metric', charts.generate_user_usage_pie_chart,
     (_user_usage_rows(),), {}),
    ('user_usage_pie.empty', charts.generate_user_usage_pie_chart, ([],), {}),

    # --- 12. jobs histogram ----------------------------------------------
    ('jobs_hist.jobs_owners', charts.generate_jobs_histogram,
     (_jobs_hist(),), {'metric': 'jobs'}),
    ('jobs_hist.charges', charts.generate_jobs_histogram,
     (_jobs_hist(),), {'metric': 'charges'}),
    ('jobs_hist.log_y', charts.generate_jobs_histogram,
     (_jobs_hist(),), {'log_y': True}),
    ('jobs_hist.flat_no_owners', charts.generate_jobs_histogram,
     (_jobs_hist(with_owners=False),), {}),
    ('jobs_hist.empty', charts.generate_jobs_histogram, ({},), {}),

    # --- 13. jobs timeseries stacked -------------------------------------
    ('jobs_ts.user_linked', charts.generate_jobs_timeseries_stacked,
     (_jobs_timeseries(),), {'metric': 'jobs', 'entity_kind': 'user'}),
    ('jobs_ts.project_unlinked', charts.generate_jobs_timeseries_stacked,
     (_jobs_timeseries(),), {'metric': 'cpu_hours', 'entity_kind': 'project',
                             'link_entities': False}),
    ('jobs_ts.empty', charts.generate_jobs_timeseries_stacked, ({},), {}),

    # --- 14. jobs usage pie (both sentinel families) ---------------------
    ('jobs_usage_pie.by_user', charts.generate_jobs_usage_pie_chart,
     (_jobs_usage(),), {'metric': 'cpu_hours'}),
    ('jobs_usage_pie.by_project', charts.generate_jobs_usage_pie_chart,
     (_jobs_usage(with_unknown=False),), {'metric': 'jobs',
                                          'row_attr': 'data-job-project'}),
    ('jobs_usage_pie.empty', charts.generate_jobs_usage_pie_chart, ({},), {}),

    # --- 15. jobs user pie (the delegating facade) -----------------------
    ('jobs_user_pie.delegated', charts.generate_jobs_user_pie_chart,
     (_jobs_usage(),), {'metric': 'cpu_hours'}),

    # --- 16. pace chart ---------------------------------------------------
    ('pace.size', charts.generate_pace_chart_matplotlib,
     (_pace_allocations(), _PACE_NOW), {'sort_by': 'size'}),
    ('pace.future', charts.generate_pace_chart_matplotlib,
     (_pace_allocations(), _PACE_NOW), {'sort_by': 'future'}),
    ('pace.small_top_n', charts.generate_pace_chart_matplotlib,
     (_pace_allocations(6), _PACE_NOW), {'top_n': 8}),
    ('pace.empty', charts.generate_pace_chart_matplotlib, ([], _PACE_NOW), {}),
]
