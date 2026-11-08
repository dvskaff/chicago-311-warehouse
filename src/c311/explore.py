"""Query the warehouse in the browser: exports the marts for DuckDB-WASM and writes the SQL page (site/sql.html).

After each build the pipeline exports the dbt marts plus a slim copy of the last 12 months of service requests as
gzipped CSV (DuckDB in the browser reads these with no extensions), with the dbt model and column descriptions in
tables.json. The page loads them into an in-browser DuckDB and runs whatever SQL the visitor types.
"""

from __future__ import annotations

import html
import json
import logging
import shutil
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)
RUNTIME = ["duckdb.bundle.mjs", "duckdb-eh.wasm", "duckdb-browser-eh.worker.js", "duckdb-mvp.wasm",
           "duckdb-browser-mvp.worker.js"]  # fmt: skip

SERVICE_REQUESTS = """
select r.created_date, r.closed_date, r.request_type, d.department_name as department,
       a.community_area_name as community_area, r.ward, r.status, r.origin,
       round(r.hours_to_close / 24, 2) as days_to_close, r.closed_within_7d, r.is_open, r.is_canceled
from intermediate.int_311__service_work as r
join marts.dim_department as d using (department_key)
left join marts.dim_community_area as a using (community_area_id), marts.mart_kpi_summary as k
where r.created_date > k.window_start and r.created_date <= k.window_end
order by r.created_date, r.request_type
"""
# (browser table name, dbt model or None, query or None, description if not from dbt)
TABLES = [
    ("service_requests", None, SERVICE_REQUESTS,
     "Every service request created in the dashboard's 12-month window (service work only: duplicates and types "
     "closed on receipt are excluded, as in the KPIs). One row per request; days_to_close is empty while open."),
    ("department_scorecard", "mart_department_scorecard", None, None),
    ("request_type_performance", "mart_request_type_performance", None, None),
    ("community_area_service_index", "mart_community_area_service_index", None, None),
    ("department_monthly_kpis", "mart_department_monthly_kpis", None, None),
    ("daily_department_activity", "fct_daily_department_activity", None, None),
    ("kpi_summary", "mart_kpi_summary", None, None),
    ("dim_request_type", "dim_request_type", None, None),
    ("dim_department", "dim_department", None, None),
    ("dim_community_area", "dim_community_area", None, None),
]  # fmt: skip
SR_COLUMNS = {
    "created_date": "Date the request was created", "closed_date": "Date it was closed (empty if open)",
    "request_type": "What was requested (e.g. pothole, graffiti removal)", "department": "Department responsible",
    "community_area": "One of Chicago's 77 community areas", "ward": "City council ward",
    "status": "Open, Completed or Canceled", "origin": "How it was filed (phone, internet, mobile app, ...)",
    "days_to_close": "Days from creation to closure", "closed_within_7d": "Closed within 7 days",
    "is_open": "Still open at the latest load", "is_canceled": "Canceled rather than completed",
}  # fmt: skip
EXAMPLES = [
    ("Slowest request types",
     "select request_type, count(*) as requests,\n       round(median(days_to_close), 1) as median_days\n"
     "from service_requests\nwhere days_to_close is not null\ngroup by request_type\nhaving count(*) >= 1000\n"
     "order by median_days desc\nlimit 10;"),
    ("Requests per month by department",
     "select date_trunc('month', created_date) as month, department, count(*) as requests\n"
     "from service_requests\ngroup by all\norder by month, requests desc;"),
    ("Does the filing channel matter?",
     "select origin, count(*) as requests,\n       round(avg(closed_within_7d::int) * 100, 1) as pct_closed_in_7_days\n"
     "from service_requests\nwhere not is_canceled\ngroup by origin\norder by requests desc;"),
    ("Slowest neighborhoods (type-adjusted)",
     "select community_area_name, round(service_index, 2) as service_index,\n"
     "       round(median_days_to_close, 1) as median_days, closed_requests\n"
     "from community_area_service_index\norder by service_index desc\nlimit 10;"),
    ("Busiest days of the week",
     "select dayname(created_date) as day, count(*) as requests\nfrom service_requests\n"
     "group by day, isodow(created_date)\norder by isodow(created_date);"),
    ("Backlog over time",
     "select date_day, sum(backlog_end_of_day) as open_requests\nfrom daily_department_activity\n"
     "group by date_day\norder by date_day;"),
]  # fmt: skip


