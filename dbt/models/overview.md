{% docs __overview__ %}
# Chicago 311 analytics warehouse

This is the documentation site for a dbt project that turns the City of Chicago's open
[311 Service Requests](https://data.cityofchicago.org/d/v6vf-nfxy) data into a tested star schema in DuckDB.
It is rebuilt every day by GitHub Actions; the public dashboard is at
[dvskaff.github.io/chicago-311-warehouse](https://dvskaff.github.io/chicago-311-warehouse/).

**Layers**

| Layer | What it holds |
|---|---|
| `raw` (source) | Append-only log of every fetched version of each request, loaded by `src/c311/extract.py` |
| `staging` | Latest version of each request, typed and cleaned |
| `intermediate` | Business rules: closure timing, counting rules, the KPI reporting window |
| `marts` | Star schema (`fct_service_requests` + `dim_*`) and KPI tables used by the dashboard |
| `snapshots` | SCD type 2 history of status, owner department and closed date |

**Key definitions**

* **Time to close**: closed date minus created date. Requests whose recorded closure precedes creation are excluded
  from durations and monitored by a data test.
* **Counted request**: not marked as a duplicate of another request. Canceled requests leave the backlog but are not
  used for resolution-time metrics.
* **Reporting window**: the 365 days ending 7 days before the latest data, so every request has had a week to close.
* **Service index**: geometric mean of (time to close / citywide median for the same request type) per community
  area. 1.00 = typical.

Use the lineage button (bottom right) to explore how every model depends on the others.
{% enddocs %}
