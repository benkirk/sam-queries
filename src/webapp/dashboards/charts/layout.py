"""`Layout` — the geometry axis.

Charts are rendered server-side at a fixed figure size and then scaled by CSS.
That works on a desktop and fails badly on a phone: measured on the running
app at 390px, the status dashboard's (18,10) chart renders at **0.224 scale**,
putting its 9-11pt labels at roughly **2px on screen**. No stylesheet can fix
that — the only fix is to re-render at a different figure size and font size,
which means the server has to know the layout. That is what this axis is for.

## What the mobile pass changed here

PR 1 shipped this module with `mobile` defined but unrequested, and said of
`MOBILE_DEFAULTS`: *"a starting point chosen to be legible, not a tuned design
— treat them as the thing that pass revises."* This is that revision.

Two of the six fields — `legend_placement` and `max_legend_entries` — were
declared and read by nothing; `base_fontsize` reached two charts of fifteen,
`max_ticks` one, `label_rotation` two. All six are now consumed by every
family that has the concept.

The aspect-preserving `mobile_figsize` default is gone. Preserving an 18:5
ratio at phone width gives a 4.5in x 1.25in strip, and once the legend moves
underneath, the plot itself is under an inch tall. **Mobile figures are
declared explicitly per family**, sized so the tight-bbox intrinsic width
lands near 350pt — roughly 1:1 with a phone viewport once card padding is off,
which is what puts a 9pt label on screen at ~9px instead of ~2px.
"""

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Layout:
    """Geometry for one rendering of a chart."""

    name: str

    #: matplotlib figure size in inches.
    figsize: tuple

    #: Base font size — tick labels and axis labels.
    #:
    #: Desktop leaves this at the rcParams value (11), so applying it is a
    #: no-op and today's output is reproduced byte for byte.
    base_fontsize: int

    #: 'right' — outside the axes, vertically centred (today's placement).
    #: 'below' — under the axes, for narrow viewports.
    #: 'none'  — suppress entirely.
    legend_placement: str

    #: Cap on legend entries, or None for no cap. A 20-entry legend is
    #: unreadable on a phone and steals the space the plot needs.
    #:
    #: Charts honour this at whichever point keeps the picture *honest*, which
    #: is not the same point for every family: a pie caps its slices (capping
    #: only the legend would leave unlabelled wedges), the pace chart and the
    #: histogram clamp the "top N" they were already computing, and the
    #: stacked family caps legend rows while still drawing every band.
    max_legend_entries: int | None

    #: Target tick count on the category/date axis.
    max_ticks: int

    #: Tick-label rotation in degrees.
    label_rotation: int

    #: Legend text size, or None for "whatever the chart declares".
    #:
    #: Desktop is None on purpose. Legend sizes are genuinely per-family today
    #: — 9pt on pies and pace, 11pt on stacked and dual-panel, 13pt on the
    #: user/proj area chart — and there is no expression of `base_fontsize`
    #: that reproduces all four. Rather than flatten a real difference to make
    #: the axis look tidy, desktop defers to the class attribute and only
    #: mobile overrides.
    legend_fontsize: int | None = None

    #: Axis-label size, or None for "whatever the chart declares".
    #:
    #: Same None-means-defer rule as `legend_fontsize`, and it exists for the
    #: same reason: `UserProjectAreaChart` labels at 13pt where everything
    #: else leaves it to rcParams, and desktop must reproduce both.
    axis_label_fontsize: int | None = None

    #: Tick-label size, or None for "the chart's own, else `base_fontsize`".
    tick_fontsize: int | None = None


#: One size for every text role on a phone. The four `*_fontsize` fields are
#: separate because *desktop* needs them separate — a family may label at 13pt
#: and tick at 12pt — but at 4in wide there is no room for a hierarchy.
_MOBILE_FONT = 9

#: Applied to every family's mobile profile unless it overrides them.
#:
#: `figsize` is deliberately absent: there is no defensible default for it.
#: See the module docstring on why aspect preservation is the wrong rule.
MOBILE_DEFAULTS = dict(
    base_fontsize=_MOBILE_FONT,
    legend_fontsize=_MOBILE_FONT,
    axis_label_fontsize=_MOBILE_FONT,
    tick_fontsize=_MOBILE_FONT,
    legend_placement='below',
    max_legend_entries=6,
    max_ticks=5,
    label_rotation=45,
)


def profile(figsize, mobile_figsize, *, base_fontsize=11,
            legend_placement='right', max_legend_entries=None, max_ticks=12,
            label_rotation=0, mobile=None):
    """Build one family's ``{'desktop': ..., 'mobile': ...}`` pair.

    The keyword arguments configure **desktop**, and must reproduce today's
    rendering exactly — they are read straight off the existing
    `plt.subplots(figsize=...)` call and the tick/rotation constants around
    it. Mobile overrides go in the `mobile` dict, never as bare keywords: an
    earlier version collected them with `**kwargs`, which meant a name that
    happened to match a desktop parameter was silently applied to desktop
    instead. `legend_placement='right'` on the pies read as a mobile override
    and configured desktop, where it was already the default, so it did
    nothing at all and looked like it worked.

    `mobile_figsize` is **required and positional**. It used to default to the
    desktop aspect ratio at 4.5in wide, which sounds reasonable and is not:
    18:5 becomes a 4.5 x 1.25in strip, and a strip with its legend moved
    underneath has well under an inch of plot left. Every family now states
    its own, sized so the tight bbox lands near 350pt wide.
    """
    desktop = Layout(
        name='desktop',
        figsize=tuple(figsize),
        base_fontsize=base_fontsize,
        legend_placement=legend_placement,
        max_legend_entries=max_legend_entries,
        max_ticks=max_ticks,
        label_rotation=label_rotation,
        legend_fontsize=None,
    )

    overrides = {**MOBILE_DEFAULTS, **(mobile or {})}
    unknown = set(overrides) - {f.name for f in fields(Layout)}
    if unknown:
        raise TypeError(f'unknown Layout field(s) in mobile override: '
                        f'{sorted(unknown)}')
    return {'desktop': desktop,
            'mobile': replace(desktop, name='mobile',
                              figsize=tuple(mobile_figsize), **overrides)}


def resolve_layout(layouts, layout) -> Layout:
    """Accept a `Layout`, a name, or None → the family's desktop profile.

    Lenient like `jobs/routes.py:_parse_period`: an unknown name means "no
    override", never an error. These are htmx fragments, and a stale
    localStorage replay must not break a card.
    """
    if isinstance(layout, Layout):
        return layout
    return layouts.get(layout or 'desktop', layouts['desktop'])
