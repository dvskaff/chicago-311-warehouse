"""End to end on synthetic data: loader -> dbt build (models + all tests) -> freshness -> docs -> website."""

import json
import shutil
from dataclasses import replace

import pytest

from c311.config import Config
from c311.pipeline import run
from c311.readme import update_readme

pytestmark = pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")


def test_full_pipeline_on_fixture(tmp_path):
    cfg = replace(Config(), db_path=tmp_path / "w.duckdb", site_dir=tmp_path / "site")
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

    readme = tmp_path / "README.md"
    readme.write_text("# T\n<!-- RESULTS:START -->\nold\n<!-- RESULTS:END -->\nend\n")
    update_readme(readme, summary)
    text = readme.read_text()
    assert "Median time to close" in text and "\nold\n" not in text and text.endswith("end\n")
