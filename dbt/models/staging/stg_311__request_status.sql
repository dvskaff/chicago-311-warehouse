-- Narrow view of the fields whose history we want to keep; the snapshot tracks changes to these daily.
select
    sr_number,
    status,
    department_name,
    closed_at_source,
    last_modified_at
from {{ ref('stg_311__service_requests') }}
