"""`Layout` — the geometry axis.

Charts are rendered server-side at a fixed figure size and then scaled by CSS.
That works on a desktop and fails badly on a phone: measured on the running
app at 390px, the status dashboard's (18,10) chart renders at **0.224 scale**,
putting its 9-11pt labels at roughly **2px on screen**. No stylesheet can fix
that — the only fix is to re-render at a different figure size and font size,
which means the server has to know the layout. That is what this axis is for.

**Nothing requests `mobile` yet.** This ships the parameter, a defined profile
per family, and the cache-key plumbing; the transport (`matchMedia` sender,
`_parse_layout()` at the fragment boundary) and the visual tuning land in the
mobile PR. The numbers in `MOBILE_DEFAULTS` are a starting point chosen to be
legible, not a tuned design — treat them as the thing that pass revises.
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Layout:
    """Geometry for one rendering of a chart."""

    name: str

    #: matplotlib figure size in inches.
    figsize: tuple

    #: Base font size. Ticks and legends scale from it.
    base_fontsize: int

    #: 'right' — outside the axes, vertically centred (today's placement).
    #: 'below' — under the axes, for narrow viewports.
    #: 'none'  — suppress entirely.
    legend_placement: str

    #: Cap on legend entries, or None for no cap. A 20-entry legend is
    #: unreadable on a phone and steals the space the plot needs.
    max_legend_entries: int | None

    #: Target tick count on the category/date axis.
    max_ticks: int

    #: Tick-label rotation in degrees.
    label_rotation: int

    @property
    def is_mobile(self) -> bool:
        return self.name == 'mobile'


#: Applied to every family's mobile profile unless it overrides them.
MOBILE_DEFAULTS = dict(
    base_fontsize=9,
    legend_placement='below',
    max_legend_entries=6,
    max_ticks=5,
    label_rotation=45,
)


def profile(figsize, *, base_fontsize=11, legend_placement='right',
            max_legend_entries=None, max_ticks=12, label_rotation=0,
            mobile_figsize=None, **mobile_overrides):
    """Build one family's ``{'desktop': ..., 'mobile': ...}`` pair.

    The desktop values must reproduce today's rendering exactly — they are
    read straight off the existing `plt.subplots(figsize=...)` call and the
    tick/rotation constants around it.

    `mobile_figsize` defaults to a 4.5in-wide figure with the family's aspect
    ratio preserved, which is roughly 1:1 with a 390px viewport once the card
    padding is taken off, so text lands near its nominal point size instead of
    at 22% of it.
    """
    desktop = Layout(
        name='desktop',
        figsize=tuple(figsize),
        base_fontsize=base_fontsize,
        legend_placement=legend_placement,
        max_legend_entries=max_legend_entries,
        max_ticks=max_ticks,
        label_rotation=label_rotation,
    )

    if mobile_figsize is None:
        w, h = figsize
        mobile_w = 4.5
        mobile_figsize = (mobile_w, round(mobile_w * h / w, 2))

    mobile = replace(desktop, name='mobile', figsize=tuple(mobile_figsize),
                     **{**MOBILE_DEFAULTS, **mobile_overrides})
    return {'desktop': desktop, 'mobile': mobile}


def resolve_layout(layouts, layout) -> Layout:
    """Accept a `Layout`, a name, or None → the family's desktop profile.

    Lenient like `jobs/routes.py:_parse_period`: an unknown name means "no
    override", never an error. These are htmx fragments, and a stale
    localStorage replay must not break a card.
    """
    if isinstance(layout, Layout):
        return layout
    return layouts.get(layout or 'desktop', layouts['desktop'])
