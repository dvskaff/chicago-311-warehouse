-- One row: the headline numbers shown on the dashboard and in the README, defined once here.
with w as (select * from {{ ref('int_311__kpi_window') }}),

requests as (
    select * from {{ ref('fct_service_requests') }} where is_counted and not is_canceled
)

select
    w.data_as_of,
    w.window_start,
    w.window_end,
    count(*) filter (where created_date > w.window_start and created_date <= w.window_end)          as requests,
    count(*) filter (where created_date > w.prior_start and created_date <= w.prior_end)            as requests_prior,
    median(hours_to_close) filter (where created_date > w.window_start and created_date <= w.window_end) / 24
                                                                                                     as median_days_to_close,
    avg(closed_within_7d::int) filter (where created_date > w.window_start and created_date <= w.window_end)
                                                                                                     as pct_closed_within_7d,
    avg(closed_within_7d::int) filter (where created_date > w.prior_start and created_date <= w.prior_end)
                                                                                                     as pct_closed_within_7d_prior,
    count(*) filter (where is_open)                                                                  as open_requests,
    count(*) filter (where is_open and date_diff('day', created_at, w.data_as_of) > 30)              as open_over_30_days,
    (select count(*) from {{ ref('fct_service_requests') }})                                         as requests_in_warehouse
from requests, w
group by w.data_as_of, w.window_start, w.window_end
