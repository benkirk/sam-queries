SELECT *
FROM project_directory
WHERE (end_date IS NULL OR end_date > NOW())
  AND directory_name LIKE '%/p/%';
