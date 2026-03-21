SELECT
    p.projcode,
    p.title,
    r.resource_name,
    a.first_threshold,
    a.second_threshold,
    a.cutoff_threshold
FROM account a
JOIN project p ON a.project_id = p.project_id
JOIN resources r ON a.resource_id = r.resource_id
WHERE
    a.deleted = 0
    AND (a.first_threshold IS NOT NULL OR a.second_threshold IS NOT NULL)
ORDER BY
    p.projcode,
    r.resource_name;

  SELECT
      p.projcode,
      p.title,
      r.resource_name,
      a.first_threshold,
      a.second_threshold,
      a.cutoff_threshold
  FROM account a
  JOIN project p ON a.project_id = p.project_id
  JOIN resources r ON a.resource_id = r.resource_id
  WHERE
      a.deleted = 0
      AND (a.first_threshold IS NOT NULL OR a.second_threshold IS NOT NULL)
      AND EXISTS (
          SELECT 1
          FROM allocation al
          WHERE al.account_id = a.account_id
            AND al.deleted = 0
            AND al.start_date <= NOW()
            AND (al.end_date IS NULL OR al.end_date >= NOW())
      )
  ORDER BY
      p.projcode,
      r.resource_name;
