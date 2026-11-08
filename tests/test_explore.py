"""The browser SQL page: exported tables, their metadata and the page itself."""

import gzip
import json

import duckdb

from c311 import explore


def _warehouse(path):
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute("create table marts.t as select range as id, range * 10::hugeint as total, "
                "date '2026-01-01' + range::int as day from range(5)")  # fmt: skip
    con.close()


def test_export_writes_gzipped_csv_with_metadata(tmp_path, monkeypatch):
    db = tmp_path / "w.duckdb"
    _warehouse(db)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"nodes": {"model.c311.t": {
        "resource_type": "model", "name": "t", "description": "A table.\n\nMore detail.",
        "columns": {"id": {"description": "Row id"}}}}}))  # fmt: skip
    monkeypatch.setattr(
        explore,
        "TABLES",
        [("t", "t", None, None), ("first_two", None, "select * from marts.t where id < 2", "Two rows")],
    )
    meta = explore.export(db, tmp_path / "data", manifest)
    t, two = meta
    assert t["rows"] == 5 and t["description"] == "A table." and t["file"] == "data/t.csv.gz"
    assert {c["name"]: c["type"] for c in t["columns"]}["total"] == "BIGINT"  # HUGEINT cast down
    assert t["columns"][0]["description"] == "Row id"
    assert two["rows"] == 2 and two["description"] == "Two rows"
    with gzip.open(tmp_path / "data" / "t.csv.gz", "rt") as f:
        lines = f.read().splitlines()
    assert lines[0] == "id,total,day" and lines[1] == "0,0,2026-01-01" and len(lines) == 6


def test_examples_run_against_their_tables():
    names = {t[0] for t in explore.TABLES}
    assert "service_requests" in names and len(explore.EXAMPLES) >= 4
    for ex in explore.EXAMPLES:
        sql = ex[-1] if isinstance(ex, (tuple, list)) else ex["sql"]
        assert any(n in sql for n in names)


def test_runtime_copy_and_page(tmp_path):
    vendor, site = tmp_path / "vendor", tmp_path / "site"
    assert explore.copy_runtime(vendor, site) is False  # missing runtime: page still builds, with a notice
    vendor.mkdir()
    for f in explore.RUNTIME:
        (vendor / f).write_text("x")
    assert explore.copy_runtime(vendor, site) is True
    assert all((site / "vendor" / f).exists() for f in explore.RUNTIME)
    meta = [{"name": "service_requests", "file": "data/service_requests.csv.gz", "rows": 3, "bytes": 100,
             "description": "Requests", "source": "x", "columns": [{"name": "created_date", "type": "DATE",
                                                                   "description": "Filed"}]}]  # fmt: skip
    explore.write_page(site, meta, "2026-11-01", True, "https://github.com/x/y", synthetic=False)
    page = (site / "sql.html").read_text()
    assert "service_requests" in page and "vendor/duckdb.bundle.mjs" in page and "2026-11-01" in page
    assert json.loads((site / "data" / "tables.json").read_text())["tables"][0]["rows"] == 3