def _descriptions(manifest: Path) -> dict[str, dict]:
    if not manifest.exists():
        return {}
    nodes = json.loads(manifest.read_text()).get("nodes", {})
    out = {}
    for n in nodes.values():
        if n.get("resource_type") == "model":
            out[n["name"]] = {"description": n.get("description", ""),
                              "columns": {c: v.get("description", "") for c, v in n.get("columns", {}).items()}}  # fmt: skip
    return out


def export(db_path: Path, out_dir: Path, manifest: Path) -> list[dict]:
    """Write one gzipped CSV per table and return their metadata (for tables.json)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    docs = _descriptions(manifest)
    con = duckdb.connect(str(db_path), read_only=True)
    meta = []
    try:
        for name, model, query, description in TABLES:
            sql = query or f"select * from marts.{model}"
            cols = con.execute(f"describe ({sql})").fetchall()
            # HUGEINT (from sums) is read back as a double by some clients; plain BIGINT is enough here
            select = ", ".join(f'cast("{c[0]}" as bigint) as "{c[0]}"' if c[1] == "HUGEINT" else f'"{c[0]}"'
                               for c in cols)  # fmt: skip
            path = out_dir / f"{name}.csv.gz"
            con.execute(f"copy (select {select} from ({sql})) to '{path}' (header, compression gzip)")
            rows = con.execute(f"select count(*) from ({sql})").fetchall()[0][0]
            d = docs.get(model or "", {})
            col_docs = SR_COLUMNS if name == "service_requests" else d.get("columns", {})
            meta.append({
                "name": name, "file": f"data/{path.name}", "rows": int(rows), "bytes": path.stat().st_size,
                "description": description or d.get("description", "").strip().split("\n\n")[0],
                "source": "derived from intermediate.int_311__service_work" if query else f"marts.{model}",
                "columns": [{"name": c[0], "type": "BIGINT" if c[1] == "HUGEINT" else c[1],
                             "description": col_docs.get(c[0], "")} for c in cols],
            })  # fmt: skip
    finally:
        con.close()
    total = sum(m["bytes"] for m in meta) / 1e6
    log.info("Browser SQL: exported %d tables (%.1f MB)", len(meta), total)
    return meta


def copy_runtime(vendor: Path, site: Path) -> bool:
    if not all((vendor / f).exists() for f in RUNTIME):
        log.warning("DuckDB-WASM runtime not found in %s; the SQL page will show a notice", vendor)
        return False
    (site / "vendor").mkdir(parents=True, exist_ok=True)
    for f in RUNTIME:
        shutil.copy(vendor / f, site / "vendor" / f)
    return True


# --------------------------------------------------------------------------- page
PAGE_CSS = """
.sqlgrid { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:16px; }
@media (max-width: 900px) { .sqlgrid { grid-template-columns:1fr; } }
textarea#sql { width:100%; min-height:190px; font:13.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  padding:10px 12px; border:1px solid #c3c2b7; border-radius:10px; background:#fff; resize:vertical; }
.bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:8px 0 10px; }
.run { background:#104281; color:#fff; border:0; border-radius:9px; padding:8px 16px; font-weight:600; cursor:pointer; }
.run:disabled { opacity:.55; cursor:default; }
.ex { border:1px solid #c3c2b7; background:#fff; border-radius:999px; padding:3px 11px; font-size:13px;
  color:#104281; cursor:pointer; }
.ghost { border:1px solid #c3c2b7; background:#fff; border-radius:9px; padding:7px 12px; font-size:13px; cursor:pointer; }
#status { font-size:13px; color:#52514e; } #status.err { color:#c4352b; white-space:pre-wrap; }
.tables details { border-bottom:1px solid #e1e0d9; padding:6px 0; } .tables summary { color:#0b0b0b; font-weight:600; }
.tables summary span { color:#898781; font-weight:400; font-size:12px; }
.tables p { font-size:12.5px; color:#52514e; margin:4px 0; }
.tables li { font-size:12.5px; } .tables code { font-size:12px; }
.results { max-height:520px; overflow:auto; border:1px solid #e1e0d9; border-radius:10px; }
.results table { margin:0; } .results th { position:sticky; top:0; background:#fcfcfb; }
"""


def write_page(
    site: Path, meta: list[dict], as_of: str, runtime_ok: bool, repo_url: str, synthetic: bool
) -> None:
    from .report import CSS

    (site / "data").mkdir(parents=True, exist_ok=True)
    (site / "data" / "tables.json").write_text(json.dumps({"as_of": as_of, "tables": meta}, indent=1))
    tables_html = "".join(
        f"<details><summary><code>{m['name']}</code> <span>{m['rows']:,} rows</span></summary>"
        f"<p>{html.escape(m['description'])}</p><ul>"
        + "".join(f"<li><code>{c['name']}</code> <span class='muted'>{c['type'].lower()}</span>"
                  f"{' – ' + html.escape(c['description']) if c['description'] else ''}</li>" for c in m["columns"])
        + "</ul></details>"
        for m in meta
    )  # fmt: skip
    total_mb = sum(m["bytes"] for m in meta) / 1e6
    banner = "<div class='synthetic'>SYNTHETIC TEST DATA: built by the test suite</div>" if synthetic else ""
    examples = json.dumps(EXAMPLES)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Query the Chicago 311 Warehouse</title>
<style>{CSS}{PAGE_CSS}</style></head><body>{banner}
<header><div class="wrap"><h1>Query the warehouse</h1>
<p>Write SQL against the Chicago 311 warehouse right here. DuckDB runs inside your browser on tables exported from
the dbt build (data through {html.escape(as_of)}), so nothing is sent to a server.</p>
<a class="btn" href="./">Back to the dashboard</a><a class="btn" href="dbt-docs/">dbt docs &amp; lineage</a>
<a class="btn" href="{repo_url}">Code on GitHub</a></div></header>
<main><section><div class="sqlgrid"><div>
<div class="bar" id="examples"></div>
<textarea id="sql" spellcheck="false">{html.escape(EXAMPLES[0][1])}</textarea>
<div class="bar"><button class="run" id="run" disabled>Run (Ctrl+Enter)</button>
<button class="ghost" id="csv" disabled>Download results as CSV</button><span id="status">Loading DuckDB…</span></div>
<div class="results" id="results"></div>
<p class="muted">The first visit downloads DuckDB and the tables ({total_mb:.1f} MB); both are cached by your browser
afterwards. Results show up to 1,000 rows; the CSV download has them all.</p>
</div><div class="tables"><h3>Tables</h3>{tables_html}</div></div></section></main>
<footer>Data: City of Chicago Data Portal, 311 Service Requests · <a href="{repo_url}">Source code</a></footer>
<script type="module">
const EXAMPLES = {examples};
const RUNTIME = {"true" if runtime_ok else "false"};
const $ = (id) => document.getElementById(id);
const status = (t, err = false) => {{ $("status").textContent = t; $("status").className = err ? "err" : ""; }};
EXAMPLES.forEach(([label, sql]) => {{
  const b = document.createElement("button"); b.className = "ex"; b.textContent = label;
  b.onclick = () => {{ $("sql").value = sql; run(); }}; $("examples").appendChild(b);
}});
let conn = null, last = null;
// Arrow hands dates and timestamps back as epoch milliseconds: format them by column type
const kind = (f) => {{ const t = String(f.type); return t.startsWith("Date") ? "date" : t.startsWith("Timestamp") ? "ts"
  : /^(Int|Uint|Float|Decimal)/.test(t) ? "num" : "other"; }};
const raw = (v, k) => v === null || v === undefined ? "" : k === "date" ? new Date(Number(v)).toISOString().slice(0, 10)
  : k === "ts" ? new Date(Number(v)).toISOString().slice(0, 19).replace("T", " ") : typeof v === "bigint" ? v.toString() : String(v);
const fmt = (v, k) => typeof v === "number" && k !== "date" && k !== "ts"
  ? (Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, {{maximumFractionDigits: 4}}))
  : typeof v === "bigint" ? v.toLocaleString() : raw(v, k);
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({{"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}})[c]);
async function init() {{
  if (!RUNTIME) throw new Error("The DuckDB runtime is not bundled in this build.");
  const duckdb = await import("./vendor/duckdb.bundle.mjs");
  const base = new URL("./vendor/", location.href).href;
  const bundle = await duckdb.selectBundle({{
    mvp: {{ mainModule: base + "duckdb-mvp.wasm", mainWorker: base + "duckdb-browser-mvp.worker.js" }},
    eh: {{ mainModule: base + "duckdb-eh.wasm", mainWorker: base + "duckdb-browser-eh.worker.js" }},
  }});
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(bundle.mainWorker));
  await db.instantiate(bundle.mainModule);
  conn = await db.connect();
  const meta = await (await fetch("data/tables.json")).json();
  for (const t of meta.tables) {{
    status(`Loading ${{t.name}} (${{t.rows.toLocaleString()}} rows)…`);
    const buf = new Uint8Array(await (await fetch(t.file)).arrayBuffer());
    await db.registerFileBuffer(t.name + ".csv.gz", buf);
    await conn.query(`create table ${{t.name}} as select * from read_csv('${{t.name}}.csv.gz', header = true)`);
  }}
  status(`Ready: ${{meta.tables.length}} tables loaded.`);
  $("run").disabled = false;
}}
async function run() {{
  if (!conn) return;
  $("run").disabled = true; status("Running…");
  const t0 = performance.now();
  try {{
    const res = await conn.query($("sql").value);
    const cols = res.schema.fields.map((f) => f.name);
    const kinds = res.schema.fields.map(kind);
    const rows = res.toArray();
    last = {{cols, kinds, rows}};
    const cls = (i) => kinds[i] === "num" ? "" : "l";  // numbers right-aligned
    const head = "<tr>" + cols.map((c, i) => `<th class="${{cls(i)}}">${{esc(c)}}</th>`).join("") + "</tr>";
    const body = rows.slice(0, 1000).map((r) => "<tr>" + cols.map((c, i) => `<td class="${{cls(i)}}">${{esc(fmt(r[c], kinds[i]))}}</td>`).join("") + "</tr>").join("");
    $("results").innerHTML = `<table><thead>${{head}}</thead><tbody>${{body}}</tbody></table>`;
    status(`${{rows.length.toLocaleString()}} row${{rows.length === 1 ? "" : "s"}} in ${{(performance.now() - t0).toFixed(0)}} ms`);
    $("csv").disabled = rows.length === 0;
  }} catch (e) {{ status(e.message, true); }}
  finally {{ $("run").disabled = false; }}
}}
$("run").onclick = run;
$("sql").addEventListener("keydown", (e) => {{ if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {{ e.preventDefault(); run(); }} }});
$("csv").onclick = () => {{
  if (!last) return;
  const q = (s) => /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  const csv = [last.cols.map(q).join(","),
    ...last.rows.map((r) => last.cols.map((c, i) => q(raw(r[c], last.kinds[i]))).join(","))].join("\\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], {{type: "text/csv"}}));
  a.download = "query_results.csv"; a.click();
}};
init().then(() => run()).catch((e) => status("Could not start DuckDB here: " + e.message, true));
</script></body></html>"""
    (site / "sql.html").write_text(page, encoding="utf-8")
