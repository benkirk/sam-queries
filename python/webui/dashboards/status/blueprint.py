"""
System Status dashboard blueprint.
"""

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from datetime import datetime, timedelta

from webui.extensions import db

bp = Blueprint('status_dashboard', __name__, url_prefix='/status')


@bp.route('/')
@login_required
def index():
    """
    Main system status dashboard landing page.
    """

    return render_template(
        'dashboards/status/dashboard.html',
        user=current_user,
    )
