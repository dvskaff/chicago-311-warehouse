# Chicago 311 Analytics Warehouse: dbt & DuckDB

[![CI](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/ci.yml)
[![Daily pipeline](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/pipeline.yml/badge.svg)](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/pipeline.yml)
![dbt](https://img.shields.io/badge/dbt-1.12-orange) ![DuckDB](https://img.shields.io/badge/DuckDB-2.0-yellow)
![Python](https://img.shields.io/badge/python-3.13-blue)

* **Dashboard:** [live results, updated daily](https://dvskaff.github.io/chicago-311-warehouse/)
* **SQL:** [query the warehouse in your browser](https://dvskaff.github.io/chicago-311-warehouse/sql.html)
* **Docs:** [dbt documentation and lineage graph](https://dvskaff.github.io/chicago-311-warehouse/dbt-docs/)
* **Map:** [service speed by neighborhood](https://dvskaff.github.io/chicago-311-warehouse/#map) · [GeoJSON file for GIS tools](maps/community_area_service_index.geojson)

An end-to-end analytics engineering project on real city data. Every day a GitHub Action pulls new and updated
311 service requests (potholes, rats, street lights, graffiti...) from the City of Chicago's open-data API, loads
them into a DuckDB warehouse, rebuilds a tested dbt star schema, and republishes a dashboard answering:

* How many requests does the city receive, and is the backlog growing or shrinking?
* How fast does each department close requests, and is it getting better than last year?
* Is service equally fast in every neighborhood once you compare like with like?

## Results

Rewritten automatically after every daily run.

<!-- RESULTS:START -->
_Data through Nov 14, 2026 · requests created Nov 08, 2025 – Nov 07, 2026 · 4,934,668 requests in the warehouse_

| Metric | Last 12 months | vs. prior 12 months |
|---|---|---|
| Service requests | 811,247 | +12.6% |
| Median time to close | 2.9 days | |
| Closed within 7 days | 56.5% | -2.5 pts |
| Open right now | 148,261 (125,182 older than 30 days) | |

_Service work only: duplicates, information-only calls and 5 request types the city closes on receipt (518,390 requests in the window, e.g. complaints that are only logged) are excluded from these metrics._

| Department | Requests | Median days to close | Closed within 7 days | Open now |
|---|---|---|---|---|
| Streets and Sanitation | 483,401 | 2.9 | 59% | 64,630 |
| CDOT - Department of Transportation | 163,136 | 3.0 | 56% | 33,092 |
| DWM - Department of Water Management | 64,621 | 1.8 | 49% | 35,973 |
| DOB - Buildings | 41,721 | 21.9 | 22% | 10,649 |
| Animal Care and Control | 23,851 | 0.9 | 81% | 1,334 |
| Outside Agencies | 13,886 | 0.1 | 92% | 477 |
| BACP - Business Affairs and Consumer Protection | 11,852 | 26.7 | 21% | 1,845 |
| City Clerk's Office | 4,572 | 3.8 | 68% | 104 |

**Neighborhood equity:** across 77 community areas, the slowest is West Garfield Park, where requests take 1.46× the typical time to close for the same request types; the fastest is Armour Square at 0.66×.

**Latest run:** 16 dbt models, 71 data tests and 3 unit tests: 72 passed, 2 warnings, 0 failed · source freshness: pass · 2026-11-14 17:15 UTC
<!-- RESULTS:END -->

## Explore it yourself

* **SQL in the browser.** The [SQL page](https://dvskaff.github.io/chicago-311-warehouse/sql.html) loads the marts and
  the last 12 months of service requests into DuckDB running inside your browser (DuckDB-WASM, served by the site
  itself). Write any query, or start from the examples: slowest request types, filing channels, busiest days, backlog.
  The tables are re-exported after every daily build, with the dbt descriptions of every column.
* **Neighborhood map.** The [dashboard's map](https://dvskaff.github.io/chicago-311-warehouse/#map) colors each of
  the 77 community areas by its service index (hover for its numbers). The same polygons and numbers are rewritten
  every day to [`maps/community_area_service_index.geojson`](maps/community_area_service_index.geojson) for GIS tools.
  Boundaries come from the city's open data and are kept in the warehouse after the first download.

## Architecture

```mermaid
flowchart LR
    API["Chicago Data Portal<br/>311 API"] -->|"backfill, then daily<br/>incremental loads"| RAW[("raw<br/>append-only log")]
    RAW --> STG["staging<br/>latest version, typed"]
    STG --> INT["intermediate<br/>business rules"]
    INT --> MARTS["marts<br/>star schema + KPI tables"]
    STG --> SNAP["snapshot<br/>SCD type 2 history"]
    TESTS{{"74 dbt tests<br/>+ freshness"}} -.->|"gate"| MARTS
    MARTS --> DASH["dashboard<br/>+ neighborhood map"]
    MARTS --> SQL["SQL in the browser<br/>DuckDB-WASM"]
    MARTS --> DOCS["dbt docs<br/>& lineage"]
```

<details><summary>Every model, layer by layer</summary>

```
Chicago Data Portal (Socrata API, dataset v6vf-nfxy)
        │  src/c311/extract.py: backfill month by month, then daily incremental loads by
        │  last_modified_date watermark (48 h overlap), schema checked on every run
        ▼
raw.service_requests       append-only log of every fetched version (all text, + batch id, load time)
raw.load_log               one row per load: mode, rows, watermark
        │  dbt (dbt/)
        ▼
staging        stg_311__service_requests   latest version per request, typed and cleaned (view)
intermediate   int_311__requests_enriched  closure timing, counting rules (view)
               int_311__service_work       what KPIs measure: no duplicates, no types closed on receipt
               int_311__kpi_window         reporting window, defined once (ephemeral)
marts          fct_service_requests        fact, 1 row per request (incremental, enforced contract)
               dim_date · dim_department · dim_request_type · dim_community_area (seed)
               fct_daily_department_activity, mart_department_scorecard, mart_department_monthly_kpis,
               mart_request_type_performance, mart_community_area_service_index, mart_kpi_summary
snapshots      snap_request_status         SCD type 2 history of status / department / closed date
exposure       chicago_311_dashboard       the public dashboard, declared in dbt for lineage
        │  src/c311/report.py
        ▼
GitHub Pages   dashboard + dbt docs site (static) · sql.html (DuckDB-WASM on exported marts)
```

</details>

**Data quality.** More than 70 dbt data tests run on every build: uniqueness and not-null keys, relationships between
the fact and every dimension, accepted values, range checks, an enforced model contract on the fact table,
singular tests that reconcile row counts between layers and check that no department's backlog goes negative, and
warning-level monitors for data-entry errors (closures recorded before creation). dbt unit tests cover the
de-duplication and closure-timing logic, `dbt source freshness` alerts if the city feed goes stale, and pytest covers
the loader (schema drift, paging, incremental watermark). A failing test stops the site from being published.

**Persistence without a cloud account.** GitHub Actions runners are temporary, so the DuckDB file (raw log, models
and snapshot history) is compressed and saved as an asset on the `warehouse` release after every run, then restored
at the start of the next one. You can download it from the Releases page and query it with DuckDB.

## Repository layout

| Path | What it does |
|---|---|
| `src/c311/extract.py` | Socrata client, schema mapping, backfill and incremental loads into `raw` |
| `dbt/` | dbt project: sources, staging, intermediate, marts, snapshot, seed, tests, macros, docs |
| `src/c311/report.py` | Builds the dashboard from the marts and dbt's run artifacts |
| `src/c311/explore.py` | Exports the marts for the browser and writes the SQL page (`sql.html`) |
| `src/c311/geo.py` | Community-area boundaries, the map file in `maps/` and the dashboard's choropleth |
| `scripts/fetch_web_runtime.sh` | Fetches DuckDB-WASM from npm and bundles it for the SQL page |
| `src/c311/pipeline.py` | Runs everything in order: load → `dbt build` → freshness → docs → site → README |
| `src/c311/fixture.py` | Synthetic, clearly labeled test data so CI can run the whole pipeline offline |
| `tests/` | pytest suite, including a full dbt build on the synthetic data |
| `.github/workflows/` | `ci.yml` (lint, dbt parse, tests) and `pipeline.yml` (daily load, build, deploy) |

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                                                  # ~30 seconds, offline
PYTHONPATH=src python -m c311.pipeline                  # real data: first run backfills since 2023
cd dbt && dbt docs serve --profiles-dir .               # browse the docs and lineage graph
```

## Data

[City of Chicago 311 Service Requests](https://data.cityofchicago.org/Service-Requests/311-Service-Requests/v6vf-nfxy)
(Chicago Data Portal), requests created since January 1, 2023. Information-only calls are excluded at extraction
because they are not service work, and legacy records migrated from the pre-2018 system are excluded in staging.
Request types the city closes on receipt (median time to close under an hour, such as complaints that are only
logged) stay in the warehouse but are excluded from timing and backlog metrics, because they would make service look
faster than it is.
No API key or account is needed.

## Stack

Python 3.13 · DuckDB · dbt Core (dbt-duckdb) · pandas · Plotly · pytest · ruff · GitHub Actions · GitHub Pages
