"""Sample fixtures for the component gallery — plain data, no DB.

`gallery_context()` returns everything the template needs. The context vars
`form` / `field_errors` / `errors` / `sort` / `sortable_columns` /
`fragment_url` / `target_id` / `form_id` must be top-level render kwargs: the
gallery imports form_fields.html and sort_link.html `with context`, which
snapshots the context at import time, so a `{% with %}` in the template would
not reach those macros.
"""

import re
from datetime import datetime
from pathlib import Path

# Section index for the in-page nav (kept in step with index.html by hand).
SECTIONS = [
    {'id': 'badges', 'title': 'Badges & status'},
    {'id': 'help', 'title': 'Help & disclosure'},
    {'id': 'modals', 'title': 'Modals & forms'},
    {'id': 'plugin', 'title': 'Plugin states'},
    {'id': 'form-fields', 'title': 'Form fields'},
    {'id': 'search', 'title': 'Search & toggles'},
    {'id': 'tables', 'title': 'Tables & pagination'},
    {'id': 'facets', 'title': 'Facets & pills'},
    {'id': 'ranges', 'title': 'Range & date pickers'},
    {'id': 'filters', 'title': 'Filter panels (layout-aware)'},
    {'id': 'people', 'title': 'People & contracts'},
    {'id': 'openers', 'title': 'Inert modal openers'},
    {'id': 'icons', 'title': 'Icons'},
    {'id': 'skipped', 'title': 'Needs live context (note-and-skip)'},
]

_WEBAPP = Path(__file__).resolve().parents[2]

# FA class tokens that are styles/utilities, not glyph names.
_FA_NONGLYPH = {
    'fa', 'fas', 'far', 'fab', 'fa-solid', 'fa-regular', 'fa-brands',
    'fa-fw', 'fa-ul', 'fa-li', 'fa-border', 'fa-pull-left', 'fa-pull-right',
    'fa-spin', 'fa-pulse', 'fa-spin-pulse', 'fa-spin-reverse', 'fa-beat',
    'fa-fade', 'fa-beat-fade', 'fa-bounce', 'fa-flip', 'fa-shake', 'fa-inverse',
    'fa-stack', 'fa-stack-1x', 'fa-stack-2x', 'fa-width-auto',
    'fa-2xs', 'fa-xs', 'fa-sm', 'fa-lg', 'fa-xl', 'fa-2xl',
    'fa-rotate-90', 'fa-rotate-180', 'fa-rotate-270',
    'fa-flip-horizontal', 'fa-flip-vertical', 'fa-flip-both',
    # Truncation artifact: `fa-caret-{{ 'up'/'down' }}` (sort_link.html) is
    # captured as a bare `fa-caret`, which is not a real glyph.
    'fa-caret',
} | {f'fa-{n}x' for n in range(1, 11)}

_FA_TOKEN = re.compile(r'fa-[a-z0-9]+(?:-[a-z0-9]+)*')


def used_fa_icons():
    """Sorted, deduped fa-* glyph names referenced across templates/js/py.

    Dev-only: scanned live (the gallery does not cache) so the icon-inventory
    panel always mirrors the tree — a blank cell on a Font Awesome bump is a
    renamed/removed glyph.
    """
    seen = set()
    for pattern in ('templates/**/*.html', 'static/js/**/*.js', '**/*.py'):
        for f in _WEBAPP.glob(pattern):
            if 'vendor/' in f.as_posix() or f.name.endswith('.min.js'):
                continue
            try:
                text = f.read_text(encoding='utf-8')
            except OSError:
                continue
            seen.update(t for t in _FA_TOKEN.findall(text) if t not in _FA_NONGLYPH)
    return sorted(seen)

# Full status vocabulary + one unknown to show the bg-secondary fallback.
STATUS_STATES = [
    'active', 'inactive', 'locked', 'expired', 'open-ended', 'received',
    'processed', 'manual', 'failed', 'rechecked', 'unmapped', 'sent', 'queued',
    'suppressed', 'redirected', 'running', 'succeeded', 'partial', 'skipped',
    'frobnicated',
]

SAMPLE_USERS = [
    {'display_name': 'Ada Lovelace', 'first_name': 'Ada', 'last_name': 'Lovelace',
     'username': 'alovelace', 'primary_email': 'ada@example.edu',
     'active': True, 'locked': False, 'is_active': True},
    {'display_name': 'Grace Hopper', 'first_name': 'Grace', 'last_name': 'Hopper',
     'username': 'ghopper', 'primary_email': 'grace@example.edu',
     'active': True, 'locked': True, 'is_active': True},
    {'display_name': 'Alan Turing', 'first_name': 'Alan', 'last_name': 'Turing',
     'username': 'aturing', 'primary_email': None,
     'active': False, 'locked': False, 'is_active': False},
]

