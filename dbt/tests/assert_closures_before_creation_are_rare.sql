-- Fails the build if more than 1% of closed requests have a closure recorded before creation
-- (a sign that the source feed or the timestamp parsing is broken).
select avg(has_closure_before_creation::int) as share_of_closed_requests
from {{ ref('fct_service_requests') }}
where closed_at is not null
having avg(has_closure_before_creation::int) > 0.01
