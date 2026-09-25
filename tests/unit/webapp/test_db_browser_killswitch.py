"""DB_BROWSER_ENABLED: on by default everywhere; off unmounts the blueprint and its nav link."""
import os

import pytest


@pytest.fixture(scope='module')
def browser_disabled_app(test_db_url, status_db_url):
    from webapp.run import create_app
    return create_app(config_overrides={
        'SQLALCHEMY_DATABASE_URI': test_db_url,
        'SQLALCHEMY_BINDS': {'system_status': status_db_url},
        'DB_BROWSER_ENABLED': False,
    })


def test_off_unmounts_the_blueprint(browser_disabled_app):
    assert 'db_browser' not in browser_disabled_app.blueprints
    assert not [r for r in browser_disabled_app.url_map.iter_rules()
                if r.endpoint.startswith('db_browser.')]


def test_off_hides_the_nav_item(browser_disabled_app, session):
    from sam import User
    client = browser_disabled_app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(User.get_by_username(session, 'benkirk').user_id)
        sess['_fresh'] = True
    html = client.get('/admin/configuration').get_data(as_text=True)
    assert 'fa-database' not in html


def test_on_shows_the_nav_item(auth_client):
    assert 'fa-database' in auth_client.get('/admin/configuration').get_data(as_text=True)


def test_every_config_class_defaults_on():
    if 'DB_BROWSER_ENABLED' in os.environ:
        pytest.skip('environment overrides the default')
    from webapp.config import DevelopmentConfig, ProductionConfig, TestingConfig
    assert ProductionConfig.DB_BROWSER_ENABLED is True
    assert DevelopmentConfig.DB_BROWSER_ENABLED is True
    assert TestingConfig.DB_BROWSER_ENABLED is True
