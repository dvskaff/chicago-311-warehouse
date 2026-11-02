"""Extract and load: Chicago 311 service requests from the Socrata API into DuckDB (raw layer).

ELT design:
* raw.service_requests is an append-only log. Every row is stored exactly as the API returned it (all text),
  plus _batch_id and _loaded_at. When a request is updated (e.g. Open -> Completed) a new version is appended;
  dbt staging keeps the latest version per sr_number.
* The first run backfills history month by month. Later runs are incremental: they fetch only rows whose
  last_modified_date is after the stored watermark (minus a lookback window, so late-arriving updates are not
  missed; duplicates from the overlap are removed downstream).
* Column names are read from the dataset's metadata on every run and mapped to the names the warehouse uses,
  so a renamed or removed column fails loudly with a clear message instead of silently loading nulls.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config

log = logging.getLogger(__name__)

# warehouse column -> (required?, candidate API field names)
SCHEMA: dict[str, tuple[bool, tuple[str, ...]]] = {
    "sr_number": (True, ("sr_number",)),
    "sr_type": (True, ("sr_type",)),
    "sr_short_code": (False, ("sr_short_code",)),
    "owner_department": (True, ("owner_department",)),
    "created_department": (False, ("created_department",)),
    "status": (True, ("status",)),
    "origin": (False, ("origin",)),
    "created_date": (True, ("created_date",)),
    "last_modified_date": (True, ("last_modified_date",)),
    "closed_date": (True, ("closed_date",)),
    "duplicate": (False, ("duplicate",)),
    "legacy_record": (False, ("legacy_record",)),
    "parent_sr_number": (False, ("parent_sr_number",)),
    "community_area": (False, ("community_area",)),
    "ward": (False, ("ward",)),
    "zip_code": (False, ("zip_code",)),
    "latitude": (False, ("latitude",)),
    "longitude": (False, ("longitude",)),
}
COLUMNS = list(SCHEMA)


class SchemaError(RuntimeError):
    pass


def map_columns(available: list[str]) -> dict[str, str | None]:
    """Warehouse column -> API field (None if an optional column is missing)."""
    mapping, missing = {}, []
    for col, (required, candidates) in SCHEMA.items():
        found = next((c for c in candidates if c in available), None)
        if found is None and required:
            missing.append(col)
        mapping[col] = found
    if missing:
        raise SchemaError(
            f"The 311 dataset no longer has required column(s) {missing}. "
            f"Columns available now: {sorted(available)}. Update SCHEMA in src/c311/extract.py."
        )
    return mapping


# --------------------------------------------------------------------------- SoQL query building
def soql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def soql_ts(ts: datetime) -> str:
    """Socrata floating timestamp literal (no time zone), e.g. '2024-05-01T00:00:00.000'."""
    return soql_literal(ts.strftime("%Y-%m-%dT%H:%M:%S.000"))


def base_filter(mapping: dict[str, str | None], cfg: Config) -> str:
    parts = [f"{mapping['created_date']} >= {soql_ts(datetime.fromisoformat(cfg.backfill_start))}"]
    parts += [f"{mapping['sr_type']} != {soql_literal(t)}" for t in cfg.excluded_types]
    return " AND ".join(parts)


def month_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """[start, end) split into calendar months."""
    windows, cur = [], datetime(start.year, start.month, 1)
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        windows.append((max(cur, start), min(nxt, end)))
        cur = nxt
    return windows


def select_clause(mapping: dict[str, str | None]) -> str:
    return ", ".join(src for src in mapping.values() if src)


# --------------------------------------------------------------------------- HTTP client
class Socrata:
    """Minimal Socrata client: dataset metadata and paged CSV queries, with retries."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        retry = Retry(
            total=6, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=["GET"]
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers["User-Agent"] = (
            "chicago-311-warehouse (github.com/dvskaff/chicago-311-warehouse)"
        )
        if cfg.app_token:
            self.session.headers["X-App-Token"] = cfg.app_token

    def columns(self) -> tuple[list[str], datetime | None]:
        r = self.session.get(f"{self.cfg.base_url}/api/views/{self.cfg.dataset_id}.json", timeout=60)
        r.raise_for_status()
        meta = r.json()
        updated = meta.get("rowsUpdatedAt")
        fields = [c["fieldName"] for c in meta.get("columns", []) if not c["fieldName"].startswith(":")]
        return fields, (datetime.fromtimestamp(updated, UTC) if updated else None)

    def query(self, params: dict[str, str | int]) -> pd.DataFrame:
        r = self.session.get(
            f"{self.cfg.base_url}/resource/{self.cfg.dataset_id}.csv", params=params, timeout=300
        )
        r.raise_for_status()
        if not r.text.strip():
            return pd.DataFrame()
        return pd.read_csv(io.StringIO(r.text), dtype=str, keep_default_na=False, na_values=[""])


