{#-
    The requests that performance metrics are about: not duplicates, and not of a type the city closes on receipt.
    Every KPI mart reads from here so the definition lives in one place.
-#}
select
    r.*,
    t.request_type,
    t.owner_department_name
from {{ ref('fct_service_requests') }} as r
join {{ ref('dim_request_type') }} as t using (request_type_key)
where r.is_counted
  and not t.is_closed_on_receipt
