{#-
    One row per request type. A type belongs to the department that owns most of its requests.
    Types the city closes on receipt (median time to close under an hour, e.g. complaints that are only logged)
    are flagged so they count toward volume but not toward timing or backlog metrics.
-#}
with requests as (
    select * from {{ ref('int_311__requests_enriched') }} where is_counted
)

select
    {{ surrogate_key('request_type') }}                                         as request_type_key,
    request_type,
    mode(request_type_code)                                                      as request_type_code,
    mode(department_name)                                                        as owner_department_name,
    {{ surrogate_key('mode(department_name)') }}                                 as owner_department_key,
    count(*)                                                                     as requests_all_time,
    median(hours_to_close) filter (where not is_canceled)                        as median_hours_to_close,
    coalesce(
        median(hours_to_close) filter (where not is_canceled) < {{ var('closed_on_receipt_hours') }}, false
    )                                                                            as is_closed_on_receipt,
    min(created_at)                                                              as first_request_at,
    max(created_at)                                                              as last_request_at
from requests
group by request_type
