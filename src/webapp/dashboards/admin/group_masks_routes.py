"""Admin -> Users & Groups -> Group masks: an operator's add/remove over a mirrored unix
group (docs/plans/UNIX_GROUP_MASK.md § 6). The tab lists every mask; the adhoc group card
and the admin project card open the same form pinned to their group. One submit names up
to 20 members and opens them as one batch under one ticket. Reads are ``VIEW_GROUPS``,
writes ``EDIT_GROUPS``; a mask over an RBAC grant-subject group needs ``MANAGE_ROLES`` and
a project mask the project's facility.
"""

from datetime import date, datetime, timedelta

from flask import render_template, request, url_for
from flask_login import current_user, login_required
from marshmallow import ValidationError

from sam.core.groups import PROJECT_MASK_MAX_DAYS, AdhocGroup, UnixGroupMask
from sam.dates import end_of_day
from sam.manage.groups import (MaskError, check_mask, close_mask, open_masks, parse_usernames,
                               resolve_target)
from sam.queries.group_masks import (MASK_FLAGS, MASK_SORTS, MASK_STATES, filter_mask_views,
                                     mask_views, search_mask_targets, sort_mask_views)
from sam.schemas.forms import EditGroupMaskForm, OpenGroupMaskForm, WithdrawGroupMaskForm
from webapp.auth.impersonation import acting_username
from webapp.extensions import db
from webapp.utils.faceted_log import build_facet_strip
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import (modal_triggers, read_active_only, read_flag, read_multi,
                               read_page, read_sort, register_typeahead)
from webapp.utils.rbac import (Permission, active_catalog, has_permission,
                               has_permission_for_facility, require_permission,
                               require_permission_any_facility)

from .blueprint import bp
from .crud import CrudSpec, register_crud
from .group_mask_tickets import comment_withdrawn, send_mask_ticket

_FORM_ID = 'groupMasksFilterForm'
_TARGET_ID = 'groupMasksTableContainer'
_PER_PAGE = 50
FORM = 'dashboards/admin/fragments/group_mask_form_htmx.html'
PENDING = 'dashboards/admin/fragments/group_masks_pending.html'
DEFAULT_PROJECT_DAYS = 7


def _filters(args):
    kind = args.get('kind') or None
    sort = read_sort(args, MASK_SORTS)
    return {
        'kind': kind if kind in ('adhoc', 'project') else None,
        'state': [s for s in read_multi(args, 'state') if s in MASK_STATES],
        'flag': [f for f in read_multi(args, 'flag') if f in MASK_FLAGS],
        'search': (args.get('q') or '').strip() or None,
        'sort_by': sort['sort_by'] or 'created',
        'sort_dir': sort['sort_dir'],
    }


@bp.route('/users/group-masks')
@login_required
@require_permission_any_facility(Permission.VIEW_GROUPS)
def users_group_masks():
    """Page shell; the query string seeds the filter form so links are shareable."""
    return render_template(
        'dashboards/admin/users_group_masks.html',
        user=current_user,
        filters=_filters(request.args),
        active_only=read_flag(request.args, 'active_only', True),
        form_id=_FORM_ID, target_id=_TARGET_ID,
        fragment_url=url_for('admin_dashboard.users_group_masks_table'),
    )


@bp.route('/htmx/users/group-masks')
@login_required
@require_permission_any_facility(Permission.VIEW_GROUPS)
def users_group_masks_table():
    """HTMX fragment: kind and state chips, the sorted page, pagination."""
    filters = _filters(request.args)
    now = datetime.now()
    rows = mask_views(db.session, now=now, open_only=read_active_only(request.args))
    kind_counts, state_counts, flag_counts = {}, {}, {}
    for r in filter_mask_views(rows, q=filters['search']):
        kind_counts[r['kind']] = kind_counts.get(r['kind'], 0) + 1
        state_counts[r['state']] = state_counts.get(r['state'], 0) + 1
        for f in r['flags']:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    rows = sort_mask_views(filter_mask_views(rows, kind=filters['kind'], states=filters['state'],
                                             flags=filters['flag'], q=filters['search']),
                           filters['sort_by'], filters['sort_dir'])
    page_n = read_page(request.args)['n']
    start = (page_n - 1) * _PER_PAGE
    return render_template(
        'dashboards/admin/fragments/group_masks_table.html',
        rows=rows[start:start + _PER_PAGE], total=len(rows), filters=filters,
        kind_facets=build_facet_strip(kind_counts, ('adhoc', 'project')),
        state_facets=build_facet_strip(state_counts, MASK_STATES),
        flag_facets=build_facet_strip(flag_counts, MASK_FLAGS),
        page={'n': page_n, 'per_page': _PER_PAGE},
        sort={'sort_by': filters['sort_by'], 'sort_dir': filters['sort_dir']},
        sortable_columns=MASK_SORTS, form_id=_FORM_ID, target_id=_TARGET_ID,
        fragment_url=url_for('admin_dashboard.users_group_masks_table'),
        can_edit=has_permission(current_user, Permission.EDIT_GROUPS),
    )


