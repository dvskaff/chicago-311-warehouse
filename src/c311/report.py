"""Static dashboard (site/index.html) built from the dbt marts and dbt's own run artifacts."""

from __future__ import annotations

import html
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version

from .config import Config

BLUE, ORANGE, AQUA, YELLOW, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#6250d6"
SERIES = [BLUE, ORANGE, AQUA, YELLOW, VIOLET]
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
GOOD, WARN, BAD = "#1a7f37", "#b7791f", "#c4352b"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


# --------------------------------------------------------------------------- plotting helpers
def _style(
    fig: go.Figure, title: str = "", height: int = 360, y_title: str = "", x_title: str = "", **layout
):
    base = dict(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15, color=INK)) if title else None,
        height=height,
        margin=dict(l=64, r=20, t=74 if title else 30, b=48),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, size=12, color=INK2),
        hovermode="x unified",
        hoverlabel=dict(font=dict(family=FONT)),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_layout(**{**base, **layout})
    fig.update_xaxes(
        showgrid=False,
        linecolor=AXIS,
        tickfont=dict(color=MUTED),
        zeroline=False,
        title=dict(text=x_title, font=dict(color=MUTED)),
    )
    fig.update_yaxes(
        gridcolor=GRID,
        linecolor=AXIS,
        tickfont=dict(color=MUTED),
        zeroline=False,
        title=dict(text=y_title, font=dict(color=MUTED)),
    )
    return fig


def _div(fig: go.Figure) -> str:
    return fig.to_html(
        full_html=False, include_plotlyjs=False, config={"displaylogo": False, "responsive": True}
    )


def _days(v) -> str:
    return "–" if pd.isna(v) else f"{v:.1f}"


def _pct(v, signed=False) -> str:
    return "–" if pd.isna(v) else (f"{v:+.0%}" if signed else f"{v:.0%}")


def _pts(v) -> str:
    return "–" if pd.isna(v) else f"{v * 100:+.1f} pts"


def _int(v) -> str:
    return "–" if pd.isna(v) else f"{int(v):,}"


# --------------------------------------------------------------------------- data access
def load_marts(db_path: Path) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        q = lambda sql: con.execute(sql).df()  # noqa: E731
        data = {
            "kpi": q("select * from marts.mart_kpi_summary"),
            "scorecard": q("select * from marts.mart_department_scorecard order by requests desc"),
            "types": q("select * from marts.mart_request_type_performance order by requests desc"),
            "areas": q("select * from marts.mart_community_area_service_index order by service_index desc"),
            "monthly": q("select * from marts.mart_department_monthly_kpis order by month_start"),
            "weekly": q("""
                select d.week_start, sum(a.requests_opened) as opened, sum(a.requests_closed) as closed,
                       sum(a.backlog_end_of_day) filter (where d.date_day = d.week_start + 6) as backlog
                from marts.fct_daily_department_activity a
                join marts.dim_date d using (date_day)
                group by d.week_start order by d.week_start"""),
            "open_points": q("""
                select * from (
                    select latitude, longitude from marts.fct_service_requests
                    where is_open and is_counted and latitude is not null and longitude is not null
                ) using sample 20000 rows (reservoir, 42)"""),
            "load_log": q("select * from raw.load_log order by started_at desc limit 10"),
            "closed_types": q("""
                select request_type, owner_department_name, requests_all_time, median_hours_to_close
                from marts.dim_request_type where is_closed_on_receipt
                order by requests_all_time desc"""),
            "layers": q("""
                select 'Raw (append-only log)' as layer, 'raw.service_requests' as relation,
                       (select count(*) from raw.service_requests) as row_count
                union all select 'Staging (latest version)', 'staging.stg_311__service_requests',
                       (select count(*) from staging.stg_311__service_requests)
                union all select 'Fact (incremental)', 'marts.fct_service_requests',
                       (select count(*) from marts.fct_service_requests)
                union all select 'Snapshot (SCD type 2 history)', 'snapshots.snap_request_status',
                       (select count(*) from snapshots.snap_request_status)
                union all select 'Daily activity', 'marts.fct_daily_department_activity',
                       (select count(*) from marts.fct_daily_department_activity)"""),
        }
    finally:
        con.close()
    return data


