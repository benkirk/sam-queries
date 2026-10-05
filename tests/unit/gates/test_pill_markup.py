"""Toggle pills have one markup: ``btn-outline-secondary``, plus ``active``.

``dashboard.css`` paints a ``.btn-group`` pill from that pair alone. A second
spelling (``btn-secondary active``) once painted the same way through a
duplicate rule, and JavaScript that toggles only ``active`` could not tell
which base class a pill started with.
"""

from _paths import REPO_ROOT

WEBAPP = REPO_ROOT / 'src' / 'webapp'


def test_no_pill_is_spelled_btn_secondary_active():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for pattern in ('templates/**/*.html', 'static/js/*.js')
        for path in WEBAPP.glob(pattern)
        if 'btn-secondary active' in path.read_text()
        or "'btn-secondary', 'active'" in path.read_text()
    ]
    assert not offenders, offenders
