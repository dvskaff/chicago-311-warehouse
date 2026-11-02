-- One row: the headline numbers shown on the dashboard and in the README, defined once here.
with w as (select * from {{ ref('int_311__kpi_window') }}),

requests as (
    select * from {{ ref('int_311__service_work') }} where not is_canceled
),

closed_on_receipt as (
    select
        count(*) filter (where r.created_date > w.window_start and r.created_date <= w.window_end) as requests,
        count(distinct r.request_type_key)                                                         as request_types
    from {{ ref('fct_service_requests') }} as r
    join {{ ref('dim_request_type') }} as t using (request_type_key), w
    where r.is_counted and t.is_closed_on_receipt
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
    any_value(c.requests)                                                                            as closed_on_receipt_requests,
    any_value(c.request_types)                                                                       as closed_on_receipt_types,
    (select count(*) from {{ ref('fct_service_requests') }})                                         as requests_in_warehouse
from requests, w, closed_on_receipt as c
group by w.data_as_of, w.window_start, w.window_end
