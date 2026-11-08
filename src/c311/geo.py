"""Community-area map: official boundaries from the city's open data portal, joined to the service index.

The boundaries are downloaded once and kept in the warehouse (raw.community_area_boundaries), so later runs don't
depend on the portal. The pipeline writes two files:

* maps/community_area_service_index.geojson, committed to the repository: GitHub renders it as an interactive map,
  with colors from the simplestyle properties (blue = faster than typical, orange = slower).
* the same polygons for the dashboard's choropleth (drawn by Plotly from the GeoJSON itself, no map tiles needed).
"""

from __future__ import annotations

import json
import logging
import math
import re

import duckdb
import pandas as pd

from .config import Config

log = logging.getLogger(__name__)
SOURCES = [  # the current "Boundaries - Community Areas" dataset (igwz-8jzy), two ways to ask for it
    "https://data.cityofchicago.org/resource/igwz-8jzy.geojson?$limit=1000",
    "https://data.cityofchicago.org/api/v3/views/igwz-8jzy/query.geojson?accessType=DOWNLOAD",
]
NUMBER_KEYS = ("area_numbe", "area_num_1", "area_number", "area_num", "community_area")
NAME_KEYS = ("community", "community_area_name", "name")


# --------------------------------------------------------------------------- boundaries
def _norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", str(name).lower())


def parse(geojson: dict, areas: pd.DataFrame) -> pd.DataFrame:
    """One row per community area id: id, name, geometry (GeoJSON dict). Matches by area number, else by name."""
    by_name = {
        _norm(n): int(i)
        for i, n in zip(areas["community_area_id"], areas["community_area_name"], strict=True)
    }
    rows = []
    for f in geojson.get("features", []):
        props = {k.lower(): v for k, v in (f.get("properties") or {}).items()}
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        area_id = None
        for k in NUMBER_KEYS:
            try:
                area_id = int(float(props[k]))
                break
            except (KeyError, TypeError, ValueError):
                continue
        if area_id is None:
            name = next((props[k] for k in NAME_KEYS if props.get(k)), "")
            area_id = by_name.get(_norm(name))
        if area_id is not None:
            rows.append({"community_area_id": area_id, "geometry": json.dumps(geom)})
    return pd.DataFrame(rows, columns=["community_area_id", "geometry"]).drop_duplicates("community_area_id")


def ensure_boundaries(cfg: Config, fetch=None) -> pd.DataFrame:
    """Boundaries from the warehouse, downloading them the first time. Empty if they can't be had."""
    con = duckdb.connect(str(cfg.db_path))
    try:
        con.execute("create schema if not exists raw")
        con.execute("create table if not exists raw.community_area_boundaries "
                    "(community_area_id integer primary key, geometry varchar)")  # fmt: skip
        have = con.execute("select count(*) from raw.community_area_boundaries").fetchall()[0][0]
        if have < 70:
            areas = con.execute(
                "select community_area_id, community_area_name from marts.dim_community_area"
            ).df()
            gj = (fetch or download)()
            b = parse(gj, areas) if gj else pd.DataFrame(columns=["community_area_id", "geometry"])
            if len(b) >= 70:
                con.execute("delete from raw.community_area_boundaries")
                con.register("b", b)
                con.execute(
                    "insert into raw.community_area_boundaries select community_area_id, geometry from b"
                )
                log.info("Community-area boundaries: %d areas saved in the warehouse", len(b))
            else:
                log.warning("Community-area boundaries unavailable (%d matched); the map is skipped", len(b))
        return con.execute("select * from raw.community_area_boundaries order by 1").df()
    finally:
        con.close()


def download() -> dict | None:
    import requests

    for url in SOURCES:
        try:
            r = requests.get(url, timeout=60,
                             headers={"User-Agent": "chicago-311-warehouse (github.com/dvskaff/chicago-311-warehouse)"})  # fmt: skip
            r.raise_for_status()
            gj = r.json()
            if gj.get("features"):
                return gj
        except Exception as exc:  # the map is a nice-to-have; the warehouse must not depend on it
            log.warning("Could not download boundaries from %s (%s)", url, exc)
    return None


# --------------------------------------------------------------------------- geometry
def _simplify(points: list, tol: float) -> list:
    """Douglas-Peucker on one ring (keeps the first and last point, so rings stay closed)."""
    if len(points) < 5:
        return points

    def dist(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p[:2], a[:2], b[:2]
        dx, dy = x2 - x1, y2 - y1
        if dx == dy == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0.0, None
        for k in range(i + 1, j):
            d = dist(points[k], points[i], points[j])
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    out = [p for p, k in zip(points, keep, strict=True) if k]
    return out if len(out) >= 4 else points


def simplify(geom: dict, tol: float = 0.00012, digits: int = 5) -> dict:
    """~10 m tolerance and ~1 m precision: the map looks the same and the file is many times smaller."""

    def ring(r):
        return [[round(x, digits), round(y, digits)] for x, y, *_ in _simplify(r, tol)]

    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in geom["coordinates"]]}
    return {"type": "MultiPolygon", "coordinates": [[ring(r) for r in poly] for poly in geom["coordinates"]]}


