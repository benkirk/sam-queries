"""Record one recommender run's notes on XRAS requests (``POST /api/v1/xras/notes``)."""
from sam.integration.xras import XrasRequestNote


def record_run_notes(session, *, source, run_id, generated_at, ruleset_version, notes,
                     created_by):
    """Upsert one note per request for this run; returns the request numbers written."""
    written = []
    for note in notes:
        XrasRequestNote.set(
            session, request_number=note['request_number'], source=source,
            created_by=created_by, run_id=run_id, generated_at=generated_at,
            ruleset_version=ruleset_version, decision=note['decision'],
            request_id=note.get('request_id'), action_id=note.get('action_id'),
            button=note.get('button'), end_date=note.get('end_date'),
            values={'hours_by_resource': note.get('hours_by_resource') or {},
                    'requested_hours': note.get('requested_hours') or {},
                    'links': note.get('links') or {}},
            admin_comment=note.get('admin_comment'), rationale=note.get('rationale'),
            pi_history=note.get('pi_history'), findings=note.get('findings') or [])
        written.append(note['request_number'])
    return written
