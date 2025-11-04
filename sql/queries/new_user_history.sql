-- New user accounts created by quarter (last 10 years)
-- define starting date
SET @start_date = '2015-01-01';

SELECT
    CONCAT(YEAR(creation_time), ' Q', QUARTER(creation_time)) AS quarter_label,
    COUNT(*) AS new_users
FROM users
WHERE creation_time >= @start_date
    AND creation_time <= CURDATE()
GROUP BY YEAR(creation_time), QUARTER(creation_time)
ORDER BY YEAR(creation_time), QUARTER(creation_time);
