# Chicago 311 Analytics Warehouse: dbt & DuckDB

[![CI](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/ci.yml)
[![Daily pipeline](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/pipeline.yml/badge.svg)](https://github.com/dvskaff/chicago-311-warehouse/actions/workflows/pipeline.yml)
![dbt](https://img.shields.io/badge/dbt-1.12-orange) ![DuckDB](https://img.shields.io/badge/DuckDB-2.0-yellow)
![Python](https://img.shields.io/badge/python-3.13-blue)

**Live dashboard: https://dvskaff.github.io/chicago-311-warehouse/** ·
**dbt docs & lineage: https://dvskaff.github.io/chicago-311-warehouse/dbt-docs/**

An end-to-end analytics engineering project on real city data. Every day a GitHub Action pulls new and updated
311 service requests (potholes, rats, street lights, graffiti...) from the City of Chicago's open-data API, loads
them into a DuckDB warehouse, rebuilds a tested dbt star schema, and republishes a dashboard answering:

* How many requests does the city receive, and is the backlog growing or shrinking?
* How fast does each department close requests, and is it getting better than last year?
* Is service equally fast in every neighborhood once you compare like with like?

## Results

Rewritten automatically after every daily run.

<!-- RESULTS:START -->
_Results appear here after the first run of the **Daily pipeline** workflow._
<!-- RESULTS:END -->

## Architecture

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
               int_311__kpi_window         reporting window, defined once (ephemeral)
marts          fct_service_requests        fact, 1 row per request (incremental, enforced contract)
               dim_date · dim_department · dim_request_type · dim_community_area (seed)
               fct_daily_department_activity, mart_department_scorecard, mart_department_monthly_kpis,
               mart_request_type_performance, mart_community_area_service_index, mart_kpi_summary
snapshots      snap_request_status         SCD type 2 history of status / department / closed date
exposure       chicago_311_dashboard       the public dashboard, declared in dbt for lineage
        │  src/c311/report.py
        ▼
GitHub Pages   dashboard + dbt docs site (static)
```

**Data quality.** About 70 dbt data tests run on every build: uniqueness and not-null keys, relationships between
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
No API key or account is needed.

## Stack

Python 3.13 · DuckDB · dbt Core (dbt-duckdb) · pandas · Plotly · pytest · ruff · GitHub Actions · GitHub Pages
