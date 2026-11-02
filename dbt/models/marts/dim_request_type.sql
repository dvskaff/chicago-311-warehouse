-- A request type belongs to the department that owns most of its requests.
select
    {{ surrogate_key('request_type') }}                            as request_type_key,
    request_type,
    mode(request_type_code)                                         as request_type_code,
    mode(department_name)                                           as owner_department_name,
    {{ surrogate_key('mode(department_name)') }}                    as owner_department_key,
    count(*)                                                        as requests_all_time,
    min(created_at)                                                 as first_request_at,
    max(created_at)                                                 as last_request_at
from {{ ref('int_311__requests_enriched') }}
group by request_type
