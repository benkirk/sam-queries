-- Rollback for the 2026-07-28 restore (1 user, 4 rows).
-- Re-applies the end_date that the restore cleared.
-- Generated from the pre-image BEFORE any UPDATE ran.

-- chenghao (4 rows): UOKL0055 — Campaign_Store, Casper, Derecho, Derecho GPU
UPDATE account_user SET end_date = '2026-07-26 00:20:52' WHERE account_user_id IN (126720,126722,126724,126726);