def load_artifacts(target: Path) -> dict:
    manifest = json.loads((target / "manifest.json").read_text())
    build = json.loads((target / "build_run_results.json").read_text())
    sources_path = target / "sources.json"
    sources = json.loads(sources_path.read_text()) if sources_path.exists() else {"results": []}

    nodes = manifest["nodes"]
    status = Counter(r["status"] for r in build["results"])
    test_results = [r for r in build["results"] if r["unique_id"].startswith(("test.", "unit_test."))]
    test_status = Counter(r["status"] for r in test_results)
    warnings = [
        (nodes.get(r["unique_id"], {}).get("name", r["unique_id"].split(".")[-1]), r.get("failures"))
        for r in test_results
        if r["status"] == "warn"
    ]
    kinds = Counter(n["resource_type"] for n in nodes.values())
    kinds["unit_test"] = len(manifest.get("unit_tests", {}))

    layers: dict[str, list[tuple[str, str]]] = {
        "Sources": [],
        "Staging": [],
        "Intermediate": [],
        "Marts": [],
        "Snapshots": [],
        "Exposure": [],
    }
    for src in manifest["sources"].values():
        layers["Sources"].append((f"{src['schema']}.{src['name']}", "source"))
    for n in nodes.values():
        if n["resource_type"] == "model":
            folder = n["fqn"][1] if len(n["fqn"]) > 2 else "marts"
            key = {"staging": "Staging", "intermediate": "Intermediate"}.get(folder, "Marts")
            layers[key].append((n["name"], n["config"]["materialized"]))
        elif n["resource_type"] == "snapshot":
            layers["Snapshots"].append((n["name"], "snapshot"))
        elif n["resource_type"] == "seed":
            layers["Sources"].append((n["name"], "seed"))
    for e in manifest.get("exposures", {}).values():
        layers["Exposure"].append((e.get("label") or e["name"], e["type"]))

    fresh = next((r for r in sources["results"] if r["unique_id"].endswith("service_requests")), None)
    return {
        "dbt_version": manifest["metadata"]["dbt_version"],
        "status": status,
        "test_status": test_status,
        "warnings": warnings,
        "kinds": kinds,
        "layers": {k: sorted(v) for k, v in layers.items()},
        "freshness": fresh,
        "elapsed": build.get("elapsed_time", 0),
    }


# --------------------------------------------------------------------------- figures
def fig_flow(weekly: pd.DataFrame) -> go.Figure:
    w = weekly.iloc[:-1].tail(156)  # drop the partial current week
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=w["week_start"],
            y=w["opened"],
            name="Requests opened",
            line=dict(color=BLUE, width=2),
            hovertemplate="%{y:,.0f}",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=w["week_start"],
            y=w["closed"],
            name="Requests closed",
            line=dict(color=ORANGE, width=2),
            hovertemplate="%{y:,.0f}",
        )
    )
    return _style(fig, "Requests opened vs. closed per week", y_title="Requests per week")


def fig_backlog(weekly: pd.DataFrame) -> go.Figure:
    w = weekly.dropna(subset=["backlog"]).tail(156)
    fig = go.Figure(
        go.Scatter(
            x=w["week_start"],
            y=w["backlog"],
            name="Open requests",
            fill="tozeroy",
            fillcolor="rgba(42,120,214,0.12)",
            line=dict(color=BLUE, width=2),
            hovertemplate="%{y:,.0f} open",
        )
    )
    return _style(
        fig, "Citywide backlog (open requests at week end)", y_title="Open requests", showlegend=False
    )


