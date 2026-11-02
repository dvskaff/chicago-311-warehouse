-- Grain: one row per request type. Same definitions as the department scorecard.
with w as (select * from {{ ref('int_311__kpi_window') }}),

requests as (
    select * from {{ ref('int_311__service_work') }}
    where not is_canceled
)

select
    request_type_key,
    request_type,
    owner_department_name,
    count(*) filter (where created_date > w.window_start and created_date <= w.window_end)       as requests,
    count(*) filter (where created_date > w.prior_start and created_date <= w.prior_end)         as requests_prior,
    median(hours_to_close) filter (
        where created_date > w.window_start and created_date <= w.window_end) / 24                as median_days_to_close,
    quantile_cont(hours_to_close, 0.9) filter (
        where created_date > w.window_start and created_date <= w.window_end) / 24                as p90_days_to_close,
    avg(closed_within_7d::int) filter (
        where created_date > w.window_start and created_date <= w.window_end)                     as pct_closed_within_7d,
    count(*) filter (where is_open)                                                               as open_requests
from requests, w
group by request_type_key, request_type, owner_department_name
having count(*) filter (where created_date > w.window_start and created_date <= w.window_end) > 0
