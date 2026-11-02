-- The daily table must account for every service-work request (no request lost in the date/department grid).
with daily as (select sum(requests_opened) as n from {{ ref('fct_daily_department_activity') }}),
     work as (select count(*) as n from {{ ref('int_311__service_work') }})
select daily.n as daily_opened, work.n as service_work_requests
from daily, work
where daily.n != work.n
