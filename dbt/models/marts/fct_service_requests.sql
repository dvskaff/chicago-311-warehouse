{{
    config(
        materialized='incremental',
        unique_key='sr_number',
        incremental_strategy='delete+insert',
        on_schema_change='fail',
        contract={'enforced': true}
    )
}}
{#-
    Grain: one row per service request (its latest state). Incremental: each run only re-processes requests
    modified since the last run (with a 2-day safety overlap), replacing their previous row.
-#}
select
    sr_number,
    {{ surrogate_key('request_type') }}                                  as request_type_key,
    {{ surrogate_key('department_name') }}                               as department_key,
    community_area_id,
    created_date,
    closed_date,
    created_at,
    closed_at,
    last_modified_at,
    status,
    origin,
    cast(hours_to_close as double)                                       as hours_to_close,
    coalesce(hours_to_close <= {{ var('fast_close_days') }} * 24, false) as closed_within_7d,
    is_open,
    is_canceled,
    is_duplicate,
    is_counted,
    has_closure_before_creation,
    ward,
    latitude,
    longitude
from {{ ref('int_311__requests_enriched') }}
{% if is_incremental() %}
where last_modified_at >= (select max(last_modified_at) - interval 2 day from {{ this }})
{% endif %}
