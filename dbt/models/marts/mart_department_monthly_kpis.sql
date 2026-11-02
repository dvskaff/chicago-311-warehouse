-- Grain: one row per department per month of request creation.
with requests as (
    select r.*, cast(date_trunc('month', r.created_date) as date) as month_start
    from {{ ref('fct_service_requests') }} as r
    where r.is_counted and not r.is_canceled
),

monthly as (
    select
        month_start,
        department_key,
        count(*)                                                    as requests_created,
        count(hours_to_close)                                       as requests_closed,
        median(hours_to_close) / 24                                 as median_days_to_close,
        quantile_cont(hours_to_close, 0.9) / 24                     as p90_days_to_close,
        avg(closed_within_7d::int)                                  as pct_closed_within_7d
    from requests
    group by all
),

month_end_backlog as (
    select
        cast(date_trunc('month', date_day) as date)                 as month_start,
        department_key,
        arg_max(backlog_end_of_day, date_day)                       as backlog_end_of_month
    from {{ ref('fct_daily_department_activity') }}
    group by all
)

select
    m.month_start,
    m.department_key,
    d.department_name,
    m.requests_created,
    m.requests_closed,
    m.median_days_to_close,
    m.p90_days_to_close,
    m.pct_closed_within_7d,
    b.backlog_end_of_month,
    -- A month is complete once its last day is at least 7 days before the latest data.
    (m.month_start + interval 1 month - interval 1 day) <= w.window_end      as is_complete_month
from monthly as m
join {{ ref('dim_department') }} as d using (department_key)
left join month_end_backlog as b using (month_start, department_key)
cross join {{ ref('int_311__kpi_window') }} as w
