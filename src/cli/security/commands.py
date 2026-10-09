"""``sam-admin rbac`` -- seed, inspect and repair the samuel_role_* catalog.

Always reads the tables, whatever ``RBAC_SOURCE`` the webapp runs with: this
is the break-glass path when the web page cannot be reached.
"""

from __future__ import annotations

from typing import Optional

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS
from cli.security import display
from sam.security import rbac_reports as builders


class RbacCommand(BaseCommand):
    """One class, one method per mode."""

    def execute(self, *, seed: bool = False, seed_keys: bool = False, keys: bool = False,
                effective: Optional[str] = None, diff: bool = False,
                grant: Optional[str] = None, revoke: Optional[int] = None,
                role: Optional[str] = None, permission: Optional[str] = None,
                facility: Optional[str] = None, note: Optional[str] = None,
                include_revoked: bool = False) -> int:
        try:
            if seed:
                return self._seed()
            if seed_keys:
                return self._seed_keys()
            if keys:
                return self._emit(builders.build_keys(self.session), display.display_keys)
            if effective:
                return self._effective(effective)
            if diff:
                return self._emit(builders.build_diff(self.session), display.display_diff)
            if grant:
                return self._grant(grant, role=role, permission=permission,
                                   facility=facility, note=note)
            if revoke is not None:
                return self._revoke(revoke)
            return self._emit(builders.build_listing(self.session, include_revoked=include_revoked),
                              display.display_listing)
        except Exception as e:                       # noqa: BLE001
            return self.handle_exception(e)

    def _emit(self, payload: dict, renderer) -> int:
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            renderer(self.ctx, payload)
        return EXIT_SUCCESS

    def _actor(self) -> str:
        import getpass
        return f'cli:{getpass.getuser()}'[:35]

    def _seed(self) -> int:
        from sam.manage import management_transaction
        from sam.security.samuel_roles import SamuelRole, seed_defaults
        with management_transaction(self.session):
            n = seed_defaults(self.session, by=self._actor())
        if n == 0:
            have = self.session.query(SamuelRole).count()
            self.console.print(f'Already seeded: {have} role(s) present; nothing written.')
        else:
            self.console.print(f'Seeded {n} role(s) and the default grants.')
        return EXIT_SUCCESS

    def _seed_keys(self) -> int:
        """Grant ``api_legacy`` to every enabled key that holds no grant yet."""
        from sam.manage import management_transaction
        from sam.security.samuel_roles import SamuelRole, SamuelRoleGrant
        legacy = SamuelRole.get_by_name(self.session, 'api_legacy')
        if legacy is None:
            self.console.print('No api_legacy role: run --seed first.', style='bold red')
            return EXIT_ERROR
        payload = builders.build_keys(self.session)
        with management_transaction(self.session):
            for name in payload['ungranted']:
                SamuelRoleGrant.create(self.session, subject_type='apikey', subject_name=name,
                                       by=self._actor(), role=legacy,
                                       note='seed-keys: what every key could reach before enforcement')
        for k in payload['keys']:
            state = 'granted api_legacy' if k['name'] in payload['ungranted'] else 'kept as is'
            self.console.print(f'{k["name"]:<20} {k["source"]:<7} {state}')
        return EXIT_SUCCESS

    def _effective(self, subject: str) -> int:
        payload = builders.build_effective(self.session, subject)
        if not payload['grants']:
            self.ctx.message_console.print(
                f'{payload["subject_type"]}:{payload["subject_name"]} holds no grant.')
            if self.ctx.output_format == 'json':
                output_json(payload)
            return EXIT_NOT_FOUND
        return self._emit(payload, display.display_effective)

    def _grant(self, subject: str, *, role, permission, facility, note) -> int:
        from sam.manage import management_transaction
        from sam.security.permissions import Permission
        from sam.security.samuel_roles import SamuelRole, SamuelRoleGrant
        subject_type, _, name = subject.partition(':')
        if not name:
            self.console.print('Subject must be user:NAME, group:NAME or apikey:NAME',
                               style='bold red')
            return EXIT_ERROR
        if bool(role) == bool(permission):
            self.console.print('Give exactly one of --role, --permission', style='bold red')
            return EXIT_ERROR
        role_row = perm = None
        if role:
            role_row = SamuelRole.get_by_name(self.session, role)
            if role_row is None or not role_row.active:
                self.console.print(f'No active role {role!r}', style='bold red')
                return EXIT_NOT_FOUND
        else:
            try:
                perm = Permission(permission)
            except ValueError:
                self.console.print(f'No permission {permission!r}', style='bold red')
                return EXIT_NOT_FOUND
        with management_transaction(self.session):
            row = SamuelRoleGrant.create(self.session, subject_type=subject_type,
                                         subject_name=name, by=self._actor(), role=role_row,
                                         permission=perm, facility_name=facility, note=note)
        self.console.print(f'Granted #{row.samuel_role_grant_id}: {row!r}')
        return EXIT_SUCCESS

    def _revoke(self, grant_id: int) -> int:
        from sam.manage import management_transaction
        from sam.security.samuel_roles import SamuelRoleGrant
        row = self.session.get(SamuelRoleGrant, grant_id)
        if row is None:
            self.console.print(f'No grant #{grant_id}', style='bold red')
            return EXIT_NOT_FOUND
        with management_transaction(self.session):
            row.revoke(by=self._actor())
        self.console.print(f'Revoked #{grant_id}: {row!r}')
        return EXIT_SUCCESS
