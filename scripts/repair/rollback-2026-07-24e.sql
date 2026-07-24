-- Rollback for the 2026-07-24 batch-E restore (3 users, 30 rows).
-- Generated from the pre-image BEFORE any UPDATE ran.

-- deppenme (13 rows): NCGD0011 P93300012
UPDATE account_user SET end_date = '2026-07-16 23:56:02' WHERE account_user_id IN (66483,66486,90862,90945,92719,104657,104740,104767,107410,107440,107522,107562,107628);

-- emeeker (8 rows): CESM0020 UCLA0088
UPDATE account_user SET end_date = '2026-07-17 00:03:39' WHERE account_user_id IN (144392,144393,144394,144395,144397,144398,154816,154817);

-- gseijo (9 rows): P93300012 UALB0057
UPDATE account_user SET end_date = '2026-07-16 23:58:31' WHERE account_user_id IN (87408,90871,90910,104661,104703,104772,157095,157097,157099);
