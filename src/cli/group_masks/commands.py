"""``sam-admin masks`` -- the webapp's group-mask writes from a shell (docs/plans/UNIX_GROUP_MASK.md).

The CLI has no permission model: holding the database credential is the authorization,
as for ``sam-admin rbac``. The RBAC grant-subject guard still applies (``--allow-protected``
lifts it), and an adhoc open files NUSD's ticket through the webapp's own routine inside a
borrowed app context, so the ticket levers come from the shell's environment.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional, Sequence

from cli.core.base import BaseCommand
from cli.core.output import refuse_json_write
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS, parse_duration_days
from cli.group_masks import builders, display
from sam.manage.groups import MaskError, check_mask, close_mask, open_masks, resolve_target

DEFAULT_PROJECT_DAYS = 7


def _actor() -> str:
    import getpass
    try:
        return f'cli:{getpass.getuser()}'[:35]
    except Exception:                                # no passwd entry (container)
        return 'cli'


def parse_until(spec: Optional[str], *, now: datetime) -> Optional[datetime]:
    """``7d`` (``parse_duration_days`` forms) or ``YYYY-MM-DD``, as that day's last second."""
    from sam.dates import end_of_day
    if not spec:
        return None
    try:
        day = date.fromisoformat(spec)
    except ValueError:
        day = (now + timedelta(days=parse_duration_days(spec, '--until'))).date()
    return end_of_day(datetime.combine(day, time.min))


class MasksCommand(BaseCommand):
    """One class, one method per verb; ``execute`` dispatches for the base contract."""

    def execute(self, *, verb: str, **kwargs) -> int:
        return getattr(self, verb)(**kwargs)

    def list(self, *, open_only: bool, kind: Optional[str], states: Iterable[str],
             flags: Iterable[str], q: Optional[str]) -> int:
        try:
            return self.emit(builders.build_listing(self.session, open_only=open_only, kind=kind,
                                                    states=states, flags=flags, q=q),
                             display.display_listing)
        except Exception as e:                       # noqa: BLE001
            return self.handle_exception(e)

    def open(self, *, target: str, usernames: Sequence[str], op: str, until: Optional[str],
             note: Optional[str], allow_protected: bool, dry_run: bool) -> int:
        if self.ctx.output_format == 'json' and not dry_run:
            return refuse_json_write('group_masks_open', 'masks open (add --dry-run)')
        try:
            gid = self._gid(target)
            if gid is None:
                return self.not_found('group_masks_open', f'No adhoc group or project named {target!r}',
                                      target=target)
            now = datetime.now()
            resolved = resolve_target(self.session, gid)
            expires_at = parse_until(until, now=now)
            if resolved.kind == 'project' and expires_at is None:
                expires_at = parse_until(f'{DEFAULT_PROJECT_DAYS}d', now=now)
            guards = dict(protected_groups=self._protected_groups(), may_touch_protected=allow_protected)
            if dry_run:
                for name in usernames:
                    check_mask(self.session, gid=gid, username=name, op=op, now=now, **guards)
                return self.emit(builders.build_opened([], resolved, dry_run=True, checked=usernames,
                                                       ticket=None), display.display_opened)
            from sam.manage import management_transaction
            with management_transaction(self.session):
                masks = open_masks(self.session, gid=gid, usernames=list(usernames), op=op,
                                   requested_by=_actor(), note=note, expires_at=expires_at,
                                   now=now, **guards)
            ticket = self._file_ticket(masks, resolved) if resolved.kind == 'adhoc' else None
            return self.emit(builders.build_opened(masks, resolved, dry_run=False, checked=usernames,
                                                   ticket=ticket), display.display_opened)
        except MaskError as e:
            self.ctx.stderr_console.print(f'Refused: {e}', style='bold red')
            return EXIT_ERROR
        except Exception as e:                       # noqa: BLE001
            return self.handle_exception(e)

    def withdraw(self, *, mask_id: int, dry_run: bool) -> int:
        from sam.core.groups import UnixGroupMask
        if self.ctx.output_format == 'json' and not dry_run:
            return refuse_json_write('group_masks_withdraw', 'masks withdraw (add --dry-run)')
        try:
            mask = self.session.get(UnixGroupMask, mask_id)
            if mask is None:
                return self.not_found('group_masks_withdraw', f'No mask #{mask_id}', mask_id=mask_id)
            if not mask.is_active:
                self.ctx.stderr_console.print(f'Mask #{mask_id} is already {mask.resolution}.',
                                              style='bold red')
                return EXIT_ERROR
            if dry_run:
                return self.emit(builders.build_withdrawn(mask, dry_run=True, commented=False),
                                 display.display_withdrawn)
            from sam.manage import management_transaction
            with management_transaction(self.session):
                close_mask(self.session, mask, resolution='withdrawn', by=_actor())
            commented = self._comment_withdrawn(mask) if mask.kind == 'adhoc' else False
            return self.emit(builders.build_withdrawn(mask, dry_run=False, commented=commented),
                             display.display_withdrawn)
        except MaskError as e:
            self.ctx.stderr_console.print(f'Refused: {e}', style='bold red')
            return EXIT_ERROR
        except Exception as e:                       # noqa: BLE001
            return self.handle_exception(e)

    # ------------------------------------------------------------------ #

    def _gid(self, target: str) -> Optional[int]:
        """A gid, an adhoc group name or a projcode."""
        from sam.core.groups import AdhocGroup
        from sam.projects.projects import Project
        if target.isdigit():
            return int(target)
        group = AdhocGroup.get_by_name(self.session, target)
        if group is not None:
            return group.unix_gid
        project = Project.get_by_projcode(self.session, target.upper())
        return project.unix_gid if project is not None else None

    def _protected_groups(self) -> frozenset:
        """The RBAC grant-subject groups: the tables (prod's catalog) plus the code defaults."""
        from sam.security.rbac_catalog import build_catalog
        from sam.security.rbac_defaults import DEFAULT_GRANTS, DEFAULT_ROLES
        from sam.security.samuel_roles import load_catalog
        subjects = set(build_catalog(DEFAULT_ROLES, DEFAULT_GRANTS).group_subjects)
        try:
            subjects |= load_catalog(self.session).group_subjects
        except Exception:                            # noqa: BLE001 - empty or absent tables
            pass
        return frozenset(subjects)

    def _webapp(self):
        """The webapp inside its app context, for its ticket routine (the xras recheck shape)."""
        import contextlib
        import sys
        with contextlib.redirect_stdout(sys.stderr):
            from webapp.run import create_app
            return create_app()

    def _file_ticket(self, masks, target) -> Optional[dict]:
        from webapp.dashboards.admin.group_mask_tickets import send_mask_ticket
        app = self._webapp()
        with app.app_context():
            result = send_mask_ticket(masks, group_name=target.name,
                                      branches=target.group.branch_names(self.session),
                                      requested_by=_actor())
        return None if result is None else {'status': result.status, 'detail': result.detail}

    def _comment_withdrawn(self, mask) -> bool:
        from webapp.dashboards.admin.group_mask_tickets import comment_withdrawn
        app = self._webapp()
        with app.app_context():
            return comment_withdrawn(mask, by=_actor(), session=self.session)
