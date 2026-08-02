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

## The tablet profile

The mobile pass gave phones a figure and left tablets in the desktop band,
where an 18in figure is squeezed into ~640px. Measured on the running app,
that put the status dashboard's smallest label at **6.0px at a 768 viewport**
— worse than the same chart on a phone, which is the defect this profile
exists to close.

The band is Bootstrap `md` to just under `xl`: **768px to 1199.98px**. The
lower edge is forced (it is where `mobile` ends). The upper edge was measured
rather than chosen: on these pages the chart's container is the viewport less
144px, so desktop's smallest label lands at 8.1px at 1024, 9.7px at 1200 and
10.4px at 1280. Desktop stops being the problem somewhere around 1110-1200,
and 1200 is the breakpoint there. Extending the band to `xxl` instead would
hand every 1280 laptop a figure sized for a 640px card.

A tablet layout is **desktop with a smaller figure**, not a large phone —
see `TABLET_DEFAULTS`. The figures are sized so the tight bbox lands near
730pt, which is what keeps a 9-11pt label at ~9px on the narrow edge of the
band. It grows to ~15px at the wide edge, because the band spans a 1.7x range
of container widths and the chart fills whatever it is given; the phone band
spans 2.2x and ships with the same property.
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


#: Applied to every family's tablet profile unless it overrides them.
#:
#: Deliberately tiny, and deliberately says nothing about fonts. A tablet is
#: a small desktop, not a large phone: `legend_placement`, `label_rotation`
#: and every `*_fontsize` are left absent so they inherit *desktop's* values,
#: which keeps the per-family type hierarchy (13pt labels over 12pt ticks on
#: the user/project area chart) that the phone had to flatten.
#:
#: Only two things genuinely do not survive the smaller figure: twelve date
#: ticks, and an uncapped legend — 20 pace-chart rows are taller than a 3in
#: plot at any width.
TABLET_DEFAULTS = dict(
    max_legend_entries=10,
    max_ticks=8,
)


def profile(figsize, mobile_figsize, tablet_figsize, *, base_fontsize=11,
            legend_placement='right', max_legend_entries=None, max_ticks=12,
            label_rotation=0, mobile=None, tablet=None):
    """Build one family's ``{'desktop': ..., 'mobile': ..., 'tablet': ...}``.

    The keyword arguments configure **desktop**, and must reproduce today's
    rendering exactly — they are read straight off the existing
    `plt.subplots(figsize=...)` call and the tick/rotation constants around
    it. Non-desktop overrides go in the `mobile` / `tablet` dicts, never as
    bare keywords: an earlier version collected them with `**kwargs`, which
    meant a name that happened to match a desktop parameter was silently
    applied to desktop instead. `legend_placement='right'` on the pies read as
    a mobile override and configured desktop, where it was already the
    default, so it did nothing at all and looked like it worked.

    Both non-desktop figsizes are **required and positional**. `mobile_figsize`
    used to default to the desktop aspect ratio at 4.5in wide, which sounds
    reasonable and is not: 18:5 becomes a 4.5 x 1.25in strip, and a strip with
    its legend moved underneath has well under an inch of plot left.
    `tablet_figsize` is positional for the same reason — the tight bbox is a
    function of the legend contents, so it can only be measured, never derived
    — and because a family that genuinely wants desktop's figure (the pies do)
    should have to say so.
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

    def _derive(name, figsize_, defaults, given):
        overrides = {**defaults, **(given or {})}
        unknown = set(overrides) - {f.name for f in fields(Layout)}
        if unknown:
            raise TypeError(f'unknown Layout field(s) in {name} override: '
                            f'{sorted(unknown)}')
        return replace(desktop, name=name, figsize=tuple(figsize_), **overrides)

    return {
        'desktop': desktop,
        'mobile': _derive('mobile', mobile_figsize, MOBILE_DEFAULTS, mobile),
        'tablet': _derive('tablet', tablet_figsize, TABLET_DEFAULTS, tablet),
    }


def resolve_layout(layouts, layout) -> Layout:
    """Accept a `Layout`, a name, or None → the family's desktop profile.

    Lenient like `jobs/routes.py:_parse_period`: an unknown name means "no
    override", never an error. These are htmx fragments, and a stale
    localStorage replay must not break a card.
    """
    if isinstance(layout, Layout):
        return layout
    return layouts.get(layout or 'desktop', layouts['desktop'])
