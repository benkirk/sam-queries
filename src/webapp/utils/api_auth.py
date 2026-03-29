"""
API key authentication for machine-to-machine routes (e.g., HPC status collectors).

Validates HTTP Basic Auth credentials against bcrypt hashes stored in
app.config['API_KEYS'] = {'username': '$2b$12$...hash...'}.

Usage:
    @bp.route('/derecho', methods=['POST'])
    @api_key_required
    def ingest_derecho():
        ...
"""

import bcrypt
from functools import wraps
from flask import request, jsonify, current_app, g


def api_key_required(f):
    """
    Decorator: requires valid HTTP Basic Auth API key credentials.

    Reads API_KEYS from app config (dict of username -> bcrypt hash).
    Returns 401 + WWW-Authenticate header on auth failure.
    Stores authenticated username in g.api_key_user for logging.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not auth.username or not auth.password:
            return (
                jsonify({'error': 'Authentication required'}),
                401,
                {'WWW-Authenticate': 'Basic realm="SAM API"'},
            )

        api_keys = current_app.config.get('API_KEYS', {})
        stored_hash = api_keys.get(auth.username)

        if not stored_hash:
            return (
                jsonify({'error': 'Invalid credentials'}),
                401,
                {'WWW-Authenticate': 'Basic realm="SAM API"'},
            )

        # bcrypt.checkpw is timing-safe
        try:
            valid = bcrypt.checkpw(
                auth.password.encode('utf-8'),
                stored_hash.encode('utf-8'),
            )
        except Exception:
            valid = False

        if not valid:
            return (
                jsonify({'error': 'Invalid credentials'}),
                401,
                {'WWW-Authenticate': 'Basic realm="SAM API"'},
            )

        g.api_key_user = auth.username  # available to view functions for logging
        return f(*args, **kwargs)

    return decorated_function
