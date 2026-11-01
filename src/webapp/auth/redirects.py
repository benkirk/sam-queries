"""The ``next`` target a login returns to: built and checked in one place."""

from urllib.parse import urlparse

from flask import request, url_for


def is_safe_redirect(target):
    """A same-site path only: no scheme or host, and no ``//`` or ``/\\`` that a browser reads as one."""
    if not target or not target.startswith('/') or target[1:2] in ('/', '\\'):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def current_target():
    """The page the visitor was on, or None. For htmx it is the page (HX-Current-URL), not the fragment."""
    if request.blueprint == 'auth':
        return None   # /auth/logout as next signs the visitor straight out again
    if request.headers.get('HX-Request'):
        page = urlparse(request.headers.get('HX-Current-URL', ''))
        target = page.path + (f'?{page.query}' if page.query else '')
    elif request.method == 'GET':
        target = request.full_path.rstrip('?')
    else:
        return None
    return target if is_safe_redirect(target) else None


def login_url():
    """The login page, carrying the current page as ``next`` when there is one."""
    return url_for('auth.login', next=current_target())
