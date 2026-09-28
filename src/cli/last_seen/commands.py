"""``sam-admin last-seen`` — show a user's ledger rows, or backfill from SAM's charge summaries.

Showing reads only `system_status`; the backfill reads SAM and writes `system_status`.
"""

from __future__ import annotations

import time

from cli.core.base import BaseCommand
from cli.core.output import output_json
from cli.core.utils import EXIT_NOT_FOUND, EXIT_SUCCESS
from cli.last_seen import display


def status_session():
    """A Session on the status DB; ``RuntimeError`` when ``STATUS_DB_*`` is not configured."""
    from sqlalchemy.orm import Session

    from system_status.session import create_status_engine
    engine, _ = create_status_engine()
    return Session(engine)


class LastSeenCommand(BaseCommand):
    """Read or seed ``system_status.user_last_seen``."""

    def execute(self, *, username: str | None = None, backfill: bool = False,
                dry_run: bool = False) -> int:
        try:
            if backfill:
                return self._backfill(dry_run=dry_run)
            return self._show(username)
        except Exception as e:                       # noqa: BLE001
            return self.handle_exception(e)

    def _show(self, username: str) -> int:
        from system_status.queries.last_seen import get_last_seen

        with status_session() as status:
            sources = get_last_seen(status, username)
        payload = {'kind': 'last_seen', 'username': username, 'sources': sources}
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_last_seen(self.ctx, payload)
        return EXIT_SUCCESS if sources else EXIT_NOT_FOUND

    def _backfill(self, *, dry_run: bool) -> int:
        from sam.queries.last_seen_backfill import (
            CHARGE_SUMMARIES, charge_summary_windows, plan_backfill,
        )
        from system_status.queries.last_seen import record_seen

        rows, tables = [], []
        for model in CHARGE_SUMMARIES:
            start = time.monotonic()
            found = charge_summary_windows(self.session, model)
            tables.append({'table': model.__tablename__, 'groups': len(found),
                           'seconds': round(time.monotonic() - start, 1)})
            rows.extend(found)
        plan, machines, skipped = plan_backfill(rows)

        applied = {}
        if not dry_run:
            with status_session() as status:
                for system, users in sorted(plan.items()):
                    applied[system] = record_seen(
                        status, 'pbs', system,
                        ((u, first, last) for u, (first, last) in users.items()))
                status.commit()

        payload = {
            'kind': 'last_seen_backfill',
            'dry_run': dry_run,
            'tables': tables,
            'machines': [{'machine': m, **info} for m, info in sorted(machines.items())],
            'systems': [{'system': s, 'users': len(u), 'applied': applied.get(s)}
                        for s, u in sorted(plan.items())],
            'skipped': skipped,
        }
        if self.ctx.output_format == 'json':
            output_json(payload)
        else:
            display.display_backfill(self.ctx, payload)
        return EXIT_SUCCESS
