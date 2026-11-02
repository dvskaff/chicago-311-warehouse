{#-
    Business logic in one place:
    * closed_at: the published closed date; canceled requests without one are treated as closed when last modified,
      so they leave the backlog.
    * hours_to_close: NULL for open requests and for data-entry errors where the closure precedes creation
      (those are counted by a warning-level data test instead of silently skewing medians).
    * is_counted: requests that count toward performance metrics (not duplicates of another request).
-#}
with requests as (

    select * from {{ ref('stg_311__service_requests') }}

),

enriched as (

    select
        *,
        case
            when closed_at_source is not null then closed_at_source
            when status = 'Canceled' then last_modified_at
        end                                                                 as closed_at,
        status = 'Canceled'                                                 as is_canceled
    from requests

)

select
    sr_number,
    request_type,
    request_type_code,
    department_name,
    status,
    origin,
    created_at,
    cast(created_at as date)                                                as created_date,
    closed_at,
    cast(closed_at as date)                                                 as closed_date,
    last_modified_at,
    case
        when closed_at is null or closed_at < created_at then null
        else date_diff('second', created_at, closed_at) / 3600.0
    end                                                                     as hours_to_close,
    coalesce(closed_at < created_at, false)                                 as has_closure_before_creation,
    closed_at is null                                                       as is_open,
    is_canceled,
    is_duplicate,
    not is_duplicate                                                        as is_counted,
    parent_sr_number,
    community_area_id,
    ward,
    zip_code,
    latitude,
    longitude
from enriched
