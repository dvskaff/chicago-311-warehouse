"""Project settings. Paths can be overridden with environment variables (used by tests and CI)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _path(env: str, default: Path) -> Path:
    return Path(os.environ[env]).resolve() if os.environ.get(env) else default


@dataclass
class Config:
    # Chicago Data Portal (Socrata). No account or API key is needed; an optional app token raises rate limits.
    base_url: str = "https://data.cityofchicago.org"
    dataset_id: str = "v6vf-nfxy"  # 311 Service Requests
    app_token: str | None = field(default_factory=lambda: os.environ.get("SOCRATA_APP_TOKEN") or None)

    # History kept in the warehouse, and how the daily incremental load works.
    backfill_start: str = "2023-01-01"
    page_size: int = 50_000
    lookback_hours: int = 48  # re-read recently modified rows; staging de-duplicates them
    # Information-only calls (e.g. "what are the library hours?") are not service work, so they are not loaded.
    excluded_types: tuple[str, ...] = ("311 INFORMATION ONLY CALL",)

    db_path: Path = field(
        default_factory=lambda: _path("C311_DB_PATH", ROOT / "warehouse" / "chicago311.duckdb")
    )
    dbt_dir: Path = field(default_factory=lambda: ROOT / "dbt")
    site_dir: Path = field(default_factory=lambda: _path("C311_SITE_DIR", ROOT / "site"))
    readme_path: Path = field(default_factory=lambda: ROOT / "README.md")

    repo_url: str = "https://github.com/dvskaff/chicago-311-warehouse"
    site_url: str = "https://dvskaff.github.io/chicago-311-warehouse/"
