-- Calendar spine covering every day from the backfill start to the latest data.
with bounds as (
    select
        cast('{{ var("backfill_start") }}' as date)                              as first_day,
        cast(max(last_modified_at) as date)                                      as last_day
    from {{ ref('int_311__requests_enriched') }}
),

days as (
    select cast(unnest(generate_series(first_day, last_day, interval 1 day)) as date) as date_day
    from bounds
)

select
    date_day,
    year(date_day)                                     as year,
    quarter(date_day)                                  as quarter,
    month(date_day)                                    as month,
    strftime(date_day, '%b')                           as month_name,
    cast(date_trunc('month', date_day) as date)        as month_start,
    cast(date_trunc('week', date_day) as date)         as week_start,
    isodow(date_day)                                   as iso_day_of_week,
    isodow(date_day) in (6, 7)                         as is_weekend
from days
