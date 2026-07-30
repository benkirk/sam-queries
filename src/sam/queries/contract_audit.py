"""
Read-only data-hygiene checks over the ``contract`` table.

Mirrors :mod:`sam.queries.tree_audit`: every function takes a session, returns
a list of plain findings, and an **empty list means the data is clean**.  No
writes anywhere — the webapp's edit form is where corrections happen.

The headline check is :data:`FUNDING_ACCOUNT_PROGRAM`.  NSF's award API carries
both ``fundProgramName`` (a research program, e.g. ``PHYSICAL & DYNAMIC
METEOROLOGY``) and ``primaryProgram`` (a funding *account*, e.g. ``01002526DB
NSF RESEARCH & RELATED ACTIVIT``).  Pasting the latter into ``nsf_program``
creates a lookup row that classifies nothing, and 57 of 368 open contracts —
15% — point at one.  The rows in use are the *recent* fiscal years, so this is
an active bug, not a historical artifact: the create form maps the right field
now, but manual entry still does not.  ``NsfAwardProvider._to_record`` carries
the same warning at the other end of the pipe.

**Everything runs in Python over one eager-loaded result set** rather than as
SQL predicates.  Two reasons: the funding-account rule is a regex, and
``REGEXP`` is MySQL-specific (see docs/plans/POSTGRES_MIGRATION.md); and the
whole table is 2,225 rows, so a single loaded pass costs less than six round
trips.
"""

import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from sam.integration.awards import nsf_award_id
from sam.queries.admin import get_contracts_with_pi

#: Check keys, in report order — highest signal first.
FUNDING_ACCOUNT_PROGRAM = 'funding_account_program'
MONITOR_IS_PI = 'monitor_is_pi'
MISSING_MONITOR = 'missing_monitor'
MISSING_PROGRAM = 'missing_program'
UNPARSEABLE_AWARD_ID = 'unparseable_award_id'
URL_MISSING = 'url_missing'

#: ``(key, label, severity)`` for every check, in report order.  The CLI builds
#: its sections from this, so a check with zero findings still gets a line —
#: an absent section reads as "not run" rather than "clean".
CHECKS = (
    (FUNDING_ACCOUNT_PROGRAM,
     'Program is a funding-account string, not a research program', 'high'),
    (MONITOR_IS_PI,
     'Contract monitor is the same person as the PI', 'high'),
    (MISSING_MONITOR,
     'NSF contract with no monitor', 'medium'),
    (MISSING_PROGRAM,
     'NSF contract with no program', 'medium'),
    (UNPARSEABLE_AWARD_ID,
     'NSF contract number does not parse to an award id', 'medium'),
    (URL_MISSING,
     'No award URL', 'low'),
)

#: NSF funding-account strings are eight digits then two letters, e.g.
#: ``01002526DB NSF RESEARCH & RELATED ACTIVIT``.  Six such rows exist and
#: they account for every ``funding_account_program`` finding.
_FUNDING_ACCOUNT_RE = re.compile(r'^\d{8}[A-Z]{2}')

#: Program names that stand in for NULL.  ``nsf_program_id=107`` is literally
#: named ``NONE`` and is used by 10 contracts; a program named "NONE" is a
#: missing program, so it is reported as one rather than as its own category.
_PLACEHOLDER_PROGRAMS = frozenset({'NONE', 'N/A', 'UNKNOWN', 'TBD'})


def _source_name(contract) -> Optional[str]:
    """The funding source's name, or ``None`` (the FK is NOT NULL, but be safe)."""
    return contract.contract_source.contract_source if contract.contract_source else None


def is_nsf(contract) -> bool:
    """Whether this contract is filed under ``contract_source = 'NSF'``.

    Three checks are NSF-only and would otherwise be mostly noise: 18 of the
    20 contracts with no program are non-NSF sources (DOE, NASA, AFOSR, …),
    where ``Contract.create``'s docstring says a program is not expected, and
    NSF is the only surveyed source that carries a program officer at all.
    """
    return (_source_name(contract) or '').strip().upper() == 'NSF'


def is_funding_account_program(program_name: Optional[str]) -> bool:
    """Whether *program_name* is an NSF funding account rather than a program."""
    return bool(program_name and _FUNDING_ACCOUNT_RE.match(program_name.strip()))


def _program_name(contract) -> Optional[str]:
    return contract.nsf_program.nsf_program_name if contract.nsf_program else None


