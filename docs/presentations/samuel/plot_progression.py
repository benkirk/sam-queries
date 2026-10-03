"""Draw images/loc_progression.png from data/loc_progression.tsv (refresh time only)."""
import csv
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

HERE = Path(__file__).parent
FONTS = HERE.parent / "common/_extensions/benkirk/ncar/ncar-assets/fonts/Poppins"
for ttf in FONTS.glob("*.ttf"):
    font_manager.fontManager.addfont(str(ttf))

INK, MUTED, GRID = "#00357A", "#6B7280", "#E5E7EB"
# Bottom to top. Blue/orange validated (dataviz validate_palette.js); gray is the "other" fold.
BANDS = [("source_lines", "Source", "#0057C2"),
         ("test_lines", "Tests", "#E8890C"),
         ("other_lines", "Docs & other", "#B8BEC7")]
LEGACY_LOC = 250_000          # "roughly 250K lines", Confessions of a Vibe Coder, slide 3
TALK = date(2026, 3, 11)      # the March talk the series picks up from

rows = list(csv.DictReader(open(HERE / "data/loc_progression.tsv"), delimiter="\t"))
x = [date.fromisoformat(r["date"]) for r in rows]
series = [[int(r[col]) for r in rows] for col, _, _ in BANDS]
total = [int(r["lines"]) for r in rows]
assert all(sum(v) == t for *v, t in zip(*series, total)), "bands must sum to the total"

plt.rcParams.update({"font.family": "Poppins", "font.size": 13})
fig, ax = plt.subplots(figsize=(10, 4.2), dpi=200)
ax.stackplot(x, *series, colors=[c for _, _, c in BANDS],
             edgecolor="white", linewidth=2)          # 2px surface seam between bands

ax.axhline(LEGACY_LOC, color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)))
ax.text(x[0], LEGACY_LOC, "legacy SAM, ~250K lines†", color=MUTED, fontsize=11,
        va="bottom", ha="left")

def k(v):
    return f"{v/1e3:,.1f}K" if v < 10_000 else f"{v/1e3:,.0f}K"

# Direct labels: each band at its vertical middle on the last point.
last, base = len(x) - 1, 0
for (_, label, _), vals in zip(BANDS, series):
    mid = base + vals[last] / 2
    ax.annotate(f"{label}  {k(vals[last])}", (x[last], mid), xytext=(10, 0),
                textcoords="offset points", va="center", ha="left", color=INK, fontsize=11)
    base += vals[last]
ax.annotate(f"Total  {k(total[last])}", (x[last], total[last]), xytext=(10, 0),
            textcoords="offset points", va="center", ha="left", color=INK, fontsize=11,
            fontweight="bold")

def mark(i, label, dx, dy, ha="center"):
    ax.plot(x[i], total[i], "o", color=INK, markersize=7,
            markeredgecolor="white", markeredgewidth=2, zorder=3, clip_on=False)
    ax.annotate(label, (x[i], total[i]), xytext=(dx, dy), textcoords="offset points",
                ha=ha, color=INK, fontsize=11)

talk = max(i for i, d in enumerate(x) if d <= TALK)
mark(0, k(total[0]), 4, 12, ha="left")
mark(talk, f"the March talk\n{k(total[talk])}", 0, 14)

ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1e3:,.0f}K" if v else "0"))
ax.set_ylim(0, max(total) * 1.15)
ax.set_xlim(x[0], x[last])
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
ax.spines["bottom"].set_color(MUTED)
ax.tick_params(colors=MUTED, length=0, labelsize=12)
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
# One tick per data point (each a month-end), the year shown where it changes.
ax.set_xticks(x, [f"{d:%b}\n{d:%Y}" if i == 0 or d.month == 1 else f"{d:%b}"
                  for i, d in enumerate(x)])
handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in BANDS]
ax.legend(handles, [lbl for _, lbl, _ in BANDS], loc="upper left", ncol=3, frameon=False,
          fontsize=11, labelcolor=INK, bbox_to_anchor=(0, 1.02))

FOOTNOTE = ("† Every text line on main, the method of the March talk. Tests: tests/ and test_*.py; "
            "docs & other: docs/ and non-code files.\n"
            "   Legacy SAM's ~250K was counted differently, so read it as a landmark, not a race.")
fig.tight_layout(rect=(0, 0.11, 0.87, 1))            # right margin holds the direct labels
fig.text(0.01, 0.015, FOOTNOTE, color=MUTED, fontsize=9.5, ha="left", va="bottom")
fig.savefig(HERE / "images/loc_progression.png", transparent=False, facecolor="white")
