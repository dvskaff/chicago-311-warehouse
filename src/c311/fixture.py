"""SYNTHETIC test data shaped like the Socrata 311 API, used only by the test suite and CI.

Nothing generated here is ever published: the real pipeline always loads data from the Chicago Data Portal.
The fixture exists so that the loader, every dbt model and test, and the website can be exercised end to end
without network access, including the awkward cases real data contains (updated versions of the same request,
canceled requests, duplicates, closures recorded before creation, missing coordinates).
"""

from __future__ import annotations

import re
from datetime import datetime

import numpy as np
import pandas as pd

from .extract import COLUMNS

TYPES = {  # request type -> (owner department, short code, typical days to close)
    "Pothole in Street Complaint": ("CDOT - Department of Transportation", "PHF", 6.0),
    "Street Light Out Complaint": ("CDOT - Department of Transportation", "SLA", 9.0),
    "Graffiti Removal Request": ("Streets and Sanitation", "GRAF", 2.0),
    "Garbage Cart Maintenance": ("Streets and Sanitation", "SGQ", 8.0),
    "Rodent Baiting/Rat Complaint": ("Streets and Sanitation", "SGA", 5.0),
    "Water On Street Complaint": ("Water Management", "WBJ", 3.0),
    "Sewer Cleaning Inspection Request": ("Water Management", "WBK", 12.0),
    "Stray Animal Complaint": ("Animal Care and Control", "EAF", 1.5),
    "Building Violation": ("Buildings", "BBA", 30.0),
    "Aircraft Noise Complaint": ("Aviation", "AVN", 0.005),  # logged and closed on receipt
}


def socrata_ts(ts: pd.Timestamp | datetime | None) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000")


def make_requests(
    n: int = 4000, start: str = "2023-01-01", end: str = "2025-12-31", seed: int = 7
) -> pd.DataFrame:
    """One row per request version, API field names, all values as strings (like the CSV endpoint)."""
    rng = np.random.default_rng(seed)
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    names = list(TYPES)
    probs = np.array([18, 10, 14, 8, 12, 9, 6, 7, 4, 12], float)
    probs /= probs.sum()
    rows = []
    for i in range(n):
        sr_type = rng.choice(names, p=probs)
        dept, code, typical = TYPES[sr_type]
        area = int(rng.integers(1, 78))
        created = t0 + (t1 - t0) * rng.random()
        created = created.floor("s")
        days = rng.exponential(typical) * (1.4 if area > 60 else 1.0)
        closed = created + pd.Timedelta(days=days)
        roll = rng.random()
        status = "Completed" if closed < t1 and roll < 0.9 else ("Canceled" if roll < 0.95 else "Open")
        closed_at = (
            closed
            if status == "Completed"
            else (created + pd.Timedelta(hours=5) if status == "Canceled" else None)
        )
        if status == "Completed" and rng.random() < 0.003:  # data-entry error: closure before creation
            closed_at = created - pd.Timedelta(hours=2)
        modified = (
            closed_at if closed_at is not None and closed_at > created else created + pd.Timedelta(hours=1)
        )
        lat, lon = 41.65 + 0.37 * rng.random(), -87.85 + 0.33 * rng.random()
        has_geo = rng.random() > 0.02
        row = {
            "sr_number": f"SR{23000000 + i:08d}",
            "sr_type": sr_type,
            "sr_short_code": code,
            "owner_department": dept,
            "created_department": dept,
            "status": status,
            "origin": rng.choice(["Phone Call", "Mobile Device", "Internet", "spot-311-app"]),
            "created_date": socrata_ts(created),
            "last_modified_date": socrata_ts(modified),
            "closed_date": socrata_ts(closed_at),
            "duplicate": "true" if rng.random() < 0.04 else "false",
            "legacy_record": "false",
            "parent_sr_number": None,
            "community_area": str(area) if has_geo else None,
            "ward": str(int(rng.integers(1, 51))) if has_geo else None,
            "zip_code": str(int(rng.integers(60601, 60661))),
            "latitude": f"{lat:.6f}" if has_geo else None,
            "longitude": f"{lon:.6f}" if has_geo else None,
        }
        # Some requests were first seen while still open, then updated later: two versions in the log.
        if status != "Open" and rng.random() < 0.15:
            rows.append(
                {
                    **row,
                    "status": "Open",
                    "closed_date": None,
                    "last_modified_date": socrata_ts(created + pd.Timedelta(minutes=30)),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


class FakeSocrata:
    """Serves fixture rows through the same interface as extract.Socrata, honouring the SoQL filters used."""

    def __init__(self, frame: pd.DataFrame, drop_columns: tuple[str, ...] = ()):
        self.frame = frame.drop(columns=list(drop_columns))
        self.calls: list[dict] = []

    def columns(self):
        return list(self.frame.columns), datetime(2026, 1, 1)

    def query(self, params) -> pd.DataFrame:
        self.calls.append(dict(params))
        f, where = self.frame, params["$where"]
        created = pd.to_datetime(f["created_date"])
        modified = pd.to_datetime(f["last_modified_date"])
        mask = pd.Series(True, index=f.index)
        for op, value in re.findall(r"created_date (>=|<) '([^']+)'", where):
            mask &= created >= pd.Timestamp(value) if op == ">=" else created < pd.Timestamp(value)
        for value in re.findall(r"last_modified_date > '([^']+)'", where):
            mask &= modified > pd.Timestamp(value)
        for value in re.findall(r"sr_type != '([^']+)'", where):
            mask &= f["sr_type"] != value
        out = f[mask]
        cols = [c.strip() for c in params["$select"].split(",")]
        off, lim = int(params["$offset"]), int(params["$limit"])
        return out[cols].iloc[off : off + lim].reset_index(drop=True)


def boundaries() -> dict:
    """SYNTHETIC community-area polygons: a grid of squares over Chicago, one per area id 1..77 (tests only)."""
    feats = []
    for i in range(77):
        row, col = divmod(i, 11)
        x0, y0 = -87.94 + col * 0.03, 41.64 + row * 0.05
        ring = [[x0, y0], [x0 + 0.03, y0], [x0 + 0.03, y0 + 0.05], [x0, y0 + 0.05], [x0, y0]]
        feats.append({"type": "Feature", "properties": {"area_numbe": str(i + 1), "community": f"AREA {i + 1}"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})  # fmt: skip
    return {"type": "FeatureCollection", "features": feats}
