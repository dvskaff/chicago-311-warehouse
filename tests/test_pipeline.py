"""End to end on synthetic data: loader -> dbt build (models + all tests) -> freshness -> docs -> website."""

import json
import shutil
from dataclasses import replace

import duckdb
import pytest

from c311 import explore
from c311.config import Config
from c311.pipeline import run
from c311.readme import update_readme

pytestmark = pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")


def test_full_pipeline_on_fixture(tmp_path):
    cfg = replace(
        Config(), db_path=tmp_path / "w.duckdb", site_dir=tmp_path / "site", map_path=tmp_path / "map.geojson"
    )
    summary = run(cfg, use_fixture=True, dbt_vars={"min_area_requests": 5})

    site = tmp_path / "site"
    page = (site / "index.html").read_text()
    assert "SYNTHETIC TEST DATA" in page  # fixture builds are clearly labeled
    assert (site / "dbt-docs" / "index.html").exists()
    assert json.loads((site / "results.json").read_text())["synthetic"] is True

    assert summary["dbt"]["failed"] == 0
    assert summary["dbt"]["models"] >= 14 and summary["dbt"]["data_tests"] >= 60
    assert summary["requests"] > 0 and 0 < summary["pct_closed_within_7d"] < 1
    assert summary["community_areas_indexed"] > 10

    # neighborhood map (synthetic squares here) and the browser SQL page
    fc = json.loads((tmp_path / "map.geojson").read_text())
    assert len(fc["features"]) == 77 and "Service index by community area" in page
    tables = json.loads((site / "data" / "tables.json").read_text())["tables"]
    assert (site / "sql.html").exists() and len(tables) == len(explore.TABLES)
    # every example query on the SQL page runs on the exported files (DuckDB here, DuckDB-WASM in the browser)
    con = duckdb.connect()
    for t in tables:
        con.execute(
            f"create table {t['name']} as select * from read_csv('{site / t['file']}', header = true)"
        )
        assert con.execute(f"select count(*) from {t['name']}").fetchall()[0][0] == t["rows"]
    for _, sql in explore.EXAMPLES:
        con.execute(sql).fetchall()

    readme = tmp_path / "README.md"
    readme.write_text("# T\n<!-- RESULTS:START -->\nold\n<!-- RESULTS:END -->\nend\n")
    update_readme(readme, summary)
    text = readme.read_text()
    assert "Median time to close" in text and "\nold\n" not in text and text.endswith("end\n")
