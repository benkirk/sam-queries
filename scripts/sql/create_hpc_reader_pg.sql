-- hpc_reader: the read-only Postgres role HPC users' tools run as (HPC_LANE_ENV_LAYERING.md § 3).
-- Run once as postgres on csg-postgres (18.3); clients connect to csg-postgres-ro.
-- The password is a SCRAM verifier from scripts/pg_scram_verifier.py, so the plaintext never
-- reaches the server. Store the plaintext in OpenBao csg/hpc-reader-pg.

-- Reads everything (Ben, 2026-10-09): pg_read_all_data covers every table, view and sequence in
-- every schema of every database, including ones created later. INHERIT, not NOINHERIT, or the
-- membership grants nothing without SET ROLE.
CREATE ROLE hpc_reader LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    CONNECTION LIMIT 40
    PASSWORD 'SCRAM-SHA-256$4096:...';
GRANT pg_read_all_data TO hpc_reader;

-- Bounds at the server, whatever the client asks.
ALTER ROLE hpc_reader SET default_transaction_read_only = on;
ALTER ROLE hpc_reader SET statement_timeout = '60s';
ALTER ROLE hpc_reader SET idle_in_transaction_session_timeout = '60s';

-- Verify, as hpc_reader on csg-postgres-ro, in system_status and casper_jobs:
--    SHOW default_transaction_read_only;          -- on
--    SELECT count(*) FROM user_last_seen;         -- works (any table, any schema)
--    INSERT INTO systems (name) VALUES ('x');     -- ERROR: read-only transaction
--    SET default_transaction_read_only = off;
--    INSERT INTO systems (name) VALUES ('x');     -- ERROR: permission denied
--    CREATE TABLE x (i int);                      -- ERROR: permission denied
--    SELECT pg_sleep(90);                         -- ERROR: statement timeout
-- The grants are the boundary; on csg-postgres-ro the standby refuses writes whatever the role.
