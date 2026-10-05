"""Every facet chip must have a control to write into.

A chip (``data-action="set-filter-submit"``) writes its value into the control
named ``data-field`` inside the form ``data-form-id``. When that control is
missing the click does nothing and nothing reaches the console. Two checks:
rendered chips resolve on every chip surface, and each in-memory ``FacetSet``
has a control per dimension whether or not its card has rows to draw chips for.
"""

from html.parser import HTMLParser

import pytest

#: (page that owns the forms, fragments htmx loads into it).
SURFACES = [
    ('/allocations/xras', ['/allocations/xras_pending_fragment',
                           '/allocations/xras_accounts_fragment',
                           '/allocations/xras_remediations',
                           '/allocations/xras_fragment']),
    ('/admin/htmx/notifications', ['/admin/htmx/notifications/log']),
    ('/admin/htmx/tasks', ['/admin/htmx/tasks/log']),
    ('/admin/account-requests', ['/admin/account-requests/fragment']),
    ('/admin/users/last-seen', ['/admin/htmx/users/last-seen']),
    ('/admin/organizations/mnemonics', ['/admin/htmx/mnemonic-codes-table']),
]

#: Draws chips only for values its rows carry, and the snapshot has no rows.
#: Its dimensions are still covered by the FacetSet check below.
_NEEDS_ROWS = {'/admin/account-requests'}

_CONTROLS = {'input', 'select', 'textarea', 'button'}


class _Forms(HTMLParser):
    """Collects ``{form id: control names}`` and ``(form id, field)`` per chip."""

    def __init__(self):
        super().__init__()
        self.forms = {}
        self.chips = set()
        self._open = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self._open = attrs.get('id')
            self.forms.setdefault(self._open, set())
        elif tag in _CONTROLS and attrs.get('name'):
            # `form=` binds a control to a form it does not sit inside.
            owner = attrs.get('form') or self._open
            self.forms.setdefault(owner, set()).add(attrs['name'])
        if attrs.get('data-action') == 'set-filter-submit':
            self.chips.add((attrs.get('data-form-id'), attrs.get('data-field')))

    def handle_endtag(self, tag):
        if tag == 'form':
            self._open = None


def _parse(client, urls):
    parser = _Forms()
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, f'{url}: {response.status_code}'
        parser.feed(response.get_data(as_text=True))
    return parser


@pytest.mark.parametrize('page, fragments', SURFACES, ids=[s[0] for s in SURFACES])
def test_every_chip_writes_into_a_real_control(auth_client, page, fragments):
    parsed = _parse(auth_client, [page, *fragments])
    if page not in _NEEDS_ROWS:
        assert parsed.chips, f'{page}: no chips rendered, so this checked nothing'
    for form_id, field in sorted(parsed.chips):
        assert form_id in parsed.forms, f'{page}: no form #{form_id}'
        assert field in parsed.forms[form_id], (
            f'{page}: #{form_id} has no control named {field!r}; '
            f'its chips would do nothing')


def _facet_sets():
    from webapp.dashboards.admin.account_requests_routes import _FACETS, _FORM_ID
    from webapp.dashboards.allocations.xras import _shared, remediation
    return [
        ('/admin/account-requests', _FORM_ID, _FACETS),
        ('/allocations/xras', _shared._XRAS_ACTIVITY_FORM_ID, _shared.ACTIVITY_FACETS),
        ('/allocations/xras', _shared._ACCOUNTS_FORM_ID, _shared.ACCOUNT_FACETS),
        ('/allocations/xras', remediation._REMEDIATION_FORM_ID,
         _shared.REMEDIATION_FACETS),
    ]


def test_every_facet_set_dimension_has_a_control(auth_client):
    for page, form_id, facets in _facet_sets():
        forms = _parse(auth_client, [page]).forms
        assert form_id in forms, f'{page}: no form #{form_id}'
        missing = set(facets.names) - forms[form_id]
        assert not missing, f'{page}: #{form_id} lacks {sorted(missing)}'
