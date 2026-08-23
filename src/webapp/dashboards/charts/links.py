"""Drill-down targets for chart artists.

Charts mark clickable artists with ``Artist.set_url()``, which makes
matplotlib's SVG backend wrap them in ``<a xlink:href="...">``. The href is a
SENTINEL, never a route the browser should follow -- ``svg-chart-links.js``
intercepts the click and dispatches the in-page behavior.

The scheme is ``#sam/<action>/<segment>/<segment>...``, percent-encoded, so a
username with a slash or a space cannot break the parse. **The attribute name
travels in the href**, so adding a row drill is one attribute declared at the
chart and needs no JavaScript change -- there is no prefix-to-attribute table
that could drift across the two languages.

``<a xlink:href>`` rather than ``set_gid()``: ids must be unique but one drill
target spans three artists (bar + legend patch + legend text), an ``<a>`` is
keyboard-focusable where a ``<g id>`` is not, and a ``#``-fragment degrades
safely when JS fails.

WARNING: no matplotlib import here, by design. This module and ``series.py``
are the two the chart layer could reuse under a different rendering backend;
``test_chart_module_boundaries.py`` enforces that they stay import-clean.
"""

from dataclasses import dataclass
from urllib.parse import quote

#: Leading segment identifying our sentinels. Matched by `svg-chart-links.js`.
SCHEME = '#sam'


def encode(action: str, *segments) -> str:
    """``#sam/<action>/<encoded segment>/...``

    ``safe=''`` so slashes inside a value are escaped rather than read as
    segment separators — projcodes and usernames are user data.
    """
    parts = [quote(str(s), safe='') for s in segments]
    return '/'.join([SCHEME, action, *parts])


@dataclass(frozen=True)
class RowDrill:
    """Expand the table row carrying ``<attr>="<value>"``.

    Resolved by the JS *within the clicked chart's tab pane*, so identical
    values in different panes never cross-fire. That scoping is also why a
    chart whose rows live in another pane must use `ModalRoute` instead — see
    the jobs activity timeline.
    """

    attr: str

    def url(self, value) -> str:
        return encode('row', self.attr, value)


@dataclass(frozen=True)
class DayDrill:
    """Expand a day row in the Historical Usage table.

    Bespoke rather than a `RowDrill` because the table has a 3-level mode for
    spans over 45 days: the parent month must be expanded before the day row
    exists to open.
    """

    def url(self, iso_date) -> str:
        return encode('day', iso_date)


@dataclass(frozen=True)
class UserDrill:
    """Expand a user's row in the Usage by User card.

    Bespoke because the lookup is by username against a client-sortable
    table's ``data-sort-value``, not by a row attribute.
    """

    def url(self, username) -> str:
        return encode('user', username)


@dataclass(frozen=True)
class ModalRoute:
    """Open an HTMX-driven Bootstrap modal.

    Carries a **real URL**, unlike the sentinels above: it is inspectable,
    degrades gracefully, and needs no server-side route table in the JS. That
    is why modals were left on their existing mechanism.
    """

    endpoint: str
    param: str

    def url(self, value) -> str:
        # Resolved lazily: `url_for` needs an application context, so a
        # module-level instance must not resolve at class-definition time.
        from flask import url_for
        return url_for(self.endpoint, **{self.param: value})


# --- The drill targets in use -------------------------------------------
# Adding a row-drill chart means adding one line here and nothing in the JS.

#: Usage Trend bars -> the Historical Usage day row.
DAY = DayDrill()

#: Stacked Usage Trend legend + By User pie -> the Usage by User row.
USAGE_USER = UserDrill()

#: fs-scans distribution histogram bands. Index-keyed, so the JS never parses
#: band labels and a trimmed axis stays consistent with the table.
AH_BUCKET = RowDrill('data-ah-bucket')

#: job-history histogram bands (Wait Times / Job Sizes / Durations).
JH_BUCKET = RowDrill('data-jh-bucket')

#: job-history activity timeline bands (Jobs tab).
JT_PERIOD = RowDrill('data-jt-period')

#: fs-scans entity pie, By User / By Group tabs.
DISK_OWNER = RowDrill('data-owner-uid')
DISK_GROUP = RowDrill('data-group-gid')

#: job-history usage pies.
JOB_USER = RowDrill('data-job-user')
JOB_PROJECT = RowDrill('data-job-project')

#: Legend entries that open a quick-view modal.
PROJECT_MODAL = ModalRoute('user_dashboard.project_details_modal', 'projcode')
USER_MODAL = ModalRoute('admin_dashboard.user_card', 'username')