def fig_department_trend(monthly: pd.DataFrame, departments: list[str]) -> go.Figure:
    m = monthly[monthly["is_complete_month"] & monthly["department_name"].isin(departments)]
    m = m[m["month_start"] >= m["month_start"].max() - pd.DateOffset(months=24)]
    fig = go.Figure()
    for name, color in zip(departments, SERIES, strict=False):
        d = m[m["department_name"] == name]
        fig.add_trace(
            go.Scatter(
                x=d["month_start"],
                y=d["median_days_to_close"],
                name=name,
                line=dict(color=color, width=2),
                hovertemplate="%{y:.1f} days",
            )
        )
    _style(
        fig,
        "Median days to close, by month created (top 5 departments)",
        height=400,
        y_title="Median days to close",
        legend=dict(orientation="h", yanchor="top", y=-0.15, x=0),
    )
    fig.update_layout(margin=dict(l=64, r=20, t=60, b=110))
    return fig


def fig_area_index(areas: pd.DataFrame) -> go.Figure:
    a = areas.sort_values("service_index")
    pick = pd.concat([a.head(10), a.tail(10)]).drop_duplicates("community_area_id")
    colors = [ORANGE if v > 1 else BLUE for v in pick["service_index"]]
    fig = go.Figure(
        go.Bar(
            x=pick["service_index"] - 1,
            y=pick["community_area_name"],
            orientation="h",
            base=1,
            marker=dict(color=colors, cornerradius=3),
            customdata=pick[["closed_requests", "median_days_to_close"]].to_numpy(),
            hovertemplate="%{y}: index %{x:.2f}<br>%{customdata[0]:,} closed requests"
            "<br>median %{customdata[1]:.1f} days<extra></extra>",
        )
    )
    fig.add_vline(x=1, line=dict(color=INK2, width=1))
    _style(
        fig,
        "Fastest and slowest community areas (type-adjusted)",
        height=560,
        hovermode="closest",
        x_title="Service index (1.00 = typical for the same request types)",
        showlegend=False,
    )
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11))
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    return fig


