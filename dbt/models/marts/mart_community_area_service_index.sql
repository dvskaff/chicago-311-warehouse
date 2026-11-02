{#-
    Are some neighborhoods served more slowly for the same kind of request?
    Raw medians mix request types (a building inspection takes longer than graffiti removal), so each closed
    request is compared with the citywide median time for its own type in the same window. The service index is
    the geometric mean of those ratios: 1.00 = typical, 1.25 = 25% slower than typical for the same work.
-#}
with w as (select * from {{ ref('int_311__kpi_window') }}),

closed as (
    select r.*
    from {{ ref('fct_service_requests') }} as r, w
    where r.is_counted and not r.is_canceled
      and r.hours_to_close > 0
      and r.created_date > w.window_start and r.created_date <= w.window_end
),

type_medians as (
    select request_type_key, median(hours_to_close) as type_median_hours
    from closed group by request_type_key
),

scored as (
    select c.community_area_id, ln(c.hours_to_close / t.type_median_hours) as log_ratio, c.hours_to_close,
           c.closed_within_7d
    from closed as c
    join type_medians as t using (request_type_key)
    where c.community_area_id is not null and t.type_median_hours > 0
)

select
    s.community_area_id,
    a.community_area_name,
    count(*)                                      as closed_requests,
    exp(avg(s.log_ratio))                         as service_index,
    median(s.hours_to_close) / 24                 as median_days_to_close,
    avg(s.closed_within_7d::int)                  as pct_closed_within_7d
from scored as s
join {{ ref('dim_community_area') }} as a using (community_area_id)
group by all
having count(*) >= {{ var('min_area_requests') }}
