-- Grain: one row per day per department. Backlog = requests opened minus closed, cumulatively.
with requests as (
    select * from {{ ref('fct_service_requests') }} where is_counted
),

opened as (
    select department_key, created_date as date_day, count(*) as n from requests group by all
),

closed as (
    -- A closure recorded before creation (data-entry error) is counted on the creation day instead.
    select department_key, greatest(closed_date, created_date) as date_day, count(*) as n
    from requests where closed_date is not null group by all
),

grid as (
    select d.date_day, dep.department_key
    from {{ ref('dim_date') }} as d
    cross join {{ ref('dim_department') }} as dep
)

select
    g.date_day,
    g.department_key,
    coalesce(o.n, 0)                                               as requests_opened,
    coalesce(c.n, 0)                                               as requests_closed,
    sum(coalesce(o.n, 0) - coalesce(c.n, 0)) over (
        partition by g.department_key order by g.date_day
        rows between unbounded preceding and current row
    )                                                              as backlog_end_of_day
from grid as g
left join opened as o using (department_key, date_day)
left join closed as c using (department_key, date_day)
