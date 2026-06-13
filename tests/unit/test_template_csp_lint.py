"""
Template lint guard for the Content-Security-Policy (webapp/utils/csp.py).

The policy is nonce-free: script-src 'self' means NO inline executable
<script> blocks, NO on*= event-handler attributes, NO hx-on: attributes
(htmx evaluates them via Function(), needing 'unsafe-eval'), and — by
choice, for hygiene — no <style> blocks. This test regex-scans every
template so new debt cannot land.

ALLOWED_VIOLATIONS is the ratchet: it pins the *exact* current debt per
template. Extraction commits shrink it; it reaches {} at the end of the
CSP work and stays empty. The assertion is equality, not <=, so a fixed
file must also be removed from the list — the allowlist can't go stale.

What to do instead of an inline pattern (see static/js/actions.js once
it lands, and docs/plans/DEFERRED-CSP-discussion.md):
- behavior      → static JS file, delegated listeners on document.body
- per-swap init → htmx.onLoad(root => ...) in static JS
- dynamic data  → data-* attributes, or a non-executable
                  <script type="application/json">{{ x|tojson }}</script> block
- styling       → static CSS file (style= attributes remain allowed)
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'

JINJA_COMMENT_RE = re.compile(r'\{#.*?#\}', re.S)
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.S)

CHECKS = {
    # executable <script> tag: no src=, not a JSON data block
    'inline_scripts': re.compile(
        r'<script\b(?![^>]*\bsrc\s*=)(?![^>]*application/(?:ld\+)?json)[^>]*>', re.I),
    # explicit attribute list avoids false positives like season= / monotone=
    'event_handlers': re.compile(
        r'\son(click|dblclick|change|submit|input|load|error|keyup|keydown|keypress|'
        r'focus|blur|mouseover|mouseout|mouseenter|mouseleave|select|reset|scroll|'
        r'toggle)\s*=', re.I),
    'hx_on': re.compile(r'\bhx-on:'),
    'style_blocks': re.compile(r'<style\b', re.I),
}

# Ratchet: exact remaining debt, seeded 2026-06-12 at CSP-work start.
# Shrink with every extraction commit; never grow.
ALLOWED_VIOLATIONS = {
    'dashboards/admin/edit_project.html': {'inline_scripts': 1, 'style_blocks': 1},
    'dashboards/admin/fragments/configuration_card.html': {'style_blocks': 1},
    'dashboards/admin/fragments/create_allocation_type_form_htmx.html': {'inline_scripts': 1},
    'dashboards/admin/fragments/create_mnemonic_code_form_htmx.html': {'inline_scripts': 1, 'event_handlers': 3},
    'dashboards/admin/fragments/create_project_form_htmx.html': {'inline_scripts': 1, 'event_handlers': 1},
    'dashboards/admin/fragments/disk_root_directories_section.html': {'event_handlers': 1},
    'dashboards/admin/fragments/edit_allocation_form_htmx.html': {'event_handlers': 1},
    'dashboards/admin/fragments/exchange_allocation_form_htmx.html': {'inline_scripts': 1},
    'dashboards/admin/fragments/facility_card.html': {'inline_scripts': 1},
    'dashboards/admin/fragments/group_search_results_htmx.html': {'hx_on': 1},
    'dashboards/admin/fragments/institution_filters.html': {'event_handlers': 1},
    'dashboards/admin/fragments/project_allocation_tree_htmx.html': {'style_blocks': 1},
    'dashboards/admin/fragments/project_directories_card.html': {'inline_scripts': 1, 'event_handlers': 2},
    'dashboards/admin/fragments/project_linked_elements_htmx.html': {'inline_scripts': 1, 'event_handlers': 6},
    'dashboards/admin/fragments/project_search_results_htmx.html': {'hx_on': 1},
    'dashboards/admin/fragments/user_search_results_htmx.html': {'event_handlers': 1, 'hx_on': 1},
    'dashboards/fragments/action_buttons.html': {'event_handlers': 2},
    'dashboards/fragments/audit_filters.html': {'event_handlers': 1},
    'dashboards/shared/project_tree.html': {'event_handlers': 2},
    'dashboards/user/fragments/user_search_results_htmx.html': {'event_handlers': 1},
    'dashboards/user/partials/project_card.html': {'inline_scripts': 1, 'event_handlers': 3},
    'dashboards/user/partials/user_card.html': {'event_handlers': 1},
    'dashboards/user/resource_details.html': {'style_blocks': 1},
    'dashboards/user/resource_details_disk.html': {'event_handlers': 1},
    'project_members/fragments/add_member_form_htmx.html': {'inline_scripts': 1, 'event_handlers': 1},
}


def scan_templates():
    """Return {relpath: {check: count}} for all current violations."""
    found = {}
    for path in sorted(TEMPLATES_DIR.rglob('*.html')):
        text = HTML_COMMENT_RE.sub('', JINJA_COMMENT_RE.sub('', path.read_text()))
        counts = {name: len(rx.findall(text)) for name, rx in CHECKS.items()}
        counts = {name: n for name, n in counts.items() if n}
        if counts:
            found[str(path.relative_to(TEMPLATES_DIR))] = counts
    return found


def test_no_new_csp_violations():
    found = scan_templates()

    new_debt = {
        path: counts for path, counts in found.items()
        if counts != ALLOWED_VIOLATIONS.get(path)
    }
    gone = {
        path: counts for path, counts in ALLOWED_VIOLATIONS.items()
        if path not in found
    }

    msg = []
    if new_debt:
        msg.append(
            'Templates with CSP-violating patterns beyond the ratchet '
            '(inline <script>, on*= handlers, hx-on:, <style>). Extract to '
            'static JS/CSS (see this test\'s docstring) instead of growing '
            'ALLOWED_VIOLATIONS:')
        msg += [f'  {p}: found {c}, allowed {ALLOWED_VIOLATIONS.get(p)}'
                for p, c in sorted(new_debt.items())]
    if gone:
        msg.append(
            'Templates fixed (or removed) but still listed in '
            'ALLOWED_VIOLATIONS — ratchet them out:')
        msg += [f'  {p}' for p in sorted(gone)]

    assert not msg, '\n' + '\n'.join(msg)
