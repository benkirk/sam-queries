"""Rendered-markup conventions for the admin CRUD tables.

Each admin card is rendered for real and parsed, so a rule holds for every row
the data produces, not just the ones a template author pictured:

- every table is ``align-middle``;
- every body row spans exactly the header's width (a short ``colspan`` left
  the Organizations empty state one column narrow);
- an icon-only button names its verb in ``aria-label``;
- a retired row is tagged, not ``opacity-50`` (which also faded its buttons).

Design record: docs/plans/ADMIN_TABLE_POLISH.md.
"""
from html.parser import HTMLParser

import pytest

pytestmark = pytest.mark.usefixtures('session')

_VOID = {'area', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr'}

CARDS = [
    '/admin/htmx/resources',
    '/admin/htmx/organizations-card',
    '/admin/htmx/institutions-fragment',
    '/admin/htmx/institutions-fragment?show_users_projects=1',
    '/admin/htmx/contracts-table',
    '/admin/htmx/facilities',
]


class _Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children, self.text = [], []

    def find_all(self, tag):
        for child in self.children:
            if child.tag == tag:
                yield child
            yield from child.find_all(tag)

    def kids(self, *tags):
        return [c for c in self.children if c.tag in tags]

    def all_text(self):
        return ''.join(self.text) + ''.join(c.all_text() for c in self.children)

    @property
    def classes(self):
        return (self.attrs.get('class') or '').split()


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node('#root', {})

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.text.append(data)


def _parse(html):
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root


def _span(row):
    return sum(int(c.attrs.get('colspan') or 1) for c in row.kids('td', 'th'))


def _render(auth_client, url, active_only):
    sep = '&' if '?' in url else '?'
    resp = auth_client.get(f'{url}{sep}active_only={active_only}')
    assert resp.status_code == 200, url
    return resp.get_data(as_text=True)


@pytest.fixture(params=[(u, a) for u in CARDS for a in ('1', '0')],
                ids=lambda p: f'{p[0]}:active_only={p[1]}')
def card(request, auth_client):
    url, active_only = request.param
    html = _render(auth_client, url, active_only)
    return url, html, _parse(html)


def test_tables_are_align_middle(card):
    url, _, root = card
    tables = [t for t in root.find_all('table') if 'table' in t.classes]
    assert tables, f'{url}: no tables rendered'
    bad = [t.attrs.get('id') or t.classes for t in tables if 'align-middle' not in t.classes]
    assert not bad, f'{url}: tables without align-middle: {bad}'


def test_rows_span_the_header(card):
    url, _, root = card
    problems = []
    for table in root.find_all('table'):
        head = table.kids('thead')
        if not head or not head[0].kids('tr'):
            continue
        width = _span(head[0].kids('tr')[0])
        for section in table.kids('tbody', 'tfoot'):
            for row in section.kids('tr'):
                if _span(row) != width:
                    snippet = ' '.join(row.all_text().split())[:60]
                    problems.append(f'{_span(row)} of {width}: {snippet!r}')
    assert not problems, f'{url}: rows that do not span the header:\n  ' + '\n  '.join(problems[:10])


def test_icon_only_buttons_are_labelled(card):
    url, _, root = card
    unlabelled = [
        b.attrs.get('title') or b.attrs.get('hx-get') or b.attrs.get('hx-delete') or b.attrs.get('hx-post')
        for b in root.find_all('button')
        if not b.all_text().strip() and not b.attrs.get('aria-label')
    ]
    assert not unlabelled, f'{url}: icon-only buttons without aria-label: {unlabelled[:10]}'


def test_retired_rows_are_tagged_not_faded(card):
    url, html, _ = card
    assert 'opacity-50' not in html, f'{url}: use .row-inactive + state_tag, not opacity-50'
