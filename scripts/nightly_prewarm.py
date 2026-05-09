from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from urllib.request import Request, urlopen


CORE_CITIES = (
    "深圳",
    "广州",
    "东莞",
    "惠州",
    "汕尾",
    "北京",
    "上海",
)

POPULAR_CITIES = (
    "深圳",
    "广州",
    "东莞",
    "惠州",
    "汕尾",
    "珠海",
    "中山",
    "佛山",
    "江门",
    "肇庆",
    "韶关",
    "河源",
    "北京",
    "上海",
    "杭州",
    "苏州",
    "南京",
    "无锡",
    "宁波",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "长沙",
    "郑州",
    "天津",
    "青岛",
    "济南",
    "厦门",
    "福州",
    "昆明",
    "贵阳",
    "南宁",
    "海口",
    "三亚",
    "大连",
    "沈阳",
    "哈尔滨",
    "香港",
    "澳门",
    "台北",
    "曼谷",
    "吉隆坡",
    "新加坡",
    "东京",
    "大阪",
    "首尔",
    "雅加达",
    "迪拜",
    "巴黎",
    "伦敦",
    "纽约",
    "芝加哥",
    "拉斯维加斯",
    "洛杉矶",
    "柏林",
    "莫斯科",
    "圣保罗",
)
STARTER_CITIES = tuple(dict.fromkeys((*CORE_CITIES, *POPULAR_CITIES)))


def read_json(url: str) -> dict:
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def rotating_batch(cities: tuple[str, ...], batch_size: int, day: dt.date) -> list[str]:
    if batch_size <= 0 or not cities:
        return []
    batch_size = max(1, min(batch_size, len(cities)))
    batch_count = (len(cities) + batch_size - 1) // batch_size
    batch_index = day.toordinal() % batch_count
    start = batch_index * batch_size
    batch = list(cities[start : start + batch_size])
    if len(batch) < batch_size:
        batch.extend(cities[: batch_size - len(batch)])
    return batch


def parse_city_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def unique_cities(cities: list[str]) -> list[str]:
    return list(dict.fromkeys(cities))


def build_city_batch(
    *,
    day: dt.date,
    batch_size: int,
    priority_cities: list[str],
    city_pool: tuple[str, ...] = STARTER_CITIES,
) -> list[str]:
    priority = unique_cities(priority_cities)
    rotating_pool = tuple(city for city in city_pool if city not in priority)
    return unique_cities(priority + rotating_batch(rotating_pool, batch_size, day))


def wait_for_prewarm(status_url: str, timeout_seconds: int, interval_seconds: int) -> dict:
    started_at = time.monotonic()
    last_state: dict = {}
    while True:
        last_state = read_json(status_url)
        print(json.dumps({"watch": True, "status": last_state}, ensure_ascii=False))
        if last_state.get("status") in {"succeeded", "failed", "idle"}:
            return {"completed": True, "status": last_state}
        if time.monotonic() - started_at >= timeout_seconds:
            return {"completed": False, "timeout_seconds": timeout_seconds, "status": last_state}
        time.sleep(max(5, interval_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Start a low-rate nightly reverse-travel cache prewarm batch.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5012")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--holiday-count", type=int, default=3)
    parser.add_argument("--holiday-code", action="append", dest="holiday_codes")
    parser.add_argument("--profiles", default="default,quality")
    parser.add_argument("--delay-seconds", type=int, default=60)
    parser.add_argument("--priority-cities", default=",".join(CORE_CITIES))
    parser.add_argument("--max-runtime-seconds", type=int, default=19800)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--watch-interval", type=int, default=60)
    parser.add_argument("--watch-timeout", type=int, default=21600)
    parser.add_argument("--cities", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cities:
        cities = unique_cities(parse_city_list(args.cities))
    else:
        cities = build_city_batch(
            day=dt.date.today(),
            batch_size=args.batch_size,
            priority_cities=parse_city_list(args.priority_cities),
        )

    if args.holiday_codes:
        holiday_codes = args.holiday_codes
    elif args.dry_run:
        holiday_codes = [f"AUTO_FIRST_{max(1, args.holiday_count)}_HOLIDAYS"]
    else:
        holidays = read_json(f"{args.base_url}/api/holidays").get("holidays") or []
        holiday_codes = [item["code"] for item in holidays[: max(1, args.holiday_count)] if item.get("code")]

    payload = {
        "cities": cities,
        "profiles": [item.strip() for item in args.profiles.split(",") if item.strip()],
        "holiday_codes": holiday_codes,
        "delay_seconds": str(max(0, args.delay_seconds)),
        "max_runtime_seconds": str(max(0, args.max_runtime_seconds)),
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, "payload": payload}, ensure_ascii=False, indent=2))
        return

    status_url = f"{args.base_url}/api/admin/prewarm/status"
    start_url = f"{args.base_url}/api/admin/prewarm/start"
    status = read_json(status_url)
    if status.get("status") == "running":
        print(json.dumps({"skipped": True, "reason": "prewarm already running", "status": status}, ensure_ascii=False))
        return

    state = post_json(start_url, payload)
    print(json.dumps({"started": True, "payload": payload, "state": state}, ensure_ascii=False, indent=2))
    if args.wait:
        final_state = wait_for_prewarm(status_url, args.watch_timeout, args.watch_interval)
        print(json.dumps(final_state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
