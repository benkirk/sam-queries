"""Categorical stacked-bar histograms.

Two charts — the filesystem-scan distribution (Access history / File sizes)
and the job-history histogram (Wait Times / Job Sizes / Durations) — share a
structure: one bar per bucket, each bar a single-hue stack of the top owners
over an aggregated remainder, shaded light-to-dark so the spread between users
is legible before clicking. Both fall back to one solid bar per bucket under
`log_y`, because a stack carries no meaning on a log axis.

The differences worth keeping are small and real:

- **How the remainder is derived.** fs-scans has the full owner set and
  computes its long tail locally; the jobs plugin pre-truncates to top-N and
  reports authoritative bucket totals, so the remainder is
  ``bucket total − Σ owners`` (which also absorbs NULL-username jobs).
- **Scaling.** fs-scans divides its segments by a byte scale; jobs does not.
- **Clickability.** every fs-scans bucket with owners is clickable; a jobs
  band is clickable iff `job_count` is nonzero — deliberately following job
  counts rather than the plotted metric, so a charges view of an all-uncharged
  band draws at zero and loses its link while its table row still drills.
"""

from sam import fmt
from webapp.caching.chart import content_hash
from webapp.dashboards.charts import links
from webapp.dashboards.charts.base import BaseChart
from webapp.dashboards.charts.jobs_metrics import (
    JOBS_METRIC_LABELS, jobs_bucket_segments, jobs_metric_value,
)
from webapp.dashboards.charts.layout import profile
from webapp.dashboards.charts.series import fold_top, other_label
from webapp.dashboards.charts.theme import (
    UNITY_NCAR_BLUE, UNITY_STACK_10, scale_bytes, shade_family,
)

#: Top-N owners drawn as their own stack segment per bar; the rest collapse
#: into one aggregated "other" segment at the base. Matches the table's top-10.
_AH_TOP_SEGMENTS = 10


def bucket_segments(owners, metric='data'):
    """Per-bucket stack segments ``[(name, value)]``, bottom -> top.

    Values are in *metric* units (``'data'`` bytes or ``'files'`` counts): the
    folded long tail (``N other``) at the base, then the top
    ``_AH_TOP_SEGMENTS`` owners ascending, so the largest sits at the top of
    the bar. Empty when the bucket has no owners (a single flat bar).
    """
    if not owners:
        return []
    kept, rest, n_rest = fold_top(
        [(name, d.get(metric, 0) or 0) for name, d in owners.items()], _AH_TOP_SEGMENTS)
    return ([(other_label(n_rest), rest)] if n_rest else []) + kept[::-1]


def _by_username(owners, username_map):
    """fs-scans keys its owners by uid (an int, or a string once cached): rekey
    them by username, else ``uid N``, as the table beside the chart does."""
    def name(uid):
        as_int = int(uid) if str(uid).isdigit() else None
        return username_map.get(as_int) or username_map.get(str(uid)) or f'uid {uid}'
    return {name(uid): row for uid, row in (owners or {}).items()}


