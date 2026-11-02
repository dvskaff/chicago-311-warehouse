{{ config(materialized='ephemeral') }}
{#-
    The reporting window used by every KPI, defined once:
    trailing 365 days of created requests, ending 7 days before the latest data so every request in the window
    has had at least a week to be closed (otherwise "% closed within 7 days" would be biased downward).
    The prior window is the 365 days before that, for year-over-year comparisons.
-#}
with latest as (
    select max(last_modified_at) as data_as_of from {{ ref('fct_service_requests') }}
)

select
    data_as_of,
    cast(data_as_of as date) - {{ var('fast_close_days') }}             as window_end,
    cast(data_as_of as date) - {{ var('fast_close_days') }} - 365       as window_start,
    cast(data_as_of as date) - {{ var('fast_close_days') }} - 365       as prior_end,
    cast(data_as_of as date) - {{ var('fast_close_days') }} - 730       as prior_start
from latest