@bp.route('/htmx/group-masks/pending')
@login_required
@require_permission_any_facility(Permission.VIEW_GROUPS)
def htmx_group_masks_pending():
    """The open masks on one gid: the list under a card, self-refreshing on reloadGroupMasks."""
    gid = request.args.get('gid', type=int)
    rows = mask_views(db.session, now=datetime.now(), gids=[gid] if gid else [], open_only=True)
    return render_template(PENDING, rows=rows, gid=gid,
                           can_edit=has_permission(current_user, Permission.EDIT_GROUPS))


# --- open -----------------------------------------------------------------------

def _form_context(target=None, username=None, op=None):
    today = date.today()
    return {
        'target': target, 'pinned_username': username, 'pinned_op': op,
        'today': today.isoformat(),
        'default_expiry': (today + timedelta(days=DEFAULT_PROJECT_DAYS)).isoformat(),
        'max_expiry': (today + timedelta(days=PROJECT_MASK_MAX_DAYS)).isoformat(),
    }


def _pinned_target(gid):
    """The card's group, or None when the form is unpinned or the gid is unknown."""
    try:
        return resolve_target(db.session, int(gid)) if gid else None
    except (MaskError, ValueError):
        return None


@bp.route('/htmx/group-mask-form')
@login_required
@require_permission(Permission.EDIT_GROUPS)
def htmx_group_mask_form():
    """The open-mask form; ``gid``, ``username`` and ``op`` pin it from a card."""
    op = request.args.get('op')
    return render_template(FORM, **_form_context(
        _pinned_target(request.args.get('gid')), request.args.get('username') or None,
        op if op in ('add', 'remove') else None))


class _OpenMaskHandler(HtmxFormHandler):
    schema_cls = OpenGroupMaskForm
    template = FORM
    error_prefix = 'The masks were not opened'
    success_message = 'Mask opened.'
    exception_map = ((MaskError, str),)

    def _guard_flags(self):
        return dict(protected_groups=active_catalog().group_subjects,
                    may_touch_protected=has_permission(current_user, Permission.MANAGE_ROLES))

    def clean(self, data):
        """Every guard runs here, per member, so one re-render shows every problem."""
        errors = {}
        gid, target = data.get('unix_gid'), None
        if not gid:
            errors['unix_gid'] = ['Pick a group or a project.']
        else:
            try:
                target = resolve_target(db.session, gid)
            except MaskError as exc:
                errors['unix_gid'] = [str(exc)]
        if target is not None and target.kind == 'project' and not has_permission_for_facility(
                current_user, Permission.EDIT_GROUPS, target.project.facility_name):
            raise FormError(f'{target.name} is outside your facility scope.')
        pinned_one = (data.get('username') or '').strip()
        names, problems = parse_usernames(pinned_one or data.get('usernames') or '')
        expires = data.get('expires_at')
        expires_at = end_of_day(datetime.combine(expires, datetime.min.time())) if expires else None
        if target is not None and target.kind == 'project' and expires_at is None:
            errors['expires_at'] = [f'A project grant needs an end date, at most {PROJECT_MASK_MAX_DAYS} days out.']
        if target is not None:
            for name in names:
                try:
                    check_mask(db.session, gid=gid, username=name, op=data['op'], **self._guard_flags())
                except MaskError as exc:
                    problems.append(str(exc))
        if problems:
            if pinned_one:
                raise FormError(*problems)         # the card's member is a hidden field
            errors['usernames'] = problems
        if errors:
            raise ValidationError(errors)
        self.target = target
        return {'gid': gid, 'usernames': names, 'op': data['op'], 'note': data.get('note'),
                'expires_at': expires_at}

    def perform(self, data):
        self.group_name = self.target.name
        self.branches = self.target.group.branch_names(db.session) if self.target.group else []
        return open_masks(db.session, requested_by=acting_username(), **self._guard_flags(), **data)

    def after_commit(self, masks):
        """NUSD's ticket, for an adhoc batch only; a project grant files nothing."""
        self.ticket = None
        if masks[0].kind == 'adhoc':
            self.ticket = send_mask_ticket(masks, group_name=self.group_name,
                                           branches=self.branches, requested_by=acting_username())

    def context(self):
        form = request.form
        pinned = form.get('pinned')
        if pinned:
            return _form_context(_pinned_target(form.get('unix_gid')), form.get('username') or None,
                                 form.get('op') if pinned == 'op' else None)
        # The picker draws its chip from <name>_display, which the browser never posts.
        data = form.to_dict()
        target = _pinned_target(form.get('unix_gid'))
        if target is not None:
            data['unix_gid_display'] = target.label
        return {**_form_context(), 'form': data}

    def detail(self, masks):
        head = masks[0]
        who = head.username if len(masks) == 1 else f'{len(masks)} members'
        what = f'{head.op} {who} on {self.group_name}'
        ticket = getattr(self, 'ticket', None)
        if ticket is None:
            return what if head.kind == 'project' else f'{what}; no ticket path is configured'
        if ticket.status == 'sent':
            return f'{what}; NUSD ticket {ticket.detail}'
        return f'{what}; ticket {ticket.status}'

    def triggers(self, masks):
        t = modal_triggers('reloadGroupMasks')
        if masks[0].kind == 'adhoc':
            t['reloadGroupCard'] = self.group_name
        return t


