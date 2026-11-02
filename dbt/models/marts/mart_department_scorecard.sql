-- Grain: one row per department. Trailing-12-month performance vs. the prior 12 months, plus today's backlog.
with w as (select * from {{ ref('int_311__kpi_window') }}),

requests as (
    select r.*, d.department_name
    from {{ ref('int_311__service_work') }} as r
    join {{ ref('dim_department') }} as d using (department_key)
    where not r.is_canceled
),

current_window as (
    select
        department_key,
        department_name,
        count(*)                                                    as requests,
        median(hours_to_close) / 24                                 as median_days_to_close,
        quantile_cont(hours_to_close, 0.9) / 24                     as p90_days_to_close,
        avg(closed_within_7d::int)                                  as pct_closed_within_7d
    from requests, w
    where created_date > w.window_start and created_date <= w.window_end
    group by all
),

prior_window as (
    select
        department_key,
        count(*)                                                    as requests_prior,
        avg(closed_within_7d::int)                                  as pct_closed_within_7d_prior
    from requests, w
    where created_date > w.prior_start and created_date <= w.prior_end
    group by all
),

open_now as (
    select
        department_key,
        count(*)                                                                as open_requests,
        count(*) filter (where date_diff('day', created_at, w.data_as_of) > 30) as open_over_30_days,
        median(date_diff('hour', created_at, w.data_as_of)) / 24                as median_open_age_days
    from requests, w
    where is_open
    group by all
)

select
    c.department_key,
    c.department_name,
    c.requests,
    p.requests_prior,
    c.requests / nullif(p.requests_prior, 0) - 1                              as volume_change_pct,
    c.median_days_to_close,
    c.p90_days_to_close,
    c.pct_closed_within_7d,
    p.pct_closed_within_7d_prior,
    coalesce(o.open_requests, 0)                                              as open_requests,
    coalesce(o.open_over_30_days, 0)                                          as open_over_30_days,
    o.median_open_age_days
from current_window as c
left join prior_window as p using (department_key)
left join open_now as o using (department_key)
