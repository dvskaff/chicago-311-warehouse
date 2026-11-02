-- The daily table must account for every counted request (no request lost in the date/department grid).
with daily as (select sum(requests_opened) as n from {{ ref('fct_daily_department_activity') }}),
     fact as (select count(*) as n from {{ ref('fct_service_requests') }} where is_counted)
select daily.n as daily_opened, fact.n as fact_requests
from daily, fact
where daily.n != fact.n
