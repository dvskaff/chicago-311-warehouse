select
    community_area_id,
    community_area_name
from {{ ref('community_areas') }}