def fetch_pages(client, mapping, where: str, order: str, page_size: int):
    """Yield DataFrames page by page until a short page is returned."""
    offset = 0
    while True:
        params = {
            "$select": select_clause(mapping),
            "$where": where,
            "$order": order,
            "$limit": page_size,
            "$offset": offset,
        }
        page = client.query(params)
        if len(page):
            yield page
        if len(page) < page_size:
            return
        offset += page_size


# --------------------------------------------------------------------------- DuckDB raw layer
RAW_DDL = f"""
CREATE SCHEMA IF NOT EXISTS raw;
CREATE TABLE IF NOT EXISTS raw.service_requests (
    {", ".join(f"{c} VARCHAR" for c in COLUMNS)},
    _batch_id VARCHAR NOT NULL,
    _loaded_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS raw.load_log (
    batch_id VARCHAR PRIMARY KEY,
    mode VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    rows_loaded BIGINT,
    watermark_from VARCHAR,
    source_updated_at TIMESTAMP
);
"""


def connect(cfg: Config) -> duckdb.DuckDBPyConnection:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.db_path))
    for statement in filter(str.strip, RAW_DDL.split(";")):
        con.execute(statement).fetchall()
    return con


def to_raw_frame(page: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            col: (page[src] if src and src in page else pd.Series([None] * len(page), dtype=object))
            for col, src in mapping.items()
        }
    )
    return out.astype(object).where(out.notna(), None)


def append(con, frame: pd.DataFrame, batch_id: str, loaded_at: datetime) -> int:
    if frame.empty:
        return 0
    con.register("batch_frame", frame)
    con.execute(
        f"INSERT INTO raw.service_requests SELECT {', '.join(COLUMNS)}, ?, ? FROM batch_frame",
        [batch_id, loaded_at.replace(tzinfo=None)],
    ).fetchall()  # DuckDB 2 requires results to be consumed before the next statement
    con.unregister("batch_frame")
    return len(frame)


def watermark(con) -> datetime | None:
    value = con.execute(
        "SELECT max(try_cast(last_modified_date AS TIMESTAMP)) FROM raw.service_requests"
    ).fetchall()[0][0]
    return value


@dataclass
class LoadResult:
    mode: str
    rows: int
    watermark_from: datetime | None
    source_updated_at: datetime | None


def run(cfg: Config, client=None, now: datetime | None = None, mode: str = "auto") -> LoadResult:
    """Load new and changed 311 requests into raw.service_requests."""
    client = client or Socrata(cfg)
    now = now or datetime.now(UTC).replace(tzinfo=None)
    con = connect(cfg)
    try:
        fields, source_updated = client.columns()
        mapping = map_columns(fields)
        missing_optional = [c for c, s in mapping.items() if s is None]
        if missing_optional:
            log.warning("Optional columns not in the source (loaded as NULL): %s", missing_optional)

        wm = watermark(con)
        mode = ("backfill" if wm is None else "incremental") if mode == "auto" else mode
        batch_id, started = uuid.uuid4().hex[:12], datetime.now(UTC)
        rows = 0
        if mode == "backfill":
            for lo, hi in month_windows(datetime.fromisoformat(cfg.backfill_start), now + timedelta(days=1)):
                where = (
                    f"{base_filter(mapping, cfg)} AND {mapping['created_date']} >= {soql_ts(lo)} "
                    f"AND {mapping['created_date']} < {soql_ts(hi)}"
                )
                for page in fetch_pages(client, mapping, where, ":id", cfg.page_size):
                    rows += append(con, to_raw_frame(page, mapping), batch_id, started)
                log.info("Backfill %s: %s rows so far", lo.strftime("%Y-%m"), f"{rows:,}")
            wm_from = None
        else:
            wm_from = wm - timedelta(hours=cfg.lookback_hours)
            where = f"{base_filter(mapping, cfg)} AND {mapping['last_modified_date']} > {soql_ts(wm_from)}"
            for page in fetch_pages(
                client, mapping, where, f"{mapping['last_modified_date']}, :id", cfg.page_size
            ):
                rows += append(con, to_raw_frame(page, mapping), batch_id, started)
            log.info("Incremental load since %s: %s rows", wm_from, f"{rows:,}")

        con.execute(
            "INSERT INTO raw.load_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                batch_id,
                mode,
                started.replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None),
                rows,
                wm_from.isoformat() if wm_from else None,
                source_updated.replace(tzinfo=None) if source_updated else None,
            ],
        ).fetchall()
        return LoadResult(mode, rows, wm_from, source_updated)
    finally:
        con.close()
