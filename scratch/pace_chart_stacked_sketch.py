"""
Plotly version of generate_pace_chart_matplotlib (staging charts.py §6).

Same data contract, same visual concept — stacked-area, one band per project,
past-rate | future-rate step at active_at — but rendered client-side via Plotly.

Run to produce scratch/pace_chart_stacked.html:

    python scratch/pace_chart_stacked_sketch.py
"""

from datetime import datetime, timedelta
from typing import List, Dict
import random
import numpy as np

import plotly.graph_objects as go

# Staging palette equivalents (tab10)
_TAB10 = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
    '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
]
_OTHER_COLOR = 'rgba(190,190,190,0.7)'


# ---------------------------------------------------------------------------
# Data prep — identical logic to staging's _pace_bands()
# ---------------------------------------------------------------------------

def _pace_bands(allocations: List[Dict], active_at: datetime,
                window_start: datetime, window_end: datetime):
    n_days = (window_end - window_start).days + 1
    today_idx = (active_at - window_start).days

    bands = []
    for a in allocations:
        s, e = a['start_date'], a['end_date']
        if s is None or e is None or e <= s:
            continue
        if e < window_start or s > window_end:
            continue

        amount = float(a.get('total_amount') or 0.0)
        used   = float(a.get('total_used')   or 0.0)

        past_end  = min(active_at, e)
        past_days = max((past_end - s).days, 0)
        past_rate = (used / past_days) if past_days > 0 else 0.0

        future_start = max(active_at, s)
        future_days  = max((e - future_start).days, 0)
        future_rate  = ((amount - used) / future_days) if future_days > 0 else 0.0

        if past_rate <= 0 and future_rate <= 0:
            continue

        rates   = np.zeros(n_days)
        s_idx   = max(0, (s - window_start).days)
        e_idx   = min(n_days, (e - window_start).days + 1)

        if past_rate   > 0: rates[s_idx:min(today_idx, e_idx)] = past_rate
        if future_rate > 0: rates[max(today_idx, s_idx):e_idx]  = future_rate

        bands.append((a.get('projcode', ''), amount, rates))

    days = [window_start + timedelta(days=i) for i in range(n_days)]
    return days, bands


# ---------------------------------------------------------------------------
# Chart function
# ---------------------------------------------------------------------------

def generate_pace_chart_plotly(
    allocations: List[Dict],
    active_at: datetime,
    window_days: int = 180,
    top_n: int = 10,
    resource_name: str = '',
) -> str:
    """
    Stacked-area pace chart rendered client-side via Plotly.

    Same call signature as generate_pace_chart_matplotlib().
    Returns an HTML fragment (<div> + inline script); caller must load
    plotly.js once in the base template.
    """
    if not allocations:
        return '<div class="text-center text-muted">No allocations available</div>'

    window_start = active_at - timedelta(days=window_days)
    window_end   = active_at + timedelta(days=window_days)

    days, bands = _pace_bands(allocations, active_at, window_start, window_end)
    if not bands:
        return f'<div class="text-center text-muted">No allocations in the ±{window_days}d window</div>'

    # Rank by total_amount; top-N get colors, rest collapse to Other
    proj_totals: Dict[str, float] = {}
    for pc, amount, _ in bands:
        proj_totals[pc] = proj_totals.get(pc, 0.0) + amount

    top_projs  = [pc for pc, _ in sorted(proj_totals.items(),
                  key=lambda kv: kv[1], reverse=True)[:top_n]]
    color_map  = {pc: _TAB10[i % len(_TAB10)] for i, pc in enumerate(top_projs)}
    n_other    = len(proj_totals) - len(top_projs)
    other_lbl  = f'Other ({n_other} project{"s" if n_other != 1 else ""})'

    # Aggregate rate arrays per color group (same SVG-size fix as staging)
    OTHER_KEY = '__other__'
    n_days = len(days)
    group_keys    = list(top_projs) + [OTHER_KEY]
    group_rates   = {k: np.zeros(n_days) for k in group_keys}
    group_totals  = {k: 0.0              for k in group_keys}

    for pc, amount, rates in bands:
        key = pc if pc in color_map else OTHER_KEY
        group_rates[key]  += rates
        group_totals[key] += amount

    # Build traces — top-N in rank order, Other on top; skip empty groups
    fig = go.Figure()

    ordered = [(k, group_rates[k]) for k in top_projs] + [(OTHER_KEY, group_rates[OTHER_KEY])]
    ordered = [(k, r) for k, r in ordered if r.any()]

    for key, rates in ordered:
        if key == OTHER_KEY:
            color = _OTHER_COLOR
            name  = f'{other_lbl}<br><span style="color:#999">total: {_fmt(group_totals[key])}</span>'
        else:
            color = color_map[key]
            name  = f'{key}<br><span style="color:#999">total: {_fmt(group_totals[key])}</span>'

        # Mask zeros as None so they don't visually extend the band outside
        # the allocation's active window (Plotly gaps on None).
        y = [float(v) if v > 0 else None for v in rates]

        fig.add_trace(go.Scatter(
            x=days,
            y=y,
            name=name,
            stackgroup='one',           # this is what stacks the areas
            fillcolor=color,
            line=dict(color=color, width=0),
            mode='none',                # no markers or lines, just the fill
            hovertemplate=(
                f'<b>{key if key != OTHER_KEY else other_lbl}</b><br>'
                '%{x|%b %d, %Y}<br>'
                'Rate: %{y:,.0f} core-hrs/day'
                '<extra></extra>'
            ),
            # Past region is slightly translucent to hint at the step
            opacity=0.75,
        ))

    # Today vertical line
    fig.add_shape(
        type='line', xref='x', yref='paper',
        x0=str(active_at.date()), x1=str(active_at.date()),
        y0=0, y1=1,
        line=dict(color='rgba(40,40,40,0.5)', width=1.5, dash='dot'),
    )
    fig.add_annotation(
        xref='x', yref='paper',
        x=str(active_at.date()), y=1.0,
        text=' today', showarrow=False,
        font=dict(size=10, color='rgba(40,40,40,0.6)'),
        yanchor='bottom', xanchor='left',
    )

    title = f'Allocation Pace — {resource_name}' if resource_name else 'Allocation Pace'

    fig.update_layout(
        title=dict(text=title, font=dict(size=14)),
        xaxis=dict(
            range=[str(window_start.date()), str(window_end.date())],
            showgrid=True, gridcolor='rgba(0,0,0,0.06)',
            tickformat='%b %Y',
        ),
        yaxis=dict(
            title='Rate (core-hrs / day)',
            showgrid=True, gridcolor='rgba(0,0,0,0.06)',
            rangemode='tozero',
            tickformat=',d',
        ),
        # Unified hover shows every stacked group at the hovered date
        hovermode='x unified',
        legend=dict(
            orientation='v',
            yanchor='top', y=1,
            xanchor='left', x=1.01,
            font=dict(size=10),
            # Each legend entry toggles its band on/off
        ),
        plot_bgcolor='white',
        paper_bgcolor='white',
        margin=dict(l=60, r=220, t=60, b=50),
        height=380,
    )

    return fig.to_html(include_plotlyjs=False, full_html=False,
                       div_id='pace-chart-stacked')


