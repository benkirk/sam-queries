"""scripts/ui_snapshots.py --redact: names and emails never reach a production screenshot.

A browser fact: the swap runs in the page, over text, attributes, SVG and content htmx adds later.
Protocol: docs/plans/SAMUEL_PROD_SCREENSHOTS_HANDOFF.md.
"""
import importlib.util

from conftest import REPO_ROOT

_spec = importlib.util.spec_from_file_location('ui_snapshots', REPO_ROOT / 'scripts' / 'ui_snapshots.py')
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)

PAGE = """<html><head><title>jdoe at work</title></head><body><table><tr>
<td class="user">Jane Doe (jdoe)</td><td>jane.doe@ucar.edu</td>
<td><a href="mailto:jane.doe@ucar.edu" title="Jane Doe">mail</a></td>
<td>/glade/u/home/jdoe/work</td><td>jdoes</td></tr></table>
<svg><g><title>Jane Doe: 12 hours</title><text>jdoe</text></g></svg><div id="late"></div></body></html>"""


def test_redaction_covers_text_attributes_svg_and_late_content(page):
    page.add_init_script(snap.REDACT_JS)
    page.goto('about:blank')   # an init script runs from the next navigation on
    page.set_content(PAGE)
    page.evaluate('s => window.__samRedact.select(s)', ['td.user'])
    page.evaluate("document.getElementById('late').innerHTML = '<span title=\"bob@x.org\">late jdoe</span>'")
    page.wait_for_function("!document.getElementById('late').textContent.includes('jdoe ')"
                           " && !document.getElementById('late').textContent.endsWith('jdoe')")
    html = page.evaluate('document.documentElement.outerHTML')
    for real in ('Jane Doe', 'jane.doe@', 'bob@x.org', '/jdoe/', '>jdoe<', 'jdoe at work'):
        assert real not in html, real
    assert 'jdoes' in html   # a longer word that merely starts with a name is left alone
    assert set(page.evaluate('window.__samRedact.originals()')) >= {'Jane Doe', 'jdoe', 'jane.doe@ucar.edu'}
