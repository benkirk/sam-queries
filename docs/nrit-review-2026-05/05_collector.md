# Phase 5 — Collector (`collectors/`)

> Sibling subproject with its own `pyproject.toml`. Pulls usage data from Derecho, Casper, JupyterHub via cron. Feeds the rest of the system.

## Scope

- `collectors/casper/`, `collectors/derecho/`, `collectors/jupyterhub/`
- `collectors/cron_scripts/`, `collectors/lib/`, `collectors/docs/`
- `collectors/pyproject.toml`, `requirements.txt`, `run_collectors.sh`
- `containers/collectors/` (Docker image)
- `utils/sample_collector_commands.sh`
- `docs/apis/HPC_DATA_COLLECTORS_GUIDE.md`, `docs/apis/CHARGING_INTEGRATION.md`

## Method

1. Understand the ingest pipeline: source system → collector → staging → main DB.
2. How is collector code packaged & deployed? Container? Cron on a host?
3. Failure semantics: retry, partial-ingest, dedup, idempotency.
4. Auth / credentials to source systems (how they're injected, how they're rotated).
5. Observability: is failure visible? Where does it surface?
6. Test coverage — what's tested, what's mocked, what's not.

## Lenses applied

- Architecture
- Security (credentials to upstream systems)
- Operability (primary — this is a cron-fed pipeline)
- Testing

## Findings

*TBD*

### Architecture

*TBD*

### Security

*TBD*

### Operability

*TBD*

### Testing

*TBD*

## Cross-cutting tags raised

*TBD*

## Open questions for Ben

*TBD*
