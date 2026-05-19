#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import finder  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed coverage supplement cache from existing search cache files.")
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--holiday-code", default="2026-06-19::端午节")
    parser.add_argument("--advanced-filter", default="all", choices=["all", "yes", "no"])
    parser.add_argument("--pool-filter", default="all", choices=["all", "yes", "no"])
    parser.add_argument("--child-facility-filter", default="all", choices=["all", "yes", "no"])
    parser.add_argument("--partial", action="store_true", help="store as partial cache so live coverage still refreshes")
    return parser.parse_args()


def same_city(left: str, right: str) -> bool:
    return finder._to_simplified_chinese(str(left or "")).strip().lower() == finder._to_simplified_chinese(
        str(right or "")
    ).strip().lower()


def search_cache_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((ROOT / ".cache" / "search").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result = data.get("result")
        if isinstance(result, dict):
            records.append({"path": str(path), "record": data, "result": result})
    return records


def main() -> None:
    args = parse_args()
    city_candidate = finder._resolve_city(args.city)
    feature_filters = finder._normalize_feature_filters(
        args.advanced_filter,
        args.pool_filter,
        args.child_facility_filter,
    )
    cache_key = finder._coverage_cache_key(city_candidate.city_name, args.holiday_code, feature_filters)

    merged_choices: list[dict[str, Any]] = []
    source_files: list[str] = []
    for item in search_cache_records():
        result = item["result"]
        holiday = result.get("holiday") or {}
        if holiday.get("code") != args.holiday_code:
            continue
        if not same_city(result.get("city"), city_candidate.city_name):
            continue
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        filtered = finder._filter_choices_by_verified_features(choices, feature_filters)
        if not filtered:
            continue
        merged_choices = finder._merge_choice_lists(merged_choices, filtered)
        source_files.append(item["path"])

    finder._apply_cached_hotel_names_to_choices(merged_choices)
    finder._refresh_choice_area_names(merged_choices, city_candidate.city_name)
    merged_choices.sort(key=finder._choice_sort_key)
    finder._store_coverage_cache(cache_key, merged_choices, time.time(), complete=not args.partial)

    print(
        json.dumps(
            {
                "city": city_candidate.city_name,
                "holiday_code": args.holiday_code,
                "feature_filters": feature_filters.cache_parts(),
                "complete": not args.partial,
                "choice_count": len(merged_choices),
                "source_file_count": len(source_files),
                "cache_key": cache_key,
                "cache_file": str(finder._coverage_cache_path(cache_key)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