@bp.route('/htmx/group-mask-create', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_GROUPS)
def htmx_group_mask_create():
    return _OpenMaskHandler().handle()


# --- edit (expiry and note) ----------------------------------------------------------

def _edit_kwargs(data):
    expires = data.get('expires_at')
    return dict(note=data.get('note') or '',
                expires_at=end_of_day(datetime.combine(expires, datetime.min.time())) if expires else None,
                clear_expiry=not expires)


register_crud(bp, CrudSpec(
    slug='group-mask', name='Mask', model=UnixGroupMask, id_param='mask_id', context_key='mask',
    edit_schema=EditGroupMaskForm, edit_kwargs=_edit_kwargs,
    edit_context=lambda: _form_context(),
    triggers=modal_triggers('reloadGroupMasks'),
    edit_permission=Permission.EDIT_GROUPS, actions=('edit',),
))


# --- withdraw ------------------------------------------------------------------------

class _WithdrawHandler(HtmxFormHandler):
    schema_cls = WithdrawGroupMaskForm
    success_message = 'Mask withdrawn.'
    exception_map = ((MaskError, str),)

    def render_errors(self, errors, field_errors=None):
        return render_template('dashboards/admin/fragments/group_mask_notice.html', errors=errors)

    def perform(self, data):
        return close_mask(db.session, self.mask, resolution='withdrawn', by=acting_username())

    def after_commit(self, result):
        if result.kind == 'adhoc':
            comment_withdrawn(result, by=acting_username())

    def triggers(self, result):
        t = modal_triggers('reloadGroupMasks')
        if result.kind == 'adhoc':
            group = AdhocGroup.get_by_unix_gid(db.session, result.unix_gid)
            if group is not None:
                t['reloadGroupCard'] = group.group_name
        return t


@bp.route('/htmx/group-mask-withdraw/<int:mask_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_GROUPS)
def htmx_group_mask_withdraw(mask_id):
    mask = db.session.get(UnixGroupMask, mask_id)
    if mask is None:
        return '<div class="alert alert-danger">Mask not found</div>', 404
    return _WithdrawHandler(mask=mask).handle()


# --- pickers ---------------------------------------------------------------------------

def _search_members(q, active_only):
    from sam.queries.users import search_users_by_pattern
    return search_users_by_pattern(db.session, q, limit=15, active_only=True)


register_typeahead(
    bp, rule='/htmx/search/mask-targets', endpoint='htmx_search_mask_targets',
    permission=Permission.EDIT_GROUPS, search=lambda q, _a: search_mask_targets(db.session, q),
    template='dashboards/admin/fragments/mask_target_results_htmx.html', ctx_key='targets',
)

register_typeahead(
    bp, rule='/htmx/search/mask-members', endpoint='htmx_search_mask_members',
    permission=Permission.EDIT_GROUPS, search=_search_members,
    template='dashboards/admin/fragments/mask_member_results_htmx.html', ctx_key='users',
)
