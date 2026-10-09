-- hpc_reader: the read-only Postgres role HPC users' sam-search runs as (HPC_LANE_ENV_LAYERING.md § 3).
-- Run as postgres on csg-postgres, once per step below; the password is a SCRAM verifier
-- from scripts/pg_scram_verifier.py, so the plaintext never reaches the server.

-- 1. The role (once per cluster). Read-only and bounded at the server, whatever the client asks.
CREATE ROLE hpc_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
    CONNECTION LIMIT 40
    PASSWORD 'SCRAM-SHA-256$4096:...';
ALTER ROLE hpc_reader SET default_transaction_read_only = on;
ALTER ROLE hpc_reader SET statement_timeout = '60s';
ALTER ROLE hpc_reader SET idle_in_transaction_session_timeout = '60s';

-- 2. system_status and system_status_dev: the last-seen tables only (sam-search user).
\connect system_status
GRANT CONNECT ON DATABASE system_status TO hpc_reader;
GRANT USAGE ON SCHEMA public TO hpc_reader;
GRANT SELECT ON access_sources, user_last_seen, systems, status_users TO hpc_reader;

\connect system_status_dev
GRANT CONNECT ON DATABASE system_status_dev TO hpc_reader;
GRANT USAGE ON SCHEMA public TO hpc_reader;
GRANT SELECT ON access_sources, user_last_seen, systems, status_users TO hpc_reader;

-- 3. casper_jobs and derecho_jobs: every job_history table (sam-search accounting --jobs, jobhist).
\connect casper_jobs
GRANT CONNECT ON DATABASE casper_jobs TO hpc_reader;
GRANT USAGE ON SCHEMA public TO hpc_reader;
GRANT SELECT ON accounts, daily_summary, job_charges, job_qos, job_records, jobs, queues, users
    TO hpc_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO hpc_reader;

\connect derecho_jobs
GRANT CONNECT ON DATABASE derecho_jobs TO hpc_reader;
GRANT USAGE ON SCHEMA public TO hpc_reader;
GRANT SELECT ON accounts, daily_summary, job_charges, job_qos, job_records, jobs, queues, users
    TO hpc_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO hpc_reader;

-- 4. Verify, as hpc_reader on csg-postgres-ro (expected results in the plan, § 6).
--    SHOW default_transaction_read_only;                -- on
--    SELECT count(*) FROM user_last_seen;               -- works
--    INSERT INTO systems (name) VALUES ('x');           -- ERROR: read-only transaction
--    SELECT * FROM task_run LIMIT 1;                    -- ERROR: permission denied
--    SELECT pg_sleep(90);                               -- ERROR: statement timeout
--    The read-only setting is a default a client can SET off; the grants are the boundary,
--    and the -ro replica refuses writes whatever the role.
