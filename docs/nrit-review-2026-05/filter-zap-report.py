#!/usr/bin/env python3
"""
Filter a ZAP HTML report to show only findings for a target host.

Removes two sources of noise introduced by proxying all browser traffic:
  1. The Sites: list in the report header (other domains browsed during the session)
  2. The Insights table rows for non-target sites

The actual alert findings are already scoped correctly by ZAP's passive scanner;
this script only cleans up the header metadata.

Usage:
    python3 filter-zap-report.py <report.html> [output.html]
    (output defaults to <stem>-filtered.html in the same directory)
"""

import re
import sys
from pathlib import Path

TARGET = "samuel.k8s.ucar.edu"


def filter_sites_line(html: str) -> str:
    """Rewrite the Sites: h2 to list only the target host."""
    def rewrite(m: re.Match) -> str:
        block = m.group(0)
        all_urls = re.findall(r'https?://\S+', block)
        kept = [u for u in all_urls if TARGET in u]
        sites_str = " ".join(kept) if kept else f"https://{TARGET}"
        # Preserve the surrounding whitespace/newline structure
        return re.sub(r'Sites:.*', f"Sites: {sites_str}", block, flags=re.DOTALL)

    return re.sub(r'<h2>[^<]*Sites:.*?</h2>', rewrite, html, flags=re.DOTALL)


def filter_insights_rows(html: str) -> str:
    """Remove Insights table <tr> rows whose site cell names a non-target host."""

    def maybe_drop_row(m: re.Match) -> str:
        row = m.group(0)
        # Insights rows have 5 <td> cells; the 3rd holds the site name.
        tds = re.findall(r'<td[^>]*>.*?</td>', row, re.DOTALL)
        if len(tds) < 3:
            return row  # header row or unexpected shape — keep it
        site_div = re.search(r'<div>(.*?)</div>', tds[2], re.DOTALL)
        if not site_div:
            return row
        site = site_div.group(1).strip()
        # Keep rows with no site or with the target site; drop everything else.
        if site and TARGET not in site:
            return ""
        return row

    # Scope the row filter to just the Insights table so we don't accidentally
    # touch alert rows elsewhere in the report.
    def filter_table(m: re.Match) -> str:
        return re.sub(r'<tr>.*?</tr>', maybe_drop_row, m.group(0), flags=re.DOTALL)

    return re.sub(
        r'<h3 class="left-header">Insights</h3>\s*<table[^>]*>.*?</table>',
        filter_table,
        html,
        flags=re.DOTALL,
    )


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <report.html> [output.html]", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = (
        Path(sys.argv[2])
        if len(sys.argv) >= 3
        else input_path.with_stem(input_path.stem + "-filtered")
    )

    html = input_path.read_text(encoding="utf-8")

    original_size = len(html)
    html = filter_sites_line(html)
    html = filter_insights_rows(html)
    filtered_size = len(html)

    output_path.write_text(html, encoding="utf-8")
    removed_kb = (original_size - filtered_size) / 1024
    print(f"Filtered report written to: {output_path}  ({removed_kb:.0f} KB removed)")


if __name__ == "__main__":
    main()