# ---------------------------------------------------------------------------
# Number formatter (stand-in for sam.fmt.number)
# ---------------------------------------------------------------------------

def _fmt(v: float) -> str:
    if v >= 1_000_000: return f'{v/1_000_000:.1f}M'
    if v >= 1_000:     return f'{v/1_000:.0f}K'
    return f'{v:.0f}'


# ---------------------------------------------------------------------------
# Synthetic allocation data
# ---------------------------------------------------------------------------

def _make_allocations(n: int = 40, seed: int = 7) -> List[Dict]:
    random.seed(seed)
    allocs = []
    base = datetime(2025, 10, 1)
    today = datetime(2026, 4, 21)
    project_names = [f'UNIV{i:04d}' for i in range(1, n + 1)]

    for pc in project_names:
        start = base - timedelta(days=random.randint(0, 60))
        end   = start + timedelta(days=random.randint(180, 400))
        amount = random.uniform(50_000, 2_000_000)
        elapsed = max((today - start).days, 1)
        total_days = max((end - start).days, 1)
        # Burn between 20% and 140% of expected pace so far
        burn_frac = random.uniform(0.2, 1.4) * (elapsed / total_days)
        used = min(amount * burn_frac, amount * 0.98)
        allocs.append({
            'projcode':     pc,
            'start_date':   start,
            'end_date':     end,
            'total_amount': amount,
            'total_used':   used,
        })
    return allocs


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    today = datetime(2026, 4, 21)
    allocs = _make_allocations(n=40)

    chart_div = generate_pace_chart_plotly(
        allocations=allocs,
        active_at=today,
        window_days=180,
        top_n=8,
        resource_name='Derecho',
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Stacked Pace Chart — Plotly</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ font-family: sans-serif; padding: 20px; background: #f8f9fa; }}
    .note {{ color: #888; font-size: 12px; margin-top: 8px; }}
  </style>
</head>
<body>
  <h2>Allocation Pace Chart — Plotly (stacked)</h2>
  {chart_div}
  <p class="note">
    Synthetic data · 40 projects · Derecho · ±180d window around 2026-04-21<br>
    Each band = daily burn rate. Left of <i>today</i>: historical rate (used/elapsed).
    Right of <i>today</i>: required rate (remaining/remaining-days).
    A band that <i>narrows</i> after today = project is ahead of pace.
    Click legend entries to isolate individual projects.
  </p>
</body>
</html>"""

    out = 'scratch/pace_chart_stacked.html'
    with open(out, 'w') as f:
        f.write(html)
    print(f'Wrote {out} — open in a browser.')
