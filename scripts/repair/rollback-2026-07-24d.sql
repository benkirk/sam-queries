-- Rollback for the 2026-07-24 batch-D restore (1 users, 5 rows).
-- Generated from the pre-image BEFORE any UPDATE ran.

-- jingyuanf (5 rows): P28100036
UPDATE account_user SET end_date = '2026-07-17 00:16:54' WHERE account_user_id IN (133611,133612,133613,133614,133616);