def _check_contract(contract) -> List[Dict]:
    """Every finding for one contract, in ``CHECKS`` order."""
    findings = []
    program = _program_name(contract)
    nsf = is_nsf(contract)

    if is_funding_account_program(program):
        findings.append((FUNDING_ACCOUNT_PROGRAM, {'nsf_program': program}))

    if (contract.contract_monitor_user_id is not None
            and contract.contract_monitor_user_id
            == contract.principal_investigator_user_id):
        findings.append((MONITOR_IS_PI, {
            'username': (contract.principal_investigator.username
                         if contract.principal_investigator else None),
        }))

    if nsf and contract.contract_monitor_user_id is None:
        findings.append((MISSING_MONITOR, {}))

    if nsf:
        if contract.nsf_program_id is None:
            findings.append((MISSING_PROGRAM, {'nsf_program': None}))
        elif (program or '').strip().upper() in _PLACEHOLDER_PROGRAMS:
            findings.append((MISSING_PROGRAM, {'nsf_program': program}))

    if nsf and nsf_award_id(contract.contract_number) is None:
        findings.append((UNPARSEABLE_AWARD_ID, {}))

    if not (contract.url or '').strip():
        findings.append((URL_MISSING, {}))

    return [{'check': key, 'contract': contract, 'detail': detail}
            for key, detail in findings]


def audit_contracts(session: Session, active_only: bool = True) -> List[Dict]:
    """
    Find contracts whose data is internally inconsistent or incomplete.

    Purely local — nothing here touches the network.  For divergence against
    what the funding agency says, see
    :func:`sam.integration.awards.audit.compare_contract`.

    Args:
        session:     SQLAlchemy session.
        active_only: audit only contracts inside their date window (the
                     default, 368 of 2,225 today).  ``False`` audits every
                     contract, which surfaces long-expired rows nobody will
                     fix but makes the vacuous checks non-vacuous.

    Returns:
        One dict per (contract, failed check), grouped by check in ``CHECKS``
        order and then by contract number::

            {
                'check':    'funding_account_program',
                'contract': <Contract>,          # ORM object
                'detail':   {'nsf_program': '01002526DB NSF RESEARCH ...'},
            }

        Empty list means every audited contract is clean.  ``contract`` is an
        ORM object on purpose: serialization is the caller's job (the CLI uses
        ``ContractSummarySchema``), so this stays usable from a template or a
        route that wants the live object.
    """
    contracts = get_contracts_with_pi(session, active_only=active_only,
                                      with_source=True)

    findings = []
    for contract in contracts:
        findings.extend(_check_contract(contract))

    order = {key: i for i, (key, _label, _sev) in enumerate(CHECKS)}
    findings.sort(key=lambda f: (order[f['check']],
                                 f['contract'].contract_number or ''))
    return findings


def audit_nsf_programs(session: Session) -> List[Dict]:
    """
    Find ``nsf_program`` rows that are funding accounts, not research programs.

    A finding about the lookup table rather than about any one contract, and
    strictly more actionable than the per-contract list: six bad rows are what
    all 57 open ``funding_account_program`` findings point at, so renaming or
    retiring six rows fixes every one of them.

    Orphan programs (rows no contract references — 53 of 239 today) are
    deliberately *not* reported: no contract is wrong because of them, and a
    cleanup list would bury the six rows that matter.

    Returns:
        One dict per offending program, most-used first::

            {'nsf_program_id': 241,
             'nsf_program_name': '01002526DB NSF RESEARCH & RELATED ACTIVIT',
             'active': True,
             'contract_count': 41,
             'open_contract_count': 23}

        Empty list means the lookup table is clean.
    """
    from sam.projects.contracts import NSFProgram

    programs = session.query(NSFProgram).order_by(NSFProgram.nsf_program_name).all()

    findings = []
    for program in programs:
        if not is_funding_account_program(program.nsf_program_name):
            continue
        contracts = program.contracts
        findings.append({
            'nsf_program_id':      program.nsf_program_id,
            'nsf_program_name':    program.nsf_program_name,
            'active':              program.is_active,
            'contract_count':      len(contracts),
            'open_contract_count': sum(1 for c in contracts if c.is_active),
        })

    findings.sort(key=lambda f: (-f['open_contract_count'], -f['contract_count']))
    return findings
