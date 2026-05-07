from __future__ import annotations

import argparse
import json
import time
from urllib.request import Request, urlopen


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Start or inspect reverse-travel cache prewarming.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5012")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--city-limit", type=int, default=None)
    parser.add_argument("--profiles", default="default")
    parser.add_argument("--holiday-code", action="append", dest="holiday_codes")
    parser.add_argument("--delay-seconds", type=int, default=1)
    args = parser.parse_args()

    status_url = f"{args.base_url}/api/admin/prewarm/status"
    if args.status:
        print(json.dumps(read_json(status_url), ensure_ascii=False, indent=2))
        return

    payload = {
        "city_preset": "major",
        "profiles": [item.strip() for item in args.profiles.split(",") if item.strip()],
        "delay_seconds": str(args.delay_seconds),
    }
    if args.city_limit:
        payload["city_limit"] = str(args.city_limit)
    if args.holiday_codes:
        payload["holiday_codes"] = args.holiday_codes

    state = post_json(f"{args.base_url}/api/admin/prewarm/start", payload)
    print(json.dumps(state, ensure_ascii=False, indent=2))

    if not args.watch:
        return
    while True:
        time.sleep(10)
        state = read_json(status_url)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        if state.get("status") in {"succeeded", "failed", "idle"}:
            return


if __name__ == "__main__":
    main()
