"""The approver's note on an XRAS action, read from the reports feed.

``adminComments`` exists only on the outbound reports line, never on the
incoming ``POST /actions`` body, keyed by the same ``actionId`` the POST
carried. Fail-open by contract: this decorates a handoff notice, so every
failure is ``None`` plus one log line and never blocks a send.
"""
import logging
import re
from typing import Any, Iterable, Mapping, Optional

from sam.integration.xras_api.base import XrasSourceUnavailable
from sam.integration.xras_api.client import XrasApiClient

logger = logging.getLogger(__name__)

_BLANK_RUN = re.compile(r'\n{3,}')


def normalize_comment(text: Any) -> Optional[str]:
    """Trimmed, LF-only, at most one blank line in a row; ``None`` when blank."""
    if not isinstance(text, str):
        return None
    cleaned = _BLANK_RUN.sub('\n\n', text.replace('\r\n', '\n').replace('\r', '\n'))
    cleaned = cleaned.strip()
    return cleaned or None


def find_action(family: Iterable[Mapping[str, Any]], action_id: Any
                ) -> Optional[Mapping[str, Any]]:
    """The ``actions[]`` entry carrying *action_id* across every line of a family."""
    wanted = str(action_id)
    for line in family:
        if not isinstance(line, Mapping):
            continue
        for action in line.get('actions') or ():
            if isinstance(action, Mapping) and str(action.get('actionId')) == wanted:
                return action
    return None


def approver_comment(client: XrasApiClient, projcode: Optional[str],
                     action_id: Any) -> Optional[str]:
    """``adminComments`` for one action of *projcode*'s request family, or ``None``."""
    if not projcode or action_id is None:
        return None
    try:
        family = client.get_request_family_by_number(projcode)
    except XrasSourceUnavailable as exc:
        logger.warning('approver comment for %s action %s unavailable: %s',
                       projcode, action_id, exc)
        return None
    action = find_action(family, action_id)
    if action is None:
        logger.warning('approver comment: action %s is not on the %s family '
                       '(%d line(s))', action_id, projcode, len(family))
        return None
    comment = normalize_comment(action.get('adminComments'))
    logger.info('approver comment for %s action %s: %s', projcode, action_id,
                f'{len(comment)} chars' if comment else 'none')
    return comment


def approver_comment_for_action(action: Any, *, client: Optional[XrasApiClient] = None
                                ) -> Optional[str]:
    """The note for an ``XrasActionLog`` row; ``None`` whenever it cannot be had.

    The projcode is ``projcode_result`` (a New) else ``request_number`` (every
    later action names the project). Unconfigured client, XRAS down, a bug in
    this path: all ``None``, because a notice must go out regardless.
    """
    if action is None:
        return None
    projcode = (getattr(action, 'projcode_result', None)
                or getattr(action, 'request_number', None))
    action_id = getattr(action, 'action_id', None)
    try:
        resolved = client or XrasApiClient.from_environment()
        return approver_comment(resolved, projcode, action_id)
    except XrasSourceUnavailable as exc:
        logger.warning('approver comment for %s action %s skipped: %s',
                       projcode, action_id, exc)
        return None
    except Exception:
        logger.exception('approver comment for %s action %s failed; sending without it',
                         projcode, action_id)
        return None
