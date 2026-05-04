#-------------------------------------------------------------------------bh-
# System Status Models - Export all models
#-------------------------------------------------------------------------eh-

# Lookup tables (Phase 2). Imported first so the FK targets exist when
# the snapshot models below register their relationships.
from .lookups import System, QueueDef, Filesystem, LoginNodeDef, UserDef, ProjectCodeDef

from .derecho import DerechoStatus
from .casper import CasperStatus, CasperNodeTypeStatus
from .jupyterhub import JupyterHubStatus
from .outages import SystemOutage, ResourceReservation
from .login_nodes import LoginNodeStatus
from .filesystems import FilesystemStatus
from .queues import QueueStatus
from .user_proj_queues import UserProjQueueStatus

# Side-effect import: registers the before_flush listener that resolves
# `_pending_*_name` strings staged by the snapshot models' property
# setters into the corresponding FK ids.
from ..queries import lookups as _lookup_listener  # noqa: F401

__all__ = [
    # Lookup tables
    'System',
    'QueueDef',
    'Filesystem',
    'LoginNodeDef',
    'UserDef',
    'ProjectCodeDef',

    # Derecho
    'DerechoStatus',

    # Casper
    'CasperStatus',
    'CasperNodeTypeStatus',

    # JupyterHub
    'JupyterHubStatus',

    # Common
    'LoginNodeStatus',
    'FilesystemStatus',
    'QueueStatus',
    'UserProjQueueStatus',

    # Support
    'SystemOutage',
    'ResourceReservation',
]
