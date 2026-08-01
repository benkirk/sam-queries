"""Fragment-family registrar — one panel × one mode = one generated route.

Both navigators expose the same panels through three modes (project /
resource-or-machine / user), which had fanned out into 37 hand-written route
bodies that were all the same four lines:

    ctx = <mode ctx builder>(url_arg)
    fragment_url = url_for('<this endpoint>', <url_param>=url_arg)
    return _render_<panel>(ctx, fragment_url, mode=…, scope_for=…, **panel_kwargs)

This turns that into two small tables. Modelled on the ``CrudSpec`` /
``register_crud`` pair in ``dashboards/admin/crud.py``, and it carries the
same hard rule:

    **A panel needing more than the spec expresses stays a bespoke route.**

Deliberately NOT folded in: the two navigators' ``explore`` pages. Their
rules are irregular (``/<projcode>/directories/explore`` in project mode vs
``/resource/<resource>/explore`` elsewhere) and each assembles a page-level
context the fragments don't have. Bending the spec to cover three routes
would cost more than it saves.

**Endpoint names are the contract.** They are named by ``url_for`` in a dozen
templates, so the generated names must match the hand-written ones exactly:
``{panel.key}{mode.endpoint_suffix}_{panel.noun}``. The route-map parity
snapshot (``tests/unit/test_route_map_parity.py``) is what proves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

from flask import url_for

from webapp.utils.htmx import read_layout


@dataclass(frozen=True)
class ModeSpec:
    """One access mode: its URL shape, its gate, and how it builds a scope."""

    #: ``'project'`` | ``'resource'`` | ``'machine'`` | ``'user'``
    mode: str
    #: URL prefix carrying the mode's parameter, e.g. ``'/<projcode>'``.
    url_prefix: str
    #: Name of that parameter — the view receives it under this name.
    url_param: str
    #: Suffix distinguishing this mode's endpoint names (``''`` for the
    #: default mode, ``'_resource'`` / ``'_machine'`` / ``'_user'``).
    endpoint_suffix: str
    #: Decorators applied outermost-first, e.g.
    #: ``(login_required, require_project_access)``. THIS is the access gate:
    #: an unscoped mode must name its ``VIEW_ALL_*`` permission here.
    decorators: Tuple[Callable, ...]
    #: ``(url_arg) -> ctx dict`` for the fragment templates.
    context: Callable[[Any], Dict[str, Any]]
    #: ``(ctx) -> whatever this navigator's renderers want as their scope
    #: handle`` — opaque to the registrar. fs-scans returns a
    #: ``subpath -> ScanScope`` factory (its panels drill into sub-paths);
    #: jobs leaves it ``None`` because its panels derive a ``JobScope`` from
    #: the ctx they already receive.
    scope_for: Callable[[Dict[str, Any]], Any] = lambda ctx: None
    #: ``(ctx) -> str`` for log lines.
    log_label: Callable[[Dict[str, Any]], str] = lambda ctx: ''
    #: When the decorators resolve the URL arg to an object (as
    #: ``require_project_access`` does), this pulls the raw value back out
    #: for ``url_for``. Identity by default.
    url_value: Callable[[Any], Any] = lambda arg: arg
    #: ``(ctx) -> Response | None``. A non-None result short-circuits the
    #: panel — used by user mode to render the no-identity empty state
    #: rather than running a scan with no owner pin.
    guard: Callable[[Dict[str, Any]], Any] = lambda ctx: None
    #: ``(ctx) -> dict`` of extra render kwargs this mode always supplies
    #: (e.g. user mode's server-side ``forced_owner_uid``).
    render_kwargs: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda ctx: {}


@dataclass(frozen=True)
class PanelSpec:
    """One panel (tab), across whichever modes offer it."""

    #: Endpoint stem — the generated name is
    #: ``{key}{mode.endpoint_suffix}_{noun}``.
    key: str
    #: Rule appended to the mode's prefix. May be ``''`` (the jobs table
    #: lives at the mode prefix itself).
    rule: str
    #: ``(ctx, fragment_url, *, mode, scope_for, log_label, **kwargs)``.
    render: Callable
    #: Modes offering this panel. Empty = all of them. Not every panel
    #: exists in every mode: fs-scans has no user-mode entity rollup (a
    #: single-owner view needs no per-owner breakdown), and jobs has no
    #: user-mode By User (a pie of one).
    modes: Tuple[str, ...] = ()
    #: Constants forwarded to *render* unchanged.
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    #: ``{render_kwarg: sibling_panel_key}`` — resolved to that sibling's
    #: URL **in the same mode**, so a drill-down lands on a panel the
    #: viewer is already authorized for.
    siblings: Mapping[str, str] = field(default_factory=dict)
    #: ``'fragment'`` or ``'page'``.
    noun: str = 'fragment'
    #: ``(ctx) -> dict`` for per-request extras a constant can't express.
    extra: Callable[[Dict[str, Any]], Dict[str, Any]] = None


def endpoint_name(panel: PanelSpec, mode: ModeSpec) -> str:
    """The endpoint a (panel, mode) pair registers under."""
    return f'{panel.key}{mode.endpoint_suffix}_{panel.noun}'


def register_panels(bp, *, modes: Sequence[ModeSpec],
                    panels: Sequence[PanelSpec]) -> None:
    """Register every (panel, mode) combination on *bp*."""
    for panel in panels:
        for mode in modes:
            if panel.modes and mode.mode not in panel.modes:
                continue
            _register_one(bp, panel, mode)


def _register_one(bp, panel: PanelSpec, mode: ModeSpec) -> None:
    endpoint = endpoint_name(panel, mode)

    def view(*args, **url_kwargs):
        # Modes differ in how the URL parameter arrives: an access decorator
        # may resolve it to an ORM object and pass it positionally
        # (``require_project_access`` hands the view a ``project``), while a
        # plain permission gate leaves it as the raw keyword from the rule.
        arg = args[0] if args else url_kwargs[mode.url_param]
        value = mode.url_value(arg)
        ctx = mode.context(arg)

        blocked = mode.guard(ctx)
        if blocked is not None:
            return blocked

        extras = dict(mode.render_kwargs(ctx))
        extras.update({
            name: url_for(f'{bp.name}.{endpoint_name(sibling, mode)}',
                          **{mode.url_param: value})
            for name, sibling in _sibling_specs(panel).items()
        })
        if panel.extra:
            extras.update(panel.extra(ctx))

        return panel.render(
            ctx,
            url_for(f'{bp.name}.{endpoint}', **{mode.url_param: value}),
            mode=mode.mode,
            scope_for=mode.scope_for(ctx),
            log_label=mode.log_label(ctx),
            # Every panel that draws a chart needs the render layout, and this
            # is the one place all 27 jobs/disk-scans fragment routes pass
            # through — so it is resolved once here rather than in each
            # renderer. Panels that draw no chart accept and ignore it.
            layout=read_layout(),
            **panel.kwargs, **extras,
        )

    view.__name__ = endpoint
    view.__qualname__ = endpoint
    view.__doc__ = panel.render.__doc__

    # Applied bottom-up so the listed order reads like the stacked
    # decorators it replaces: `login_required` outermost, the permission or
    # object-resolving gate inside it.
    wrapped = view
    for decorator in reversed(mode.decorators):
        wrapped = decorator(wrapped)

    # `endpoint` is passed explicitly, so routing never depends on the
    # decorators having preserved __name__.
    bp.add_url_rule(f'{mode.url_prefix}{panel.rule}',
                    endpoint=endpoint, view_func=wrapped)


#: Panels are declared before their siblings exist as objects, so
#: ``siblings`` names them by key; this resolves those names once the whole
#: table is built.
_PANELS_BY_KEY: Dict[str, PanelSpec] = {}


def _sibling_specs(panel: PanelSpec) -> Dict[str, PanelSpec]:
    return {name: _PANELS_BY_KEY[key] for name, key in panel.siblings.items()}


def declare_panels(panels: Sequence[PanelSpec]) -> Sequence[PanelSpec]:
    """Record *panels* by key so ``siblings`` can be resolved by name."""
    for p in panels:
        _PANELS_BY_KEY[p.key] = p
    return panels
