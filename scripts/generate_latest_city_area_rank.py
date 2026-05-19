#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from generate_xhs_area_region_rank import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    build_area_rows,
    collect_cache_hotels,
    diff_text,
    price_text,
)
from holiday_helper import HolidayCalendar  # noqa: E402
from reverse_travel import ReverseTravelFinder  # noqa: E402

EXPORT_DIRS = {
    "广州": ROOT / "exports/xhs_guangzhou_duanwu_2026",
    "佛山": ROOT / "exports/xhs_foshan_duanwu_2026",
    "深圳": ROOT / "exports/xhs_shenzhen_duanwu_2026",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate city hotel rank and area rank from latest local search data.")
    parser.add_argument("--city", action="append", required=True)
    parser.add_argument("--holiday-contains", default="2026-06-19")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-rise", type=int, default=0)
    parser.add_argument("--min-city-rank-count", type=int, default=8)
    parser.add_argument("--no-rerun-under-count", action="store_true")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def city_rank_rows(hotels: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(hotels, key=lambda item: (item["price"], item["diff"], item["name"]))[:limit]


def discount_rank_rows(hotels: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(
        [item for item in hotels if item["diff"] < 0],
        key=lambda item: (item["diff"], item["price"], item["name"]),
    )[:limit]


def hotel_line(index: int, item: dict[str, Any]) -> str:
    return (
        f"{index}. {item['name']}｜{item['area']}｜"
        f"端午每晚含税均价{price_text(item['price'])}｜{diff_text(item['diff'])}"
    )


def area_line(item: dict[str, Any]) -> str:
    hotels = "、".join(item["representative_hotels"])
    return (
        f"{item['rank']}. {item['area']}｜{item['hotel_count']}家高级酒店｜"
        f"端午每晚含税均价{price_text(item['average_price'])}｜"
        f"{diff_text(item['average_diff'], average=True)}\n"
        f"   代表酒店：{hotels}"
    )


def build_city_payload(city: str, cache_dir: Path, holiday_contains: str, limit: int, max_rise: int) -> dict[str, Any]:
    hotels, meta = collect_cache_hotels(
        city=city,
        cache_dir=cache_dir,
        holiday_contains=holiday_contains,
        advanced_only=True,
        max_rise=max_rise,
    )
    area_rows = build_area_rows(hotels, limit)
    city_rows = city_rank_rows(hotels, limit)
    discount_rows = discount_rank_rows(hotels, limit)
    return {
        "city": city,
        "city_rank": city_rows,
        "discount_rank": discount_rows,
        "area_rank": area_rows,
        "meta": meta,
    }


def resolve_holiday_code(holiday_contains: str) -> str:
    target = str(holiday_contains or "").strip()
    if "::" in target:
        return target
    calendar = HolidayCalendar()
    for item in calendar.get_upcoming_holidays():
        if target and (target in item.code or target in item.name or target == item.start.isoformat()):
            return item.code
    raise RuntimeError(f"无法根据 {holiday_contains} 匹配法定假期。")


def run_city_search_refresh(city: str, holiday_contains: str) -> None:
    holiday_code = resolve_holiday_code(holiday_contains)
    finder = ReverseTravelFinder(HolidayCalendar())
    last_stage = ""

    def progress_callback(progress: dict[str, Any]) -> None:
        nonlocal last_stage
        stage = str(progress.get("stage") or "")
        message = str(progress.get("message") or "")
        if stage == last_stage:
            return
        last_stage = stage
        print(f"[{city}] {message}", file=sys.stderr, flush=True)

    finder.find_choices(
        city=city,
        holiday_code=holiday_code,
        min_price=None,
        max_price=None,
        advanced_filter="yes",
        pool_filter="all",
        child_facility_filter="all",
        use_cache=False,
        cache_only=False,
        progress_callback=progress_callback,
    )


def build_city_payload_with_low_count_rerun(
    *,
    city: str,
    cache_dir: Path,
    holiday_contains: str,
    limit: int,
    max_rise: int,
    min_city_rank_count: int,
    rerun_under_count: bool,
) -> dict[str, Any]:
    payload = build_city_payload(
        city=city,
        cache_dir=cache_dir,
        holiday_contains=holiday_contains,
        limit=limit,
        max_rise=max_rise,
    )
    threshold = max(0, min(min_city_rank_count, limit))
    first_count = len(payload["city_rank"])
    if not rerun_under_count or threshold <= 0 or first_count >= threshold:
        return payload

    payload["meta"]["low_count_rerun"] = {
        "triggered": True,
        "threshold": threshold,
        "first_city_rank_count": first_count,
        "status": "started",
    }
    print(
        f"[{city}] 城市榜单只有 {first_count} 家，低于 {threshold} 家，强制新搜索一次。",
        file=sys.stderr,
        flush=True,
    )
    try:
        run_city_search_refresh(city, holiday_contains)
    except Exception as exc:  # noqa: BLE001
        payload["meta"]["low_count_rerun"].update(
            {
                "status": "failed",
                "error": str(exc),
                "final_city_rank_count": first_count,
            }
        )
        print(f"[{city}] 兜底重跑失败，保留首次榜单：{exc}", file=sys.stderr, flush=True)
        return payload

    refreshed = build_city_payload(
        city=city,
        cache_dir=cache_dir,
        holiday_contains=holiday_contains,
        limit=limit,
        max_rise=max_rise,
    )
    second_count = len(refreshed["city_rank"])
    refreshed["meta"]["low_count_rerun"] = {
        "triggered": True,
        "threshold": threshold,
        "first_city_rank_count": first_count,
        "final_city_rank_count": second_count,
        "status": "used_second_result",
    }
    print(f"[{city}] 兜底重跑完成，城市榜单为 {second_count} 家。", file=sys.stderr, flush=True)
    return refreshed


def render_markdown(payloads: list[dict[str, Any]]) -> str:
    lines = [
        "# 2026端午最新反向旅游城市与片区榜",
        "",
        "口径：四星级以上；端午每晚含税均价对比未来一月非法定假期代表时段均价；仅保留不涨价或降价酒店。",
        "参考价格仅供参考，实际价格以各大软件订购为准。制作人：小中",
        "",
    ]
    for payload in payloads:
        city = payload["city"]
        meta = payload["meta"]
        lines.extend(
            [
                f"## {city}｜城市酒店榜单",
                "",
                f"本榜单按本地最新数据生成：纳入{meta.get('included_hotel_count', 0)}家符合条件的高级酒店。",
                "",
            ]
        )
        if payload["city_rank"]:
            lines.extend(hotel_line(index, item) for index, item in enumerate(payload["city_rank"], start=1))
        else:
            lines.append("暂无符合条件的酒店。")
        lines.extend(["", f"## {city}｜降价重点榜", ""])
        if payload["discount_rank"]:
            lines.extend(hotel_line(index, item) for index, item in enumerate(payload["discount_rank"], start=1))
        else:
            lines.append("暂无降价酒店。")
        lines.extend(["", f"## {city}｜片区榜单", ""])
        if payload["area_rank"]:
            lines.extend(area_line(item) for item in payload["area_rank"])
        else:
            lines.append("暂无可推荐片区。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_city_artifacts(payload: dict[str, Any]) -> None:
    city = payload["city"]
    export_dir = EXPORT_DIRS.get(city)
    if export_dir is None:
        return
    export_dir.mkdir(parents=True, exist_ok=True)
    source_path = export_dir / "source_data.json"
    data: dict[str, Any] = {}
    if source_path.exists():
        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["latest_city_rank"] = payload["city_rank"]
    data["latest_discount_rank"] = payload["discount_rank"]
    data["area_region_rank"] = payload["area_rank"]
    data["latest_rank_source"] = payload["meta"]
    source_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown([payload])
    (export_dir / "latest_city_area_rank.md").write_text(markdown, encoding="utf-8")


def main() -> None:
    args = parse_args()
    limit = max(1, args.limit)
    payloads = [
        build_city_payload_with_low_count_rerun(
            city=city,
            cache_dir=Path(args.cache_dir),
            holiday_contains=args.holiday_contains,
            limit=limit,
            max_rise=args.max_rise,
            min_city_rank_count=max(0, args.min_city_rank_count),
            rerun_under_count=not args.no_rerun_under_count,
        )
        for city in args.city
    ]
    for payload in payloads:
        write_city_artifacts(payload)
    markdown = render_markdown(payloads)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
