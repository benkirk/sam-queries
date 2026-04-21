"""
Pace chart sketch — Plotly version.

Shows what generate_allocation_pace_chart() would look like in production.
Run directly to write pace_chart.html you can open in a browser:

    python scratch/pace_chart_sketch.py
"""

from datetime import date, timedelta
import random
import math

import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# Synthetic data — shaped like real CompChargeSummary rows
# ---------------------------------------------------------------------------

def _make_synthetic_charges(
    start: date, today: date, allocated: float, seed: int = 42
) -> dict[date, float]:
    """Daily charges with realistic variability: slow start, busy midterm."""
    random.seed(seed)
    n_days = (today - start).days
    linear_daily = allocated / 365          # expected daily rate for a 1-year alloc
    charges: dict[date, float] = {}
    for i in range(n_days):
        d = start + timedelta(days=i)
        # Ramp: lower in first month, higher in middle, weekend dips
        ramp = min(1.0, (i + 1) / 30)
        weekend_factor = 0.15 if d.weekday() >= 5 else 1.0
        noise = random.gauss(1.0, 0.25)
        daily = linear_daily * ramp * weekend_factor * max(0.0, noise) * 1.35
        charges[d] = daily
    return charges


# ---------------------------------------------------------------------------
# Core chart function (this is what would live in charts.py)
# ---------------------------------------------------------------------------

