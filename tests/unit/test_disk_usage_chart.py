"""Unit tests for the disk "Usage Over Time" stacked-area chart renderer.

``generate_disk_usage_stacked_area`` renders bytes (TiB/PiB y-axis) or
file counts (raw integer y-axis) from the same timeseries shape, selected
by ``metric``. matplotlib's SVG backend rasterizes axis text to vector
paths (``svg.fonttype`` defaults to ``'path'``), so we assert on behavior
— empty handling and that the two metrics produce distinct output — rather
than grepping for label strings.
"""

import pytest

from webapp.dashboards.charts import generate_disk_usage_stacked_area

pytestmark = pytest.mark.unit

_TIB = 1024 ** 4


def _timeseries():
    # Values where byte-scaling vs no-scaling clearly diverges.
    return {
        'dates': ['2026-04-04', '2026-04-11'],
        'series': [
            {'username': 'Others', 'values': [1 * _TIB, 2 * _TIB]},
            {'username': 'alice', 'values': [3 * _TIB, 4 * _TIB]},
        ],
    }


def test_empty_timeseries_returns_placeholder_for_both_metrics():
    for metric in ('bytes', 'files'):
        out = generate_disk_usage_stacked_area({'dates': [], 'series': []}, metric=metric)
        assert '<svg' not in out
        assert 'No disk-usage history' in out


def test_both_metrics_render_svg():
    for metric in ('bytes', 'files'):
        out = generate_disk_usage_stacked_area(_timeseries(), metric=metric)
        assert '<svg' in out


def test_bytes_and_files_render_differently():
    """Same data, different metric → different SVG (and distinct cache keys,
    so the two variants never collide in the LRU)."""
    svg_bytes = generate_disk_usage_stacked_area(_timeseries(), metric='bytes')
    svg_files = generate_disk_usage_stacked_area(_timeseries(), metric='files')
    assert svg_bytes != svg_files


def test_default_metric_matches_explicit_bytes():
    assert (
        generate_disk_usage_stacked_area(_timeseries())
        == generate_disk_usage_stacked_area(_timeseries(), metric='bytes')
    )
