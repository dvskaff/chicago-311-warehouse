"""Rewrites the results block in README.md after each daily run."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


def results_block(s: dict) -> str:
    as_of = pd.Timestamp(s["data_as_of"])
    start = pd.Timestamp(s["window_start"]) + pd.Timedelta(days=1)
    end = pd.Timestamp(s["window_end"])
    vol = s["requests"] / s["requests_prior"] - 1 if s["requests_prior"] else 0
    d7 = (s["pct_closed_within_7d"] - s["pct_closed_within_7d_prior"]) * 100
    dbt = s["dbt"]
    lines = [
        f"_Data through {as_of:%b %d, %Y} · requests created {start:%b %d, %Y} – {end:%b %d, %Y} · "
        f"{s['requests_in_warehouse']:,} requests in the warehouse_",
        "",
        "| Metric | Last 12 months | vs. prior 12 months |",
        "|---|---|---|",
        f"| Service requests | {s['requests']:,} | {vol:+.1%} |",
        f"| Median time to close | {s['median_days_to_close']:.1f} days | |",
        f"| Closed within 7 days | {s['pct_closed_within_7d']:.1%} | {d7:+.1f} pts |",
        f"| Open right now | {s['open_requests']:,} ({s['open_over_30_days']:,} older than 30 days) | |",
        "",
        "| Department | Requests | Median days to close | Closed within 7 days | Open now |",
        "|---|---|---|---|---|",
    ]
    for d in s["departments"]:
        lines.append(
            f"| {d['department_name']} | {d['requests']:,} | {d['median_days_to_close']:.1f} | "
            f"{d['pct_closed_within_7d']:.0%} | {d['open_requests']:,} |"
        )
    if s.get("slowest_area"):
        lines += [
            "",
            f"**Neighborhood equity:** across {s['community_areas_indexed']} community areas, the slowest is "
            f"{s['slowest_area']['community_area_name']}, where requests take "
            f"{s['slowest_area']['service_index']:.2f}× the typical time to close for the same request types; the "
            f"fastest is {s['fastest_area']['community_area_name']} at {s['fastest_area']['service_index']:.2f}×.",
        ]
    lines += [
        "",
        f"**Latest run:** {dbt['models']} dbt models, {dbt['data_tests']} data tests and "
        f"{dbt['unit_tests']} unit tests: {dbt['passed']} passed, {dbt['warnings']} warning{'' if dbt['warnings'] == 1 else 's'}, "
        f"{dbt['failed']} failed · source freshness: {s['freshness'] or 'n/a'} · {s['built_at']}",
    ]
    return "\n".join(lines)


def update_readme(readme: Path, summary: dict) -> None:
    text = readme.read_text()
    pattern = r"(<!-- RESULTS:START -->\n).*?(\n<!-- RESULTS:END -->)"
    block = results_block(summary)
    readme.write_text(re.sub(pattern, lambda m: m.group(1) + block + m.group(2), text, flags=re.S))
