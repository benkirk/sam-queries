"""FairShare resource_group API (v1).

Wraps the hpc-scheduling-tools ``fsparsetree-mr`` tool in-process: builds a PBS
fairshare ``resource_group`` tree for a machine, applying ``--rollup`` /
``--equalize`` as query params. Unlike the standalone CLI it reads the fstree
payload straight from SAM's DB (``get_fstree_data``) — no HTTP loopback, no API
credentials. This is a NEW, non-legacy blueprint; it does not touch the
legacy-compat ``fstree_access`` endpoints.

``GET /<machine>`` → JSON ``{machine, capacities, rollup, equalize, lines,
warnings}`` — ``capacities`` is the per-machine config SAM actually loaded
(N_cpu/N_gpu/…), surfaced so drift in the baked/ConfigMap capacities.json is
visible on every call. ``?format=text`` returns the raw resource_group as
``text/plain`` (no capacities block).
``?rollup=`` / ``?equalize=`` map to the CLI flags; ``?clear_cache=1`` recomputes.
"""
from flask import Blueprint, Response, abort, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from webapp.utils.rbac import Permission
from webapp.utils.api_auth import login_or_token_required
from webapp.utils.htmx import read_flag
from webapp.extensions import db, cache
from webapp.api.helpers import register_error_handlers
from sam.plugins import HPC_SCHEDULING_TOOLS, PluginUnavailableError
from sam.queries.fstree_access import get_fstree_data

bp = Blueprint('api_fairshare', __name__)
register_error_handlers(bp)


def _plugin_or_503():
    """Return the loaded plugin module, or abort 503 if disabled/uninstalled."""
    if not current_app.config.get('HPC_SCHEDULING_TOOLS_ENABLED', False):
        abort(503, 'fairshare tree feature is disabled (HPC_SCHEDULING_TOOLS_ENABLED)')
    try:
        return HPC_SCHEDULING_TOOLS.load()
    except PluginUnavailableError as exc:
        abort(503, str(exc))


def _resolve_machine_or_400(mod, machine):
    """Map a case-insensitive machine to its exact capacities key, or 400."""
    try:
        keys = {m.lower(): m for m in mod.available_machines()}
    except Exception as exc:            # missing/unreadable capacities.json
        current_app.logger.error('fairshare: capacities unavailable: %s', exc)
        abort(503, 'fairshare machine configuration is unavailable')
    key = keys.get(machine.lower())
    if key is None:
        abort(400, 'machine must be one of %s' % sorted(keys.values()))
    return key


@cache.memoize()
def _compute_tree(machine_key, rollup, equalize_key):
    """Build (lines, warnings) for one (machine, rollup, equalize), memoized.

    Feeds the plugin an in-process fetcher over SAM's DB instead of its HTTP
    client. Runs on a cache miss only; keyed on the args, not the request URL.
    """
    mod = HPC_SCHEDULING_TOOLS.load()

    def fetch(resource):
        data = get_fstree_data(db.session, resource_name=resource)
        if not data or not data.get('facilities'):
            abort(404, f'Resource {resource!r} not found or has no fairshare data')
        return data

    return mod.build_tree(machine_key, rollup, set(equalize_key), fetch=fetch)


@bp.route('/<machine>', methods=['GET'])
@login_or_token_required(Permission.VIEW_ALL_JOB_DATA)
def get_fairshare_tree(machine):
    """Build the fairshare resource_group tree for one machine."""
    mod = _plugin_or_503()
    machine_key = _resolve_machine_or_400(mod, machine)

    fmt = (request.args.get('format') or 'json').lower()
    if fmt not in ('json', 'text'):
        abort(400, "format must be 'json' or 'text'")
    try:
        rollup, tokens = mod.normalize_options(
            request.args.get('rollup', 'none'), request.args.get('equalize', ''))
    except mod.OptionError as exc:
        abort(400, str(exc))
    equalize_key = tuple(sorted(tokens))

    if read_flag(request.args, 'clear_cache'):
        cache.delete_memoized(_compute_tree, machine_key, rollup, equalize_key)

    try:
        lines, warnings = _compute_tree(machine_key, rollup, equalize_key)
    except HTTPException:
        raise                            # 404 from fetch, etc. — pass through
    except SystemExit as exc:
        # The plugin's data-validation paths call die() (SystemExit); never let
        # one take down the worker — surface it as a 500.
        current_app.logger.error('fairshare: plugin exited for %s: %s', machine_key, exc)
        abort(500, 'fairshare tree computation failed')
    except Exception:
        current_app.logger.exception('fairshare: error building tree for %s', machine_key)
        abort(500, 'fairshare tree computation failed')

    if fmt == 'text':
        return Response(''.join(line + '\n' for line in lines), mimetype='text/plain')

    # The constants that produced this tree, surfaced for drift-spotting against
    # the plugin's source capacities.json. machine_key is already validated, so
    # load_config succeeds; guard its die() anyway rather than 500 the response.
    try:
        mcfg, default_days, scale = mod.load_config(machine_key)
        capacities = {**mcfg, 'scale': scale, 'default_duration_days': default_days}
    except SystemExit:
        current_app.logger.error('fairshare: load_config failed for %s', machine_key)
        capacities = None

    return jsonify({
        'machine': machine_key,
        'capacities': capacities,
        'rollup': rollup,
        'equalize': list(equalize_key),
        'lines': lines,
        'warnings': list(mod.format_warnings(warnings)),
    })
