"""Which SAM project owns a storage fileset: one rule for the disk ingest and the quota reconcile.

A fileset is a (path, label) pair; the label is the storage system's group or fileset name,
uppercased to compare against projcodes. Resolution follows legacy: an active ProjectDirectory
for the path first, then the label as a projcode, then the one project the file's linked paths
give that label (`/quasar/rda_dr` rides with `/quasar/rda`). A fileset that fails gets a
category (`FilesetResolution.reason`) instead of an error. See docs/plans/DISK_INGEST_TRIAGE.md.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


# Fileset labels HSG reports that no SAM project owns (scratch, test and
# hackathon areas). Rows under them with an unlinked path are skipped, not
# charged and not errors. Grow it here; it is not config.
KNOWN_UNOWNED_PROJCODES = frozenset({'ROOT', 'RISC', 'NGIC'})

# --reconcile-directories writes these; 'review' (path owned by another project) never.
DIRECTORY_AUTO_ACTIONS = ('reopen', 'rename', 'create')


@dataclass(frozen=True)
class FilesetResolution:
    """How a fileset resolved; ``reason`` is None or a report category."""
    project: Optional[object] = None
    account: Optional[object] = None
    via: Optional[str] = None       # 'path' | 'projcode' | 'label'
    reason: Optional[str] = None
    action: Optional[str] = None    # unlinked_directory only: DIRECTORY_AUTO_ACTIONS or 'review'
    directory_id: Optional[int] = None   # the row a reopen/rename acts on

    @property
    def ok(self) -> bool:
        return self.account is not None


class DirectoryIndex:
    """Every project_directory row, ended ones included, to pick a reconcile action."""

    def __init__(self, rows):
        self.by_path = defaultdict(list)
        self.by_case = defaultdict(list)
        for pd in rows:
            self.by_path[pd.directory_name].append(pd)
            self.by_case[(pd.project_id, pd.directory_name.lower())].append(pd)

    @staticmethod
    def _latest(rows):
        return max(rows, key=lambda r: (r.end_date is None, r.end_date or datetime.min))

    def action_for(self, project_id: int, path: str) -> tuple[str, Optional[int]]:
        same = [r for r in self.by_path.get(path, ()) if r.project_id == project_id]
        if same:
            return 'reopen', self._latest(same).project_directory_id
        if self.by_path.get(path):
            return 'review', None
        variants = self.by_case.get((project_id, path.lower()))
        if variants:
            return 'rename', self._latest(variants).project_directory_id
        return 'create', None


class FilesetResolver:
    """Resolve filesets on one disk resource; ``learn_labels`` the whole file before ``resolve``."""

    def __init__(self, session, resource, *, include_deleted_accounts: bool = False):
        from sam.accounting.accounts import Account
        from sam.accounting.allocations import Allocation
        from sam.projects.projects import Project, ProjectDirectory

        self.session = session
        self.resource = resource
        self.include_deleted_accounts = include_deleted_accounts
        self.path_to_project = {
            pd.directory_name: proj
            for pd, proj in (
                session.query(ProjectDirectory, Project)
                .join(Project, Project.project_id == ProjectDirectory.project_id)
                .filter(ProjectDirectory.is_currently_active)
                .all()
            )
        }
        self.directory_index = DirectoryIndex(session.query(ProjectDirectory).all())
        self.funded_accounts = {
            account_id for (account_id,) in (
                session.query(Allocation.account_id)
                .join(Account, Account.account_id == Allocation.account_id)
                .filter(Account.resource_id == resource.resource_id, Allocation.is_active)
                .distinct()
            )
        }
        self._label_projects: dict[str, dict] = {}
        self._cache: dict[tuple, FilesetResolution] = {}
        self._accounts: dict[int, Optional[object]] = {}

    def learn_labels(self, filesets: Iterable[tuple[Optional[str], str]]) -> None:
        """Record which projects each label's linked paths belong to, for the sibling rule."""
        for path, label in filesets:
            proj = self.path_to_project.get(path) if path else None
            if proj is not None:
                self._label_projects.setdefault(label, {})[proj.project_id] = proj

    def resolve(self, path: Optional[str], label: str) -> FilesetResolution:
        key = (path, label)
        if key not in self._cache:
            self._cache[key] = self._resolve(path, label)
        return self._cache[key]

    def _account_for(self, project):
        from sam.accounting.accounts import Account
        if project.project_id not in self._accounts:
            self._accounts[project.project_id] = Account.get_by_project_and_resource(
                self.session, project.project_id, self.resource.resource_id,
                exclude_deleted=not self.include_deleted_accounts,
            )
        return self._accounts[project.project_id]

    def _resolve(self, path: Optional[str], label: str) -> FilesetResolution:
        from sam.projects.projects import Project

        project = self.path_to_project.get(path) if path else None
        via = 'path'
        if project is None:
            if label in KNOWN_UNOWNED_PROJCODES:
                return FilesetResolution(reason='known_unowned')
            project = Project.get_by_projcode(self.session, label)
            via = 'projcode'
        if project is None:
            siblings = self._label_projects.get(label, {})
            if len(siblings) != 1:
                return FilesetResolution(reason='no_project')
            (project,) = siblings.values()
            via = 'label'

        acct = self._account_for(project)
        if acct is None:
            return FilesetResolution(project=project, via=via, reason='no_account')
        if via == 'path' or not path:
            return FilesetResolution(project=project, account=acct, via=via)
        if acct.account_id not in self.funded_accounts:
            return FilesetResolution(project=project, account=acct, via=via,
                                     reason='expired_directory')
        action, directory_id = self.directory_index.action_for(project.project_id, path)
        return FilesetResolution(project=project, account=acct, via=via,
                                 reason='unlinked_directory', action=action,
                                 directory_id=directory_id)