PERSON = {
    'firstName': 'Katherine', 'middleName': 'G.', 'lastName': 'Johnson',
    'email': 'kjohnson@example.edu', 'phone': '+1 303 555 0111',
    'organization': 'Example University', 'academicStatus': 'Faculty',
    'residenceCountry': 'United States', 'orcid': '0000-0002-1825-0097',
}

# contract_status_badge reads .is_active / .is_future (Jinja attr->item fallback).
CONTRACTS = [
    {'label': 'Active', 'is_active': True, 'is_future': False},
    {'label': 'Not started', 'is_active': False, 'is_future': True},
    {'label': 'Expired', 'is_active': False, 'is_future': False},
]

FACET_VALUES = [
    {'value': 'received', 'count': 128},
    {'value': 'processed', 'count': 74},
    {'value': 'manual', 'count': 12},
    {'value': 'failed', 'count': 0},
]

# age_band_range bands: 'label' + the two field keys the caller wires.
AGE_BANDS = [
    {'label': '< 30 days', 'start_date': '2026-07-25', 'end_date': ''},
    {'label': '30–90 days', 'start_date': '2026-05-26', 'end_date': '2026-07-25'},
    {'label': '90–365 days', 'start_date': '2025-08-24', 'end_date': '2026-05-26'},
    {'label': '> 1 year', 'start_date': '', 'end_date': '2025-08-24'},
]

# ladder_range fields: numeric node-count example (uncrossed thumbs).
NODE_BANDS = [
    {'label': '1', 'min_nodes': 1, 'max_nodes': 1},
    {'label': '2–8', 'min_nodes': 2, 'max_nodes': 8},
    {'label': '9–64', 'min_nodes': 9, 'max_nodes': 64},
    {'label': '> 64', 'min_nodes': 65, 'max_nodes': None},
]
NODE_FIELDS = [
    {'key': 'min', 'name': 'min_nodes', 'thumb': 'lo', 'type': 'number',
     'sublabel': 'Min', 'title': 'Minimum nodes', 'value': '2', 'min': 1, 'step': 1},
    {'key': 'max', 'name': 'max_nodes', 'thumb': 'hi', 'type': 'number',
     'sublabel': 'Max', 'title': 'Maximum nodes', 'value': '64', 'min': 1, 'step': 1},
]


def gallery_context():
    """Everything the gallery template renders against."""
    return {
        'sections': SECTIONS,
        'used_icons': used_fa_icons(),

        # with-context snapshot vars (form_fields.html, sort_link.html)
        'form': {},
        'field_errors': {'demo_bad': ['This value is required.']},
        'errors': ['Example: end date must be after start date.'],
        'sortable_columns': ['name', 'created', 'size'],
        'sort': {'sort_by': 'name', 'sort_dir': 'asc'},
        'fragment_url': '#',
        'target_id': 'gallerySortTarget',
        'form_id': 'gallerySortForm',

        # call-time fixtures
        'status_states': STATUS_STATES,
        'sample_users': SAMPLE_USERS,
        'person': PERSON,
        'contracts': CONTRACTS,
        'facet_values': FACET_VALUES,
        'age_bands': AGE_BANDS,
        'node_bands': NODE_BANDS,
        'node_fields': NODE_FIELDS,
        'pill_windows': [(7, '7d'), (30, '30d'), (90, '90d'), (365, '1yr')],
        'pagination_page': {'n': 3, 'per_page': 25},
        'pagination_total': 137,
        'trp_start': datetime(2026, 8, 1, 9, 0),
        'trp_end': datetime(2026, 8, 8, 9, 0),
        'audit_resources': ['Derecho', 'Casper', 'Campaign Store'],
        'xras_statuses': ['received', 'processed', 'manual', 'failed'],
        'xras_action_types': ['New', 'Renewal', 'Supplement', 'Transfer'],
        'facilities': ['UNIV', 'WNA', 'NCAR'],

        # a user/program object for the linking (inert opener) specimens
        'link_user': {'username': 'alovelace', 'display_name': 'Ada Lovelace'},
        'nsf_program': {'nsf_program_id': 1234, 'nsf_program_name': 'Atmospheric Sciences'},
    }
