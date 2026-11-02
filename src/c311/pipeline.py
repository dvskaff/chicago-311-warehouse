"""Daily pipeline: extract/load -> dbt build (seed, snapshot, models, tests) -> freshness -> docs -> website.

python -m c311.pipeline                  # real data from the Chicago Data Portal
python -m c311.pipeline --full-refresh   # rebuild the incremental fact table from scratch
python -m c311.pipeline --fixture        # offline run on synthetic test data (CI and tests only)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import extract, fixture
from .config import Config
from .readme import update_readme
from .report import build_site

log = logging.getLogger("c311")


class DbtFailed(RuntimeError):
    pass


def dbt(cfg: Config, args: list[str], target_dir: Path, fatal: bool = True) -> bool:
    """Run a dbt command against the project in dbt/ (in a subprocess, so DuckDB is released afterwards)."""
    exe = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt") or "dbt"
    env = {**os.environ, "C311_DB_PATH": str(cfg.db_path), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    full = [
        exe,
        *args,
        "--project-dir",
        str(cfg.dbt_dir),
        "--profiles-dir",
        str(cfg.dbt_dir),
        "--target-path",
        str(target_dir),
        "--log-path",
        str(target_dir / "logs"),
    ]
    log.info("dbt %s", " ".join(args))
    ok = subprocess.run(full, env=env, check=False).returncode == 0
    if fatal and not ok:
        raise DbtFailed(f"`dbt {' '.join(args)}` failed; see the log above for the failing model or test.")
    return ok


def run(
    cfg: Config, *, use_fixture: bool = False, full_refresh: bool = False, dbt_vars: dict | None = None
) -> dict:
    t0 = time.time()
    target = cfg.dbt_dir / "target" if not use_fixture else cfg.site_dir.parent / "dbt-target"
    target.mkdir(parents=True, exist_ok=True)

    # 1. Extract & load into the raw layer.
    client = fixture.FakeSocrata(fixture.make_requests()) if use_fixture else None
    now = datetime(2026, 1, 1) if use_fixture else None
    load = extract.run(cfg, client=client, now=now)
    log.info("Loaded %s rows (%s)", f"{load.rows:,}", load.mode)

    # 2. Transform and test everything in dependency order.
    vars_arg = ["--vars", json.dumps(dbt_vars)] if dbt_vars else []
    dbt(cfg, ["build", *vars_arg, *(["--full-refresh"] if full_refresh else [])], target)
    shutil.copy(target / "run_results.json", target / "build_run_results.json")

    # 3. Source freshness (reported on the dashboard; a stale city feed should not block publishing).
    fresh_ok = dbt(cfg, ["source", "freshness"], target, fatal=False)
    log.info("Source freshness check %s", "passed" if fresh_ok else "flagged a stale source")

    # 4. Documentation site with lineage graph.
    dbt(cfg, ["docs", "generate", "--static", *vars_arg], target)

    # 5. Website + README.
    summary = build_site(cfg, target, load_mode=load.mode, rows_loaded=load.rows, synthetic=use_fixture)
    if not use_fixture:
        update_readme(cfg.readme_path, summary)
    log.info("Done in %.0fs. Site: %s", time.time() - t0, cfg.site_dir / "index.html")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fixture", action="store_true", help="offline run on synthetic test data")
    parser.add_argument("--full-refresh", action="store_true", help="rebuild incremental models from scratch")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config()
    dbt_vars = None
    if args.fixture:
        tmp = Path(os.environ.get("C311_FIXTURE_DIR", "/tmp/c311-fixture")).resolve()
        if tmp.exists():
            shutil.rmtree(tmp)
        cfg = replace(cfg, db_path=tmp / "warehouse.duckdb", site_dir=tmp / "site")
        dbt_vars = {"min_area_requests": 5}
    run(cfg, use_fixture=args.fixture, full_refresh=args.full_refresh, dbt_vars=dbt_vars)


if __name__ == "__main__":
    main()
