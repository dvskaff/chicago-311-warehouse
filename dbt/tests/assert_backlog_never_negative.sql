-- A department can never have closed more requests than it opened.
select date_day, department_key, backlog_end_of_day
from {{ ref('fct_daily_department_activity') }}
where backlog_end_of_day < 0
