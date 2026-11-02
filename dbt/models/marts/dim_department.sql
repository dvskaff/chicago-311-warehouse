select
    {{ surrogate_key('department_name') }}   as department_key,
    department_name,
    count(distinct request_type)              as request_types,
    min(created_at)                           as first_request_at,
    max(created_at)                           as last_request_at
from {{ ref('int_311__requests_enriched') }}
group by department_name
