"""Component gallery route — one page, no DB, no caching (reflects live edits)."""

from flask import render_template
from flask_login import login_required

from webapp.utils.htmx import read_layout
from . import bp
from .specimens import gallery_context


@bp.route('/')
@login_required
def index():
    """The gallery. `theme` is global; `layout` is threaded like every axis route."""
    return render_template(
        'dashboards/gallery/index.html',
        layout=read_layout(),
        **gallery_context(),
    )