class CategoricalStackChart(BaseChart):
    """One bar per bucket; each bar a shaded single-hue stack."""

    #: Tablet: 11in puts the tight bbox at ~796pt, 9.5px in the 687px jobs
    #: card at a 768 viewport. 12in measured 8.9px there — this family looks
    #: like it needs the least shrinking (it starts at 14in, not 18) but its
    #: per-bucket owner segments give it a full-height legend to carry.
    LAYOUTS = profile((14, 5), (4.0, 2.6), (11, 4.2), label_rotation=30)
    grid = {'axis': 'y', 'alpha': 0.3}

    bar_edge_width = 0.5
    segment_edge_width = 0.3
    #: Drill target for a bar, or None.
    drill = None

    # --- subclass contract ------------------------------------------------

    def buckets(self):
        """Ordered bucket handles — whatever the subclass wants to index."""
        raise NotImplementedError

    def bucket_label(self, bucket) -> str:
        raise NotImplementedError

    def bucket_total(self, bucket) -> float:
        raise NotImplementedError

    def bucket_segments(self, bucket) -> list:
        """Stack segments ``[(name, value)]`` bottom -> top, or [] for a flat bar."""
        raise NotImplementedError

    def bucket_is_clickable(self, bucket) -> bool:
        return True

    def ylabel(self) -> str:
        raise NotImplementedError

    def amount(self, value) -> str:
        """A plotted value as its hover says it."""
        return fmt.number(value)

    def tooltip_text(self, label, value, owner=None) -> str:
        """Hover text for one bar, or for one owner's segment of it."""
        return ' · '.join(str(p) for p in (label, owner, self.amount(value)) if p)

    #: True to draw one solid bar per bucket instead of a stack. A log axis
    #: forces it; a subclass may also have no owner data at all.
    def flat_only(self) -> bool:
        return self.log_y

    def flat_bar_color(self, i):
        return self.band_colors[i]

    # --- lifecycle --------------------------------------------------------

    def prepare(self):
        self._buckets = list(self.buckets())
        self.labels = [self.bucket_label(b) for b in self._buckets]
        self.values = [self.bucket_total(b) for b in self._buckets]
        self.band_colors = self.theme.data_colors(
            [UNITY_STACK_10[i % len(UNITY_STACK_10)]
             for i in range(len(self.labels))])

    def is_empty(self) -> bool:
        # Defined explicitly per family — see BaseChart.is_empty on why the
        # base must not guess.
        return not self._buckets or not any(self.values)

    def draw(self, ax, layout, theme):
        if self.flat_only():
            bars = ax.bar(range(len(self.labels)), self.values,
                          color=[self.flat_bar_color(i)
                                 for i in range(len(self.labels))],
                          edgecolor=theme.bar_edge, linewidth=self.bar_edge_width)
            for i, (bucket, rect) in enumerate(zip(self._buckets, bars.patches)):
                self._link(rect, bucket, i)
                if self.values[i]:
                    self.tooltip(rect, self.tooltip_text(self.labels[i], self.values[i]))
            return

        for i, bucket in enumerate(self._buckets):
            segs = self.bucket_segments(bucket)
            if not segs:
                bar = ax.bar(i, self.values[i], color=self.band_colors[i],
                             edgecolor=theme.bar_edge, linewidth=self.bar_edge_width)
                self._link(bar.patches[0], bucket, i)
                if self.values[i]:
                    self.tooltip(bar.patches[0], self.tooltip_text(self.labels[i], self.values[i]))
                continue
            shades = shade_family(self.band_colors[i], len(segs),
                                  toward=theme.shade_toward)
            bottom = 0.0
            for (name, seg_val), shade in zip(segs, shades):
                cont = ax.bar(i, seg_val, bottom=bottom, color=shade,
                              edgecolor=theme.segment_edge,
                              linewidth=self.segment_edge_width)
                self._link(cont.patches[0], bucket, i)
                if seg_val:
                    self.tooltip(cont.patches[0],
                                 self.tooltip_text(self.labels[i], seg_val, owner=name))
                bottom += seg_val

    def _link(self, artist, bucket, i):
        if self.drill is None or not self.bucket_is_clickable(bucket):
            return
        artist.set_url(self.drill.url(i))

    def decorate(self, ax, layout, theme):
        ax.set_xticks(range(len(self.labels)))
        ax.set_xticklabels(self.labels, rotation=layout.label_rotation, ha='right')
        ax.set_ylabel(self.ylabel(), **self.label_kw(layout))
        if self.log_y:
            ax.set_yscale('log')
        self.apply_grid(ax, theme)


class DistributionHistogram(CategoricalStackChart):
    """A metric across filesystem-scan distribution buckets.

    Shared by the Access-history and File-size tabs — both consume the same
    ``{'bucket_labels', 'buckets': {label: {'data','files','owners'}},
    'reference_scan_date', ...}`` shape (see
    ``webapp.disk_scans.service.scan_access_history`` / ``scan_file_sizes``).
    The ``files``/``owners`` detail is surfaced in the surrounding table.
    """

    cache_name = 'distribution_histogram'
    cache_maxsize = 128
    empty_message = 'No distribution data for this scope'
    drill = links.AH_BUCKET

    def __init__(self, hist, *, log_y=False, metric='data'):
        self.hist = hist or {}
        self.log_y = log_y
        self.metric = metric

    @staticmethod
    def cache_key(hist, *, log_y=False, metric='data'):
        """Stable key from the per-bucket totals + segment shape + date + options.

        Hashes the bucket order, the exact stacked-bar segment values for the
        chosen *metric* (top-N owners + "other"), the snapshot date in the
        title, and the y-scale / metric flags — everything the rendered SVG
        depends on.
        """
        labels = list((hist or {}).get('bucket_labels', []))
        buckets = (hist or {}).get('buckets', {})
        names = (hist or {}).get('username_map') or {}
        payload = [
            (lbl, bucket_segments(_by_username(buckets.get(lbl, {}).get('owners'), names), metric))
            for lbl in labels
        ]
        return content_hash(
            [payload, str((hist or {}).get('reference_scan_date', '')),
             bool(log_y), str(metric)]
        )

    def buckets(self):
        return list(self.hist.get('bucket_labels') or [])

    def _row(self, label):
        return (self.hist.get('buckets') or {}).get(label, {})

    def bucket_label(self, bucket):
        return bucket

    def bucket_total(self, bucket):
        return (self._row(bucket).get(self.metric, 0) or 0) / self.scale

    def bucket_segments(self, bucket):
        owners = _by_username(self._row(bucket).get('owners'), self.hist.get('username_map') or {})
        return [(name, v / self.scale) for name, v in bucket_segments(owners, self.metric)]

    def bucket_is_clickable(self, bucket):
        return bool(self._row(bucket).get('owners'))

    def ylabel(self):
        return self._ylabel

    def amount(self, value):
        # Plotted values are scaled to the axis unit; a hover gives the real size.
        return fmt.number(value) if self.metric == 'files' else fmt.size(value * self.scale)

    def prepare(self):
        labels = list(self.hist.get('bucket_labels') or [])
        buckets = self.hist.get('buckets') or {}
        raw = [buckets.get(l, {}).get(self.metric, 0) or 0 for l in labels]

        if self.metric != 'files':
            # floor='GiB': three rungs here, unlike the disk-usage timeseries.
            self.scale, unit = scale_bytes(max(raw) if raw else 0, floor='GiB')
            self._ylabel = f'Data ({unit})'
        else:
            self.scale, self._ylabel = 1, 'Files'
        super().prepare()

    def decorate(self, ax, layout, theme):
        super().decorate(ax, layout, theme)
        if self.metric == 'files':
            ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())


