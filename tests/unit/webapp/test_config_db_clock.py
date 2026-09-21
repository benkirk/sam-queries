"""The "DB clock" stat on Admin -> Configuration -> Database: the runtime twin
of tests/integration/test_db_timezone.py, for environments pytest never runs in."""

from datetime import datetime, timedelta

from webapp.extensions import db
from webapp.utils import config_inspect


def test_a_matching_clock_is_ok(app):
    with app.app_context():
        clock = config_inspect.clock_skew(db.engine)
    assert clock['ok'] and clock['skew_seconds'] < 300


def test_a_utc_server_against_a_mountain_app_is_flagged(app, monkeypatch):
    class _SixHoursBehind(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now() - timedelta(hours=6)
    monkeypatch.setattr(config_inspect, 'datetime', _SixHoursBehind)
    with app.app_context():
        clock = config_inspect.clock_skew(db.engine)
    assert not clock['ok'] and clock['skew_seconds'] >= 21_000


def test_an_unreadable_clock_is_none_not_a_500():
    assert config_inspect.clock_skew(object()) is None


def test_the_card_shows_it_for_sam_only(auth_client):
    html = auth_client.get('/admin/htmx/configuration').data.decode()
    assert html.count('DB clock') == 1
    assert 's from app' in html
