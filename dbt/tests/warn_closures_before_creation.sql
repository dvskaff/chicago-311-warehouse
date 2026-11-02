{{ config(severity='warn') }}
-- Data-quality monitor (warning only): individual requests whose closure precedes creation.
-- They are kept but excluded from resolution-time metrics.
select sr_number, created_at, closed_at
from {{ ref('fct_service_requests') }}
where has_closure_before_creation
