from dataclasses import replace
from datetime import datetime

import duckdb
import pytest

from c311 import extract, fixture
from c311.config import Config


@pytest.fixture
def cfg(tmp_path):
    return replace(Config(), db_path=tmp_path / "w.duckdb", page_size=500)


def test_map_columns_accepts_the_published_schema():
    mapping = extract.map_columns(list(extract.COLUMNS))
    assert mapping["sr_number"] == "sr_number" and mapping["closed_date"] == "closed_date"


def test_missing_required_column_fails_loudly():
    cols = [c for c in extract.COLUMNS if c != "status"]
    with pytest.raises(extract.SchemaError, match="status"):
        extract.map_columns(cols)


def test_missing_optional_column_is_loaded_as_null(cfg):
    client = fixture.FakeSocrata(fixture.make_requests(300), drop_columns=("ward",))
    extract.run(cfg, client=client, now=datetime(2026, 1, 1))
    con = duckdb.connect(str(cfg.db_path))
    assert con.execute("select count(ward), count(*) from raw.service_requests").fetchall()[0][0] == 0


def test_soql_literals_are_escaped():
    assert extract.soql_literal("O'Hare") == "'O''Hare'"
    assert extract.soql_ts(datetime(2024, 5, 1, 13, 5)) == "'2024-05-01T13:05:00.000'"


def test_month_windows_cover_the_range_without_gaps():
    w = extract.month_windows(datetime(2023, 11, 15), datetime(2024, 2, 10))
    assert w[0] == (datetime(2023, 11, 15), datetime(2023, 12, 1))
    assert w[-1] == (datetime(2024, 2, 1), datetime(2024, 2, 10))
    assert all(a[1] == b[0] for a, b in zip(w, w[1:], strict=False))


def test_filters_exclude_information_only_calls_and_old_records(cfg):
    f = extract.base_filter(extract.map_columns(list(extract.COLUMNS)), cfg)
    assert "created_date >= '2023-01-01T00:00:00.000'" in f
    assert "sr_type != '311 INFORMATION ONLY CALL'" in f


def test_backfill_then_incremental(cfg):
    data = fixture.make_requests(1500)
    client = fixture.FakeSocrata(data)
    first = extract.run(cfg, client=client, now=datetime(2026, 1, 1))
    assert first.mode == "backfill" and first.rows == len(data)
    # Paging: no call asked for more than the page size, and windows are month by month.
    assert all(int(c["$limit"]) == 500 for c in client.calls)

    second = extract.run(cfg, client=client, now=datetime(2026, 1, 2))
    assert second.mode == "incremental"
    assert "last_modified_date >" in client.calls[-1]["$where"]
    # Only rows modified within the lookback window are re-read.
    assert 0 < second.rows < len(data) * 0.05

    con = duckdb.connect(str(cfg.db_path))
    log = con.execute("select mode, rows_loaded from raw.load_log order by started_at").fetchall()
    assert [m for m, _ in log] == ["backfill", "incremental"]
    assert (
        con.execute("select count(distinct sr_number) from raw.service_requests").fetchall()[0][0]
        == data["sr_number"].nunique()
    )
