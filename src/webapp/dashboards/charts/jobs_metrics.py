"""Metric accessors for the hpc-usage-queries plugin envelopes.

Inputs are the plugin envelopes verbatim (see ``webapp.jobs.service``) — the
histogram envelope is self-describing (dimension, unit, full zero-filled bucket
vector, null_count), so the renderers never hardcode bucket tables.

Lives in its own module because three chart modules need it. It was previously
in the package facade, which meant `histogram.py`, `stacked.py` and `pie.py`
each imported it *lazily inside a method* to dodge the circular import — a
workaround for a layering mistake rather than a real constraint.

**No matplotlib import here**, same as `links.py` and `series.py`, and enforced
by the same test: this is envelope arithmetic, not rendering.
"""

# UI metric name → the plugin key(s) SUMMED to produce it. 'jobs' is the
# count metric; the hours metrics come from the LEFT OUTER JOIN against
# job_charges upstream. 'charges' is a pair because the plugin reports
# cpu_charges and gpu_charges separately (they are separately meaningful and
# separately rankable) while the pill means "total charged".
#
# Charges are NOT proportional to hours: qos_factor is a genuine 0.0 for the
# 'uncharged' QoS, so a charges view can legitimately render an empty bar
# where an hours view shows work.
JOBS_METRIC_KEYS = {
    'jobs':      ('job_count',),
    'cpu_hours': ('cpu_hours',),
    'gpu_hours': ('gpu_hours',),
    'charges':   ('cpu_charges', 'gpu_charges'),
}
JOBS_METRIC_LABELS = {
    'jobs':      'Jobs',
    'cpu_hours': 'CPU-hours',
    'gpu_hours': 'GPU-hours',
    'charges':   'Charges',
}


def jobs_metric_value(d, metric, default='jobs'):
    """Value of *metric* from a plugin band / row / owner dict.

    One accessor so a multi-key metric can never be read as a single key
    somewhere and silently render as zero.
    """
    keys = JOBS_METRIC_KEYS.get(metric) or JOBS_METRIC_KEYS[default]
    return sum(float((d or {}).get(k) or 0) for k in keys)


def jobs_bucket_segments(bucket, metric, default='jobs'):
    """Per-bucket stacked-bar segments (active-metric units), bottom → top.

    The plugin envelope carries pre-truncated top-N ``owners`` per bucket
    with authoritative bucket totals, so — unlike the fs_scans
    ``histogram.bucket_segments``, which derives the long tail locally — the "other"
    base segment here is ``bucket total − Σ owners`` (it also absorbs
    NULL-username jobs). Owner segments follow ascending so the largest
    owner sits at the top of the bar. Empty list when the bucket has no
    owners (→ drawn as a single flat bar).
    """
    owners = bucket.get('owners') or {}
    if not owners:
        return []
    vals = sorted(jobs_metric_value(d, metric, default)
                  for d in owners.values())
    remainder = jobs_metric_value(bucket, metric, default) - sum(vals)
    if remainder > 1e-9:
        return [remainder] + vals
    return vals


def jobs_timeseries_series(ts, metric):
    """``(labels, series)`` for the stacked timeline, bottom → top.

    ``series`` is ``[(label, [value per band]), …]`` with ``'Others'``
    first — the ``get_daily_user_usage_for_project`` convention the
    resource-details Usage Trend already renders, so the two stacked charts
    read the same way.

    The plugin hands owners back in **global rank order, identical in every
    band**, so a name keeps its colour and its position across the whole
    axis. That is the property a stacked time series needs and the reason
    ``jobs_timeseries`` ranks once over the window rather than per band.
    Owners are reversed here so the largest lands on top of the stack, and
    "Others" is ``band total − Σ owners`` — derivable, never synthesized.
    """
    bands = (ts or {}).get('bands') or []
    labels = [b.get('label', '') for b in bands]
    if not bands:
        return labels, []

    # Every band carries the same keys; take the order from the first.
    owner_names = list((bands[0].get('owners') or {}).keys())

    others = []
    for band in bands:
        owners = band.get('owners') or {}
        total = jobs_metric_value(band, metric)
        named = sum(jobs_metric_value(owners.get(n), metric)
                    for n in owner_names)
        others.append(max(0.0, total - named))

    series = []
    if any(v > 1e-9 for v in others) or not owner_names:
        series.append(('Others', others))
    for name in reversed(owner_names):
        series.append((name, [
            jobs_metric_value((b.get('owners') or {}).get(name), metric)
            for b in bands
        ]))
    return labels, series
