-- Every request in staging must be in the fact table exactly once, and nothing else (catches incremental drift).
with s as (select count(*) as n from {{ ref('stg_311__service_requests') }}),
     f as (select count(*) as n from {{ ref('fct_service_requests') }})
select s.n as staging_rows, f.n as fact_rows
from s, f
where s.n != f.n
