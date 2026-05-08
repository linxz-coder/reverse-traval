from __future__ import annotations

import argparse
import datetime as dt
import json
from urllib.request import Request, urlopen


STARTER_CITIES = (
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
    "成都",
    "重庆",
    "武汉",
    "西安",
    "长沙",
    "厦门",
    "三亚",
)


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
    batch_size = max(1, min(batch_size, len(cities)))
    batch_count = (len(cities) + batch_size - 1) // batch_size
    batch_index = day.toordinal() % batch_count
    start = batch_index * batch_size
    batch = list(cities[start : start + batch_size])
    if len(batch) < batch_size:
        batch.extend(cities[: batch_size - len(batch)])
    return batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Start a low-rate nightly reverse-travel cache prewarm batch.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5012")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--holiday-count", type=int, default=3)
    parser.add_argument("--holiday-code", action="append", dest="holiday_codes")
    parser.add_argument("--profiles", default="default,quality")
    parser.add_argument("--delay-seconds", type=int, default=90)
    parser.add_argument("--cities", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cities:
        cities = [item.strip() for item in args.cities.split(",") if item.strip()]
    else:
        cities = rotating_batch(STARTER_CITIES, args.batch_size, dt.date.today())

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


if __name__ == "__main__":
    main()