class JobsHistogram(CategoricalStackChart):
    """Bar chart over a jobs_histogram plugin envelope; owner-stacked when
    possible.

    Shared by the Wait Times, Job Sizes, and Durations tabs — the envelope's
    bucket vector is already complete and ordered (zeros included), so the
    x-axis is stable across filter changes. When buckets carry ``owners``
    (plugin ``owners_limit``), each bar becomes a single-hue per-user stack
    over an aggregated remainder base; otherwise the historical flat
    single-series chart renders unchanged.
    """

    cache_name = 'jobs_histogram'
    cache_maxsize = 128
    empty_message = 'No jobs in this range'
    drill = links.JH_BUCKET

    def __init__(self, hist, *, metric='jobs', log_y=False):
        self.hist = hist or {}
        self.metric = metric
        self.log_y = log_y

    @staticmethod
    def cache_key(hist, *, metric='jobs', log_y=False):
        """Hash exactly what the SVG depends on: the bucket labels, the chosen
        metric's values and owner-segment split, the dimension, null_count and
        the y-scale (not the full envelope — e.g. min_param/max_param don't
        affect the rendering). The job_count positivity vector joins the key
        because it decides which bars carry drill URLs — an hours-metric SVG
        with matching hours but a different populated-band set must not be
        reused. Each segment is keyed with its owner's name."""
        buckets = (hist or {}).get('buckets') or []
        payload = [(b.get('label'), jobs_metric_value(b, metric),
                    tuple(jobs_bucket_segments(b, metric))) for b in buckets]
        clickable = [int(bool(b.get('job_count'))) for b in buckets]
        return content_hash([
            payload, clickable, str((hist or {}).get('dimension', '')),
            int((hist or {}).get('null_count') or 0), str(metric), bool(log_y),
        ])

    def buckets(self):
        return list(self.hist.get('buckets') or [])

    def bucket_label(self, bucket):
        return bucket.get('label', '')

    def bucket_total(self, bucket):
        return jobs_metric_value(bucket, self.metric)

    def bucket_segments(self, bucket):
        return jobs_bucket_segments(bucket, self.metric)

    def bucket_is_clickable(self, bucket):
        # Follows job_count, not the plotted metric: a band with no jobs is
        # never clickable whatever the charges view happens to draw.
        return bool(bucket.get('job_count'))

    def ylabel(self):
        return JOBS_METRIC_LABELS.get(self.metric, 'Jobs')

    def flat_only(self):
        # Owner-less envelope (owners_limit unset, or an older plugin) — the
        # historical flat single-series chart — and the log y-axis, on which a
        # stack carries no meaning.
        return self.log_y or not self._has_owners

    def flat_bar_color(self, i):
        # Without owners the whole chart is one series in the primary color
        # (NCAR blue, as the flat usage trend — NOT the stack palette's first
        # entry, which is gold); with owners it keeps
        # the per-band palette its stack would have used.
        # `band_colors` is already lifted for the theme; the bare fallback is
        # not, so it needs the same treatment — ncar-blue is 2.27:1 on the
        # dark card, and this branch paints the entire chart with it.
        return (self.band_colors[i] if self._has_owners
                else self.theme.data_color(UNITY_NCAR_BLUE))

    def prepare(self):
        self._has_owners = any(b.get('owners')
                               for b in (self.hist.get('buckets') or []))
        super().prepare()

    def decorate(self, ax, layout, theme):
        super().decorate(ax, layout, theme)
        ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