# --------------------------------------------------------------------------- outputs
def color(index: float | None) -> str:
    """Diverging: blue = faster than the citywide typical request of the same type, orange = slower."""
    if index is None or pd.isna(index):
        return "#d9d8d2"
    t = max(-1.0, min(1.0, (index - 1.0) / 0.4))
    lo, mid, hi = (42, 120, 214), (240, 239, 234), (235, 104, 52)
    end = hi if t > 0 else lo
    c = [round(m + abs(t) * (e - m)) for m, e in zip(mid, end, strict=True)]
    return "#{:02x}{:02x}{:02x}".format(*c)


def feature_collection(boundaries: pd.DataFrame, areas: pd.DataFrame, as_of: str) -> dict:
    a = areas.set_index("community_area_id")
    names = a["community_area_name"].to_dict() if "community_area_name" in a else {}
    feats = []
    for r in boundaries.itertuples():
        idx = a["service_index"].get(r.community_area_id) if "service_index" in a else None
        row = a.loc[r.community_area_id] if r.community_area_id in a.index else None
        has = row is not None and not pd.isna(idx)
        props = {
            "community_area": names.get(r.community_area_id, str(r.community_area_id)),
            "community_area_id": int(r.community_area_id),
            "service_index": round(float(idx), 2) if has else None,
            "median_days_to_close": round(float(row["median_days_to_close"]), 1) if has else None,
            "closed_within_7_days": f"{row['pct_closed_within_7d']:.0%}" if has else None,
            "closed_requests": int(row["closed_requests"]) if has else 0,
            "data_through": as_of,
            "fill": color(idx if has else None), "fill-opacity": 0.75, "stroke": "#ffffff", "stroke-width": 1,
        }  # fmt: skip
        feats.append({"type": "Feature", "properties": props, "geometry": simplify(json.loads(r.geometry))})
    return {"type": "FeatureCollection", "features": feats}


def _area(ring) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False)) / 2


def rewind_for_d3(geom: dict) -> dict:
    """Plotly's geo maps (d3-geo) need outer rings clockwise and holes counter-clockwise, the reverse of the
    GeoJSON standard; with the wrong order a polygon is read as "everything except this area" and disappears."""

    def poly(rings):
        out = []
        for k, r in enumerate(rings):
            clockwise = _area(r) < 0
            want_clockwise = k == 0
            out.append(r if clockwise == want_clockwise else r[::-1])
        return out

    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": poly(geom["coordinates"])}
    return {"type": "MultiPolygon", "coordinates": [poly(p) for p in geom["coordinates"]]}


def fig_map(fc: dict):
    """Choropleth of the service index drawn from the polygons alone (no basemap service)."""
    import plotly.graph_objects as go

    from .report import SURFACE, _style

    f = [x for x in fc["features"] if x["properties"]["service_index"] is not None]
    if not f:
        return None
    d3 = {"type": "FeatureCollection",
          "features": [{**x, "geometry": rewind_for_d3(x["geometry"])} for x in fc["features"]]}  # fmt: skip
    fig = go.Figure(go.Choropleth(
        geojson=d3, featureidkey="properties.community_area_id",
        locations=[x["properties"]["community_area_id"] for x in f],
        z=[x["properties"]["service_index"] for x in f],
        text=[x["properties"]["community_area"] for x in f],
        customdata=[[x["properties"]["median_days_to_close"], x["properties"]["closed_requests"]] for x in f],
        zmin=0.6, zmax=1.4, colorscale=[[0, "#2a78d6"], [0.5, "#f0efea"], [1, "#eb6834"]],
        marker=dict(line=dict(color="#ffffff", width=0.8)),
        colorbar=dict(title="index", thickness=12, len=0.7, tickvals=[0.6, 0.8, 1.0, 1.2, 1.4]),
        hovertemplate="<b>%{text}</b><br>service index %{z:.2f}<br>median %{customdata[0]:.1f} days"
                      "<br>%{customdata[1]:,} closed requests<extra></extra>",
    ))  # fmt: skip
    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator", bgcolor=SURFACE)
    _style(fig, "Service index by community area (blue = faster, orange = slower than typical)", height=620,
           hovermode="closest", margin=dict(l=0, r=0, t=50, b=0))  # fmt: skip
    return fig


def write(cfg: Config, fc: dict) -> None:
    cfg.map_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.map_path.write_text(json.dumps(fc, separators=(",", ":")))