def generate_allocation_pace_chart(
    start_date: date,
    end_date: date,
    allocated: float,
    daily_charges: dict[date, float],   # {date: daily_charge_amount}
    resource_name: str = "Derecho",
    projcode: str = "SCSG0001",
) -> str:
    """
    Return an HTML fragment (<div> + inline script) for a Plotly pace chart.
    Caller injects this into a template where plotly.js is already loaded.

    The chart shows:
      - Cumulative actual usage (area)
      - Daily charges (bar, secondary trace)
      - Expected linear pace line (start → allocated at end_date)
      - Projected exhaustion extrapolated from 30-day trailing burn rate
      - Today marker + annotation
    """
    today = date.today()
    total_days = (end_date - start_date).days or 1

    # -- Build cumulative actuals -------------------------------------------
    sorted_days = sorted(daily_charges.keys())
    cumulative = 0.0
    cum_dates, cum_values, bar_dates, bar_values = [], [], [], []
    for d in sorted_days:
        charge = daily_charges[d]
        cumulative += charge
        cum_dates.append(d)
        cum_values.append(cumulative)
        bar_dates.append(d)
        bar_values.append(charge)

    total_used = cumulative
    elapsed_days = (today - start_date).days or 1

    # -- Expected pace line (full allocation period) ------------------------
    pace_dates = [start_date, end_date]
    pace_values = [0.0, float(allocated)]

    # -- 30-day trailing burn rate + projection ----------------------------
    window_start = today - timedelta(days=30)
    recent_charges = [v for d, v in daily_charges.items() if d >= window_start]
    burn_rate = sum(recent_charges) / max(len(recent_charges), 1)  # per day

    proj_dates, proj_values = [today], [total_used]
    if burn_rate > 0:
        days_to_exhaust = (allocated - total_used) / burn_rate
        exhaustion_date = today + timedelta(days=days_to_exhaust)
        # Only draw projection if it falls before end_date (i.e. ahead of pace)
        if exhaustion_date < end_date:
            proj_dates.append(exhaustion_date)
            proj_values.append(allocated)
        else:
            # Behind pace — project to end_date anyway (will be below allocated)
            proj_dates.append(end_date)
            proj_values.append(total_used + burn_rate * (end_date - today).days)

    exhaustion_date = proj_dates[-1] if len(proj_dates) > 1 else None

    # -- Percent used & pace status ----------------------------------------
    pct_used = (total_used / allocated * 100) if allocated else 0
    pct_elapsed = (elapsed_days / total_days * 100)
    ahead = pct_used > pct_elapsed      # spending faster than expected

    # -----------------------------------------------------------------------
    # Build Plotly figure
    # -----------------------------------------------------------------------
    fig = go.Figure()

    # 1. Daily charges — bar (secondary y)
    fig.add_trace(go.Bar(
        x=bar_dates, y=bar_values,
        name='Daily charges',
        marker_color='rgba(100, 149, 237, 0.4)',
        yaxis='y2',
        hovertemplate='%{x|%b %d}<br>Daily: %{y:,.0f} core-hrs<extra></extra>',
    ))

    # 2. Cumulative actual usage — filled area (primary y)
    fig.add_trace(go.Scatter(
        x=cum_dates, y=cum_values,
        mode='lines',
        name='Cumulative used',
        line=dict(color='steelblue', width=2),
        fill='tozeroy',
        fillcolor='rgba(70, 130, 180, 0.15)',
        hovertemplate='%{x|%b %d, %Y}<br>Total used: %{y:,.0f} core-hrs<extra></extra>',
    ))

    # 3. Expected linear pace — dashed grey
    fig.add_trace(go.Scatter(
        x=pace_dates, y=pace_values,
        mode='lines',
        name='Expected pace',
        line=dict(color='#888', width=1.5, dash='dash'),
        hoverinfo='skip',
    ))

    # 4. Projection from today — dotted orange/red depending on status
    if len(proj_dates) > 1:
        proj_color = 'tomato' if ahead else 'orange'
        proj_label = (
            f"Projected exhaustion {exhaustion_date.strftime('%b %d')}"
            if ahead else 'Projected end-of-period'
        )
        fig.add_trace(go.Scatter(
            x=proj_dates, y=proj_values,
            mode='lines',
            name=proj_label,
            line=dict(color=proj_color, width=2, dash='dot'),
            hoverinfo='skip',
        ))

    # 5. Allocated ceiling — horizontal reference line
    fig.add_hline(
        y=allocated,
        line=dict(color='red', width=1, dash='longdash'),
        annotation_text=f'Allocated: {allocated:,.0f}',
        annotation_position='top left',
        annotation_font_size=11,
    )

    # 6. Today vertical line (add_vline has a string-date bug in some versions)
    fig.add_shape(
        type='line',
        xref='x', yref='paper',
        x0=str(today), x1=str(today),
        y0=0, y1=1,
        line=dict(color='rgba(0,0,0,0.35)', width=1, dash='dot'),
    )
    fig.add_annotation(
        xref='x', yref='paper',
        x=str(today), y=1.0,
        text='Today',
        showarrow=False,
        font=dict(size=10, color='rgba(0,0,0,0.5)'),
        yanchor='bottom',
    )

    # -----------------------------------------------------------------------
    # Annotation: pace status summary
    # -----------------------------------------------------------------------
    days_remaining_alloc = (end_date - today).days
    if ahead and exhaustion_date:
        days_early = (end_date - exhaustion_date).days
        status_text = (
            f"<b>⚠ Ahead of pace</b><br>"
            f"Projected to exhaust <b>{days_early}d early</b><br>"
            f"({exhaustion_date.strftime('%b %d, %Y')})"
        )
        status_color = 'rgba(255,80,60,0.12)'
        border_color = 'tomato'
    else:
        status_text = (
            f"<b>On pace</b><br>"
            f"{pct_used:.1f}% used / {pct_elapsed:.1f}% elapsed<br>"
            f"{days_remaining_alloc}d remaining"
        )
        status_color = 'rgba(60,180,60,0.10)'
        border_color = 'mediumseagreen'

    fig.add_annotation(
        xref='paper', yref='paper',
        x=0.02, y=0.97,
        text=status_text,
        showarrow=False,
        align='left',
        bgcolor=status_color,
        bordercolor=border_color,
        borderwidth=1,
        borderpad=6,
        font=dict(size=11),
    )

    # -----------------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------------
    fig.update_layout(
        title=dict(
            text=f'{projcode} — {resource_name} Allocation Pace',
            font=dict(size=15),
        ),
        xaxis=dict(
            title='Date',
            range=[str(start_date), str(end_date)],
            showgrid=True,
            gridcolor='rgba(0,0,0,0.06)',
        ),
        yaxis=dict(
            title='Cumulative core-hours',
            showgrid=True,
            gridcolor='rgba(0,0,0,0.06)',
            rangemode='tozero',
        ),
        yaxis2=dict(
            title='Daily core-hours',
            overlaying='y',
            side='right',
            showgrid=False,
            rangemode='tozero',
        ),
        legend=dict(
            orientation='h',
            yanchor='bottom', y=1.02,
            xanchor='right', x=1,
        ),
        hovermode='x unified',
        plot_bgcolor='white',
        paper_bgcolor='white',
        margin=dict(l=60, r=60, t=80, b=50),
        height=420,
    )

    return fig.to_html(include_plotlyjs=False, full_html=False, div_id='pace-chart')


# ---------------------------------------------------------------------------
# Demo: write a standalone HTML file you can open in a browser
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    start  = date(2025, 10, 1)
    end    = date(2026, 9, 30)
    today  = date(2026, 4, 21)
    alloc  = 1_800_000.0

    charges = _make_synthetic_charges(start, today, alloc)
    chart_div = generate_allocation_pace_chart(
        start_date=start,
        end_date=end,
        allocated=alloc,
        daily_charges=charges,
        resource_name='Derecho',
        projcode='SCSG0001',
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pace Chart Demo</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>body {{ font-family: sans-serif; padding: 20px; background: #f8f9fa; }}</style>
</head>
<body>
  <h2>Allocation Pace Chart — Plotly Sketch</h2>
  {chart_div}
  <p style="color:#888; font-size:12px">
    Synthetic data · 1.8M core-hr Derecho allocation · Oct 2025 – Sep 2026
  </p>
</body>
</html>"""

    out = 'scratch/pace_chart.html'
    with open(out, 'w') as f:
        f.write(html)
    print(f"Wrote {out} — open it in a browser.")
