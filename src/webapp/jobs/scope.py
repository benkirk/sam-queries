"""Job-history scopes — which jobs a fragment is allowed to aggregate.

The job service used to carry three near-identical families
(``search_jobs`` / ``_machine`` / ``_user``, and the matching ``count_*``)
that differed *only* in how they pinned the plugin's ``account`` / ``user``
kwargs, with the security rule for each restated in prose across six
docstrings. Those rules are these three classes now:

============  =================================================================
mode          pin
============  =================================================================
project       ``account`` = the project's tree projcodes. Always applied — it
              is the security boundary.
machine       **nothing.** Deliberately unscoped: every user's jobs,
              cross-project. The route MUST be gated on
              ``Permission.VIEW_ALL_JOB_DATA``; there is no fallback pinning
              here. ``account`` is an optional *narrowing* filter (the By
              Project drill) — it only restricts an already-authorized view.
user          ``user`` = the session username, server-side and
              non-negotiable. A client-supplied ``user`` filter raises rather
              than being silently overwritten. ``account`` may narrow, safely:
              the user pin still applies, so it restricts which of one's OWN
              jobs show.
============  =================================================================

`apply()` mutates the kwargs dict the service is about to hand the plugin;
`check_filters()` runs first, against the *raw* filter names, so a rejected
combination never reaches the query.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from webapp.utils.scope import NavigatorScope


class JobScope(NavigatorScope):
    """Base for the three job-history scopes."""

    def check_filters(self, filters: Dict[str, Any]) -> None:
        """Reject filter combinations this scope forbids. Default: none."""

    def apply(self, kwargs: Dict[str, Any]) -> None:
        """Inject this scope's pins into the plugin kwargs."""
        raise NotImplementedError

    @property
    def summary_projcodes(self) -> Optional[List[str]]:
        """Projcodes for the SAM ``comp_charge_summary`` count fast path.

        ``None`` means "not eligible" — the count must come from the
        plugin. Only project mode qualifies: the summary is a per-project
        accounting table, and the other two modes are permission-gated,
        low-volume operator surfaces where a ``COUNT(*)`` is fine.
        """
        return None


class ProjectJobScope(JobScope):
    """Jobs belonging to one project (or its whole tree)."""

    mode = 'project'

    def __init__(self, project=None,
                 account_projcodes: Optional[Sequence[str]] = None):
        """
        Args:
            project: the authorized project. Pins ``account`` to its
                projcode when *account_projcodes* is not given.
            account_projcodes: an already-resolved project tree (parent +
                descendants), so child-projcode jobs show under the
                parent's drill-downs. Callers that resolved the tree
                themselves may pass this alone.

        At least one is required. A project scope with neither would be an
        unpinned query wearing project mode's clothes — the one shape this
        class exists to make impossible.
        """
        if project is None and not account_projcodes:
            raise ValueError(
                'ProjectJobScope requires a project or account_projcodes '
                '(the account filter is the security boundary).')
        self.project = project
        self.account_projcodes = (list(account_projcodes)
                                  if account_projcodes is not None else None)

    @property
    def projcodes(self) -> List[str]:
        """The projcodes this scope pins to — always at least one."""
        return (self.account_projcodes if self.account_projcodes is not None
                else [self.project.projcode])

    def apply(self, kwargs: Dict[str, Any]) -> None:
        kwargs['account'] = (self.account_projcodes
                             if self.account_projcodes is not None
                             else self.project.projcode)

    @property
    def summary_projcodes(self) -> Optional[List[str]]:
        return self.projcodes

    def context(self) -> Dict[str, Any]:
        return {'mode': self.mode,
                'projcode': self.project.projcode if self.project
                            else self.projcodes[0]}


class MachineJobScope(JobScope):
    """Every job on a machine. SECURITY: unscoped — see the module table."""

    mode = 'machine'

    def __init__(self, account: Optional[str] = None):
        #: Optional NARROWING filter (the By Project drill). Never widens.
        self.account = account or None

    def apply(self, kwargs: Dict[str, Any]) -> None:
        if self.account:
            kwargs['account'] = self.account

    def context(self) -> Dict[str, Any]:
        return {'mode': self.mode, 'account': self.account}


class UserJobScope(JobScope):
    """One user's jobs ("My Jobs"). The user pin is server-side."""

    mode = 'user'

    def __init__(self, username: str, account: Optional[str] = None):
        if not username:
            raise ValueError('UserJobScope requires a username (user pin).')
        self.username = username
        #: Optional NARROWING filter — safe from the client in this mode
        #: only, because the user pin still applies on top of it.
        self.account = account or None

    def check_filters(self, filters: Dict[str, Any]) -> None:
        """A client-supplied ``user`` raises rather than being overwritten.

        Silently dropping it would make a crafted ``?user=someone-else``
        look like it worked; the route must never forward one into this mode.
        """
        if 'user' in filters:
            raise ValueError(
                'user-scoped queries pin the user server-side; '
                "remove the 'user' filter from the call."
            )

    def apply(self, kwargs: Dict[str, Any]) -> None:
        if self.account:
            kwargs['account'] = self.account
        kwargs['user'] = self.username

    def context(self) -> Dict[str, Any]:
        return {'mode': self.mode, 'username': self.username}
