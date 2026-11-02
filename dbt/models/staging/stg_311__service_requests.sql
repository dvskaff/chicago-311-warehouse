{#-
    One row per service request: the latest version from the append-only raw log, with proper types.
    Legacy records migrated from the pre-2018 system are excluded (different workflow and definitions).
-#}
with versions as (

    select
        {{ clean_text('sr_number') }}                                   as sr_number,
        {{ clean_text('sr_type') }}                                     as request_type,
        upper({{ clean_text('sr_short_code') }})                        as request_type_code,
        {{ clean_text('owner_department') }}                            as department_name,
        {{ clean_text('created_department') }}                          as created_department,
        {{ clean_text('status') }}                                      as status,
        {{ clean_text('origin') }}                                      as origin,
        try_cast(created_date as timestamp)                              as created_at,
        try_cast(last_modified_date as timestamp)                        as last_modified_at,
        try_cast(closed_date as timestamp)                               as closed_at_source,
        coalesce(try_cast(duplicate as boolean), false)                 as is_duplicate,
        coalesce(try_cast(legacy_record as boolean), false)             as is_legacy,
        {{ clean_text('parent_sr_number') }}                            as parent_sr_number,
        try_cast(try_cast(community_area as double) as integer)         as community_area_id,
        try_cast(try_cast(ward as double) as integer)                   as ward,
        left({{ clean_text('zip_code') }}, 5)                           as zip_code,
        try_cast(latitude as double)                                    as latitude,
        try_cast(longitude as double)                                   as longitude,
        _batch_id,
        _loaded_at
    from {{ source('chicago_311', 'service_requests') }}

)

select *
from versions
where sr_number is not null
  and created_at is not null
  and not is_legacy
qualify row_number() over (
    partition by sr_number
    order by last_modified_at desc nulls last, _loaded_at desc
) = 1