def fig_open_map(points: pd.DataFrame) -> go.Figure | None:
    if points.empty:
        return None
    fig = go.Figure(
        go.Densitymap(
            lat=points["latitude"],
            lon=points["longitude"],
            radius=6,
            colorscale=[[0, "rgba(42,120,214,0)"], [0.3, "#7fb2ec"], [0.7, BLUE], [1, "#0b3a75"]],
            showscale=False,
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        map=dict(style="carto-positron", center=dict(lat=41.84, lon=-87.68), zoom=9.3),
        margin=dict(l=0, r=0, t=0, b=0),
        height=520,
        paper_bgcolor=SURFACE,
    )
    return fig


# --------------------------------------------------------------------------- tables & widgets
def _table(headers: list[str], rows: list[list[str]], left_cols: int = 1) -> str:
    th = "".join(f"<th class='{'l' if i < left_cols else ''}'>{h}</th>" for i, h in enumerate(headers))
    body = "".join(
        "<tr>"
        + "".join(f"<td class='{'l' if i < left_cols else ''}'>{c}</td>" for i, c in enumerate(r))
        + "</tr>"
        for r in rows
    )
    return f"<div class='tablewrap'><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>"


def _delta_class(v, good_when_positive: bool) -> str:
    if pd.isna(v) or abs(v) < 0.005:
        return ""
    return "pos" if (v > 0) == good_when_positive else "neg"


def scorecard_table(sc: pd.DataFrame) -> str:
    rows = []
    for r in sc.itertuples(index=False):
        d7 = r.pct_closed_within_7d - r.pct_closed_within_7d_prior
        rows.append(
            [
                html.escape(r.department_name),
                _int(r.requests),
                f"<span class='{_delta_class(r.volume_change_pct, False)}'>{_pct(r.volume_change_pct, True)}</span>",
                _days(r.median_days_to_close),
                _days(r.p90_days_to_close),
                _pct(r.pct_closed_within_7d),
                f"<span class='{_delta_class(d7, True)}'>{_pts(d7)}</span>",
                _int(r.open_requests),
                _int(r.open_over_30_days),
            ]
        )
    return _table(
        [
            "Department",
            "Requests",
            "vs. prior yr",
            "Median days",
            "90th pct days",
            "Closed ≤ 7 days",
            "vs. prior yr",
            "Open now",
            "Open > 30 days",
        ],
        rows,
    )


def types_table(t: pd.DataFrame, n: int = 15) -> str:
    rows = [
        [
            html.escape(r.request_type),
            html.escape(str(r.owner_department_name)),
            _int(r.requests),
            _days(r.median_days_to_close),
            _days(r.p90_days_to_close),
            _pct(r.pct_closed_within_7d),
            _int(r.open_requests),
        ]
        for r in t.head(n).itertuples(index=False)
    ]
    return _table(
        [
            "Request type",
            "Department",
            "Requests",
            "Median days",
            "90th pct days",
            "Closed ≤ 7 days",
            "Open now",
        ],
        rows,
        left_cols=2,
    )


def areas_table(a: pd.DataFrame) -> str:
    rows = [
        [
            html.escape(r.community_area_name),
            f"{r.service_index:.2f}",
            _int(r.closed_requests),
            _days(r.median_days_to_close),
            _pct(r.pct_closed_within_7d),
        ]
        for r in a.sort_values("service_index", ascending=False).itertuples(index=False)
    ]
    return _table(
        ["Community area", "Service index", "Closed requests", "Median days", "Closed ≤ 7 days"], rows
    )


def _breakable(name: str) -> str:
    """Let long model names wrap at underscores and dots instead of mid-word."""
    return html.escape(name).replace("_", "_<wbr>").replace(".", ".<wbr>")


def _card(label: str, value: str, note: str = "") -> str:
    return (
        f"<div class='card'><div class='label'>{label}</div><div class='value'>{value}</div>"
        f"<div class='note'>{note}</div></div>"
    )


def health_section(art: dict, data: dict, load_mode: str, rows_loaded: int) -> str:
    ts, k = art["test_status"], art["kinds"]
    failed = ts.get("fail", 0) + ts.get("error", 0)
    test_line = (
        f"<span class='chip good'>✓ {ts.get('pass', 0)} passed</span>"
        f"<span class='chip warn'>⚠ {ts.get('warn', 0)} warning{'' if ts.get('warn', 0) == 1 else 's'}</span>"
        f"<span class='chip {'bad' if failed else 'good'}'>{'✗' if failed else '✓'} {failed} failed</span>"
    )
    fr = art["freshness"]
    if fr:
        age_h = (fr.get("max_loaded_at_time_ago_in_s") or 0) / 3600
        cls = {"pass": "good", "warn": "warn"}.get(fr["status"], "bad")
        fresh = (
            f"<span class='chip {cls}'>{fr['status'].upper()}</span> newest record modified "
            f"{age_h:.0f} hours before the check (warn after 3 days, error after 7)"
        )
    else:
        fresh = "<span class='chip warn'>not checked</span>"
    last = data["load_log"].iloc[0] if len(data["load_log"]) else None
    load_line = (
        f"{load_mode} load of <b>{rows_loaded:,}</b> new or changed rows at "
        f"{pd.Timestamp(last['started_at']).strftime('%b %d, %Y %H:%M')} UTC"
        if last is not None
        else "–"
    )
    warn_list = "".join(
        f"<li><code>{html.escape(n)}</code>: {f or 0:,} rows flagged</li>" for n, f in art["warnings"]
    )
    warn_html = (
        f"<p class='muted'>Warnings (logged, not blocking):</p><ul class='small'>{warn_list}</ul>"
        if warn_list
        else ""
    )
    layer_rows = [
        [html.escape(r.layer), f"<code>{r.relation}</code>", _int(r.row_count)]
        for r in data["layers"].itertuples(index=False)
    ]
    lineage = "".join(
        f"<div class='lcol'><div class='lhead'>{name}</div>"
        + "".join(f"<div class='lnode'><span>{_breakable(n)}</span><em>{m}</em></div>" for n, m in nodes)
        + "</div>"
        for name, nodes in art["layers"].items()
        if nodes
    )
    return f"""
<div class='grid2'>
  <div>
    <h3>Latest run</h3>
    <p><b>Load:</b> {load_line}</p>
    <p><b>Data tests:</b> {test_line}</p>
    <p><b>Source freshness:</b> {fresh}</p>
    <p class='muted'>dbt {art["dbt_version"]} · {k.get("model", 0)} models · {k.get("test", 0)} data tests ·
       {k.get("unit_test", 0)} unit tests · {k.get("snapshot", 0)} snapshot · {k.get("seed", 0)} seed ·
       build took {art["elapsed"]:.0f}s</p>
    {warn_html}
  </div>
  <div><h3>Rows by layer</h3>{_table(["Layer", "Relation", "Rows"], layer_rows, left_cols=2)}</div>
</div>
<h3>Lineage</h3>
<div class='lineage'>{lineage}</div>
"""


# --------------------------------------------------------------------------- page
CSS = """
:root { --ink:#0b0b0b; --ink2:#52514e; --muted:#898781; --grid:#e1e0d9; --surface:#fcfcfb; --page:#f9f9f7; }
* { box-sizing:border-box; }
body { margin:0; background:var(--page); color:var(--ink); font-family:%FONT%; line-height:1.55; }
header { background:#104281; color:#fff; padding:40px 16px 32px; }
header .wrap, main { max-width:1140px; margin:0 auto; }
header h1 { margin:0 0 6px; font-size:clamp(24px,4vw,34px); letter-spacing:-0.01em; }
header p { margin:0; color:#cde2fb; max-width:820px; }
header a { color:#fff; }
.synthetic { background:#c4352b; color:#fff; text-align:center; padding:8px 16px; font-weight:600; }
main { padding:24px 16px 64px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; margin-bottom:28px; }
.card { background:var(--surface); border:1px solid rgba(11,11,11,.10); border-radius:12px; padding:14px 16px; }
.card .label { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
.card .value { font-size:25px; font-weight:650; margin:4px 0 2px; }
.card .note { font-size:12.5px; color:var(--ink2); }
section { background:var(--surface); border:1px solid rgba(11,11,11,.10); border-radius:12px; padding:20px 20px 10px;
          margin-bottom:20px; }
section h2 { margin:0 0 4px; font-size:19px; }
section > p { margin:4px 0 12px; color:var(--ink2); max-width:900px; }
h3 { font-size:15px; margin:14px 0 6px; }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr)); gap:8px 24px; }
.grid2 > * { min-width:0; }
.tablewrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:13.5px; font-variant-numeric:tabular-nums; margin:6px 0 14px; }
th, td { text-align:right; padding:7px 9px; border-bottom:1px solid var(--grid); white-space:nowrap; }
th.l, td.l { text-align:left; white-space:normal; }
th { color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; }
.pos { color:#1a7f37; } .neg { color:#c4352b; }
.muted { color:var(--muted); font-size:13px; }
.small { font-size:13px; color:var(--ink2); }
code { background:#f0efec; border-radius:5px; padding:1px 5px; font-size:12.5px; }
.chip { display:inline-block; border-radius:999px; padding:1px 10px; font-size:12.5px; font-weight:600;
        margin-right:6px; }
.chip.good { background:#e3f3e7; color:#1a7f37; } .chip.warn { background:#fbf0d9; color:#8a5a00; }
.chip.bad { background:#fbe3e1; color:#c4352b; }
.lineage { display:flex; gap:10px; overflow-x:auto; padding-bottom:8px; margin-bottom:10px; }
.lcol { flex:1 0 170px; background:#f4f3ef; border-radius:10px; padding:10px; }
.lhead { font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); font-weight:700;
         margin-bottom:6px; }
.lnode { background:#fff; border:1px solid var(--grid); border-radius:7px; padding:5px 8px; margin-bottom:6px;
         font-size:12.5px; }
.lnode span { display:block; overflow-wrap:anywhere; }
.lnode em { display:block; color:var(--muted); font-style:normal; font-size:11px; }
details summary { cursor:pointer; color:#2a78d6; margin:4px 0 10px; }
ul.method li { margin-bottom:6px; color:var(--ink2); }
.btn { display:inline-block; background:#fff; color:#104281; border-radius:8px; padding:6px 12px; margin:12px 8px 0 0;
       font-weight:600; text-decoration:none; font-size:14px; }
.btn.dark { background:#104281; color:#fff; margin:4px 0 14px; }
footer { text-align:center; color:var(--muted); font-size:13px; padding:0 16px 40px; }
""".replace("%FONT%", FONT)

LAYOUT_JS = """
window.addEventListener("load", () => document.querySelectorAll(".plotly-graph-div").forEach(d => {
  if (window.innerWidth < 640 && d.layout && d.layout.showlegend !== false && d.data.length > 1) {
    Plotly.relayout(d, {"legend.y": -0.22, "legend.yanchor": "top", "legend.x": 0, "legend.xanchor": "left",
                        "margin.t": 50, "margin.b": Math.max((d.layout.margin || {}).b || 0, 130)});
  }
  Plotly.Plots.resize(d);
}));
"""


def build_site(
    cfg: Config,
    target: Path,
    *,
    load_mode: str,
    rows_loaded: int,
    synthetic: bool = False,
    boundaries: pd.DataFrame | None = None,
) -> dict:
    site = cfg.site_dir
    site.mkdir(parents=True, exist_ok=True)
    data, art = load_marts(cfg.db_path), load_artifacts(target)
    k = data["kpi"].iloc[0]
    sc = data["scorecard"]
    top5 = sc.head(5)["department_name"].tolist()
    as_of = pd.Timestamp(k["data_as_of"])
    window = f"{pd.Timestamp(k['window_start']) + pd.Timedelta(days=1):%b %d, %Y} – {pd.Timestamp(k['window_end']):%b %d, %Y}"
    vol = k["requests"] / k["requests_prior"] - 1 if k["requests_prior"] else float("nan")
    d7 = k["pct_closed_within_7d"] - k["pct_closed_within_7d_prior"]

    docs = target / "static_index.html"
    if docs.exists():
        (site / "dbt-docs").mkdir(exist_ok=True)
        shutil.copy(docs, site / "dbt-docs" / "index.html")

    ct = data["closed_types"]
    if len(ct):
        names = ", ".join(html.escape(n) for n in ct["request_type"].head(3))
        type_rows = [
            [
                html.escape(r.request_type),
                html.escape(str(r.owner_department_name)),
                _int(r.requests_all_time),
                f"{r.median_hours_to_close * 60:.0f} min",
            ]
            for r in ct.itertuples(index=False)
        ]
        closed_note = (
            f"<b>{len(ct)} request type{'' if len(ct) == 1 else 's'} the city closes on receipt</b> "
            f"({_int(k['closed_on_receipt_requests'])} "
            f"requests in the last 12 months; largest: {names}). Their median time to close is under an hour, so "
            "they are logged rather than worked and would make service look faster than it is.</p>"
            f"<details><summary>Show the excluded request type{'' if len(ct) == 1 else 's'}</summary>"
            + _table(
                ["Request type", "Department", "Requests since 2023", "Median time to close"],
                type_rows,
                left_cols=2,
            )
            + "</details>"
        )
    else:
        closed_note = "request types the city closes on receipt (none detected in this data).</p>"
    cards = "".join(
        [
            _card("Requests, last 12 months", _int(k["requests"]), f"{_pct(vol, True)} vs. prior 12 months"),
            _card(
                "Median time to close",
                f"{_days(k['median_days_to_close'])} days",
                "half of requests close faster",
            ),
            _card("Closed within 7 days", _pct(k["pct_closed_within_7d"]), f"{_pts(d7)} vs. prior 12 months"),
            _card(
                "Open right now",
                _int(k["open_requests"]),
                f"{_int(k['open_over_30_days'])} open more than 30 days",
            ),
            _card("Data as of", f"{as_of:%b %d, %Y}", "refreshed daily by GitHub Actions"),
        ]
    )
    map_fig = fig_open_map(data["open_points"])
    map_html = _div(map_fig) if map_fig else "<p class='muted'>No open requests with coordinates.</p>"
    areas = data["areas"]
    choropleth, map_note = "", ""
    if boundaries is not None and len(boundaries) and len(areas):
        from . import geo

        fc = geo.feature_collection(boundaries, areas, str(as_of.date()))
        if (
            not synthetic or cfg.map_path != Config().map_path
        ):  # test data never overwrites the repository map
            geo.write(cfg, fc)
        fig = geo.fig_map(fc)
        if fig is not None:
            choropleth = _div(fig)
            map_note = (
                f"<p class='muted'>The same map is in the repository as "
                f"<a href='{cfg.repo_url}/blob/main/maps/community_area_service_index.geojson'>a GeoJSON file</a>, "
                "which GitHub shows as an interactive map. Gray areas had too few closed requests to score.</p>"
            )
    area_html = (
        choropleth
        + map_note
        + f"<div class='grid2'>{_div(fig_area_index(areas))}<div><h3>Where open requests are right now</h3>"
        f"{map_html}<p class='muted'>Density of a random sample of up to 20,000 open requests.</p></div></div>"
        f"<details><summary>All {len(areas)} community areas</summary>{areas_table(areas)}</details>"
        if len(areas)
        else map_html
    )
    banner = (
        (
            "<div class='synthetic'>SYNTHETIC TEST DATA: this build was produced by the test suite, "
            "not from the City of Chicago feed.</div>"
        )
        if synthetic
        else ""
    )
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chicago 311 Service Performance</title>
<meta name="description" content="Daily-refreshed dbt + DuckDB warehouse of Chicago 311 service requests: resolution times, backlog and neighborhood equity.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏙️</text></svg>">
<script src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js" charset="utf-8"></script>
<style>{CSS}</style></head>
<body>{banner}
<header><div class="wrap">
  <h1>Chicago 311 Service Performance</h1>
  <p>How quickly does the City of Chicago resolve 311 requests for potholes, rats, broken street lights and more,
  and is service equally fast in every neighborhood? Built from the city's open data with an analytics warehouse
  (DuckDB + dbt) that reloads, tests and republishes itself every day.</p>
  <a class="btn" href="sql.html">Query the warehouse (SQL in your browser)</a>
  <a class="btn" href="dbt-docs/">dbt docs &amp; lineage</a><a class="btn" href="{cfg.repo_url}">Code on GitHub</a>
</div></header>
<main>
<div class="cards">{cards}</div>

<section><h2>Demand and throughput</h2>
<p>Service requests created since January 2023. When the closed line sits below the opened line, the backlog
grows. Information-only calls and duplicates are excluded, and so are {closed_note}
<div class="grid2">{_div(fig_flow(data["weekly"]))}{_div(fig_backlog(data["weekly"]))}</div></section>

<section><h2>Department scorecard</h2>
<p>Requests created {window} (the 12 months ending a week before the latest data, so every request has had a
week to close), compared with the 12 months before. "Open now" counts every unresolved request.</p>
{scorecard_table(sc)}
{_div(fig_department_trend(data["monthly"], top5))}</section>

<section><h2>Request types</h2>
<p>The 15 most common request types in the same 12-month window.</p>
{types_table(data["types"])}</section>

<section id="map"><h2>Is service equally fast in every neighborhood?</h2>
<p>Raw medians would mislead, because neighborhoods file different kinds of requests. Each closed request is
compared with the citywide median for its own request type; a community area's <b>service index</b> is the
median of those ratios. 1.00 means a typical request there closes as fast as the citywide typical request of
the same type; 1.20 means 20% slower, 0.80 means 20% faster.
Areas with fewer than 200 closed requests in the window are left out.</p>
{area_html}</section>

<section><h2>Query it yourself</h2>
<p>The warehouse's marts and the last 12 months of service requests are exported after every build. The
<a href="sql.html"><b>SQL page</b></a> loads them into DuckDB running inside your browser, so you can ask your own
questions (slowest request types, busiest days, filing channels, any neighborhood) with nothing to install.</p>
<a class="btn dark" href="sql.html">Open the SQL page</a></section>

<section><h2>Pipeline health</h2>
<p>Every run loads new and changed rows, rebuilds the models in dependency order, and runs the full test suite;
failing tests stop the dashboard from being published. Results of the latest run:</p>
{health_section(art, data, load_mode, rows_loaded)}</section>

<section><h2>How it works</h2><ul class="method">
<li><b>Extract &amp; load:</b> a Python loader pulls the City of Chicago's 311 Service Requests dataset from the
Socrata API into DuckDB. The first run backfills from 2023 month by month; later runs fetch only rows modified
since the last watermark (with a 48-hour overlap) into an append-only raw table.</li>
<li><b>Transform (dbt):</b> staging keeps the latest version of each request and casts types; an intermediate
model holds the business rules; marts form a star schema (fact table plus date, department, request-type and
community-area dimensions) and KPI tables. The fact table is incremental and has an enforced contract.</li>
<li><b>History:</b> a dbt snapshot records every change in status, owner department and closed date as a
type 2 slowly changing dimension, history the source itself overwrites.</li>
<li><b>Quality:</b> generic, singular and unit tests in dbt (uniqueness, relationships, reconciliation between
layers, backlog sanity, closure-timing checks) plus source-freshness monitoring; pytest covers the loader.</li>
<li><b>Orchestration &amp; hosting:</b> GitHub Actions runs everything daily, keeps the DuckDB warehouse as a
release asset between runs, and deploys this page and the dbt docs to GitHub Pages.</li>
<li><b>Definitions:</b> time to close = closed date − created date (requests whose recorded closure precedes
creation are excluded from durations and flagged by a data test); canceled requests leave the backlog when
canceled but are not counted in resolution-time metrics.</li>
</ul></section>
</main>
<footer>Data: City of Chicago Data Portal, 311 Service Requests (v6vf-nfxy) ·
<a href="{cfg.repo_url}">Source code</a></footer>
<script>{LAYOUT_JS}</script>
</body></html>"""
    (site / "index.html").write_text(page, encoding="utf-8")
    (site / ".nojekyll").touch()

    ts = art["test_status"]
    summary = {
        "data_as_of": str(as_of),
        "window_start": str(k["window_start"]),
        "window_end": str(k["window_end"]),
        "requests": int(k["requests"]),
        "requests_prior": int(k["requests_prior"]),
        "median_days_to_close": float(k["median_days_to_close"]),
        "pct_closed_within_7d": float(k["pct_closed_within_7d"]),
        "pct_closed_within_7d_prior": float(k["pct_closed_within_7d_prior"]),
        "open_requests": int(k["open_requests"]),
        "open_over_30_days": int(k["open_over_30_days"]),
        "requests_in_warehouse": int(k["requests_in_warehouse"]),
        "closed_on_receipt_requests": int(k["closed_on_receipt_requests"] or 0),
        "closed_on_receipt_types": int(k["closed_on_receipt_types"] or 0),
        "departments": sc[
            ["department_name", "requests", "median_days_to_close", "pct_closed_within_7d", "open_requests"]
        ]
        .head(8)
        .to_dict(orient="records"),
        "community_areas_indexed": int(len(areas)),
        "slowest_area": areas.iloc[0][["community_area_name", "service_index"]].to_dict()
        if len(areas)
        else None,
        "fastest_area": areas.iloc[-1][["community_area_name", "service_index"]].to_dict()
        if len(areas)
        else None,
        "dbt": {
            "models": art["kinds"].get("model", 0),
            "data_tests": art["kinds"].get("test", 0),
            "unit_tests": art["kinds"].get("unit_test", 0),
            "passed": ts.get("pass", 0),
            "warnings": ts.get("warn", 0),
            "failed": ts.get("fail", 0) + ts.get("error", 0),
        },
        "freshness": art["freshness"]["status"] if art["freshness"] else None,
        "load_mode": load_mode,
        "rows_loaded": rows_loaded,
        "synthetic": synthetic,
        "built_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    (site / "results.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary
