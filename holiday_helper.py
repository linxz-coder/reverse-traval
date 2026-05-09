from __future__ import annotations

import datetime as dt
import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

API_URL = "https://www.iamwawa.cn/workingday/api"
USER_AGENT = "iamwawa-open-api"
CACHE_PATH = Path.home() / ".cache" / "reverse_travel_workingday_cache.json"
WEEKEND_INFOS = {"双休日", "周末", "休息日", "星期六", "星期日", "周六", "周日"}
OFFICIAL_2026_HOLIDAYS = [
    ("元旦", dt.date(2026, 1, 1), dt.date(2026, 1, 3)),
    ("春节", dt.date(2026, 2, 15), dt.date(2026, 2, 23)),
    ("清明节", dt.date(2026, 4, 4), dt.date(2026, 4, 6)),
    ("劳动节", dt.date(2026, 5, 1), dt.date(2026, 5, 5)),
    ("端午节", dt.date(2026, 6, 19), dt.date(2026, 6, 21)),
    ("中秋节", dt.date(2026, 9, 25), dt.date(2026, 9, 27)),
    ("国庆节", dt.date(2026, 10, 1), dt.date(2026, 10, 7)),
]


class HolidayCalendarError(RuntimeError):
    pass


@dataclass
class HolidayRange:
    code: str
    name: str
    start: dt.date
    end: dt.date
    days: int

    @property
    def check_out(self) -> dt.date:
        return self.end + dt.timedelta(days=1)


class HolidayCalendar:
    def __init__(self) -> None:
        self._cache = self._load_cache()
        self._cache_lock = threading.Lock()
        self._direct_opener = build_opener(ProxyHandler({}))

    def get_upcoming_holidays(self, days_ahead: int = 260, today: dt.date | None = None) -> list[HolidayRange]:
        today = today or dt.date.today()
        if today.year == 2026:
            official_items = self._official_2026_upcoming_holidays(today)
            if official_items:
                return official_items

        return self._fetch_upcoming_holidays_from_api(today=today, days_ahead=days_ahead)

    def _official_2026_upcoming_holidays(self, today: dt.date) -> list[HolidayRange]:
        items = []
        for name, start, end in OFFICIAL_2026_HOLIDAYS:
            if end < today:
                continue
            code = f"{start.isoformat()}::{name}"
            items.append(HolidayRange(code=code, name=name, start=start, end=end, days=(end - start).days + 1))
        return items

    def _fetch_upcoming_holidays_from_api(self, *, today: dt.date, days_ahead: int) -> list[HolidayRange]:
        target_days = [today + dt.timedelta(days=offset) for offset in range(days_ahead + 1)]
        rows: list[tuple[dt.date, dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            for day, row in zip(target_days, executor.map(self._fetch_day, target_days)):
                if self._is_statutory_holiday(row):
                    rows.append((day, row))

        grouped: list[HolidayRange] = []
        current: list[tuple[dt.date, dict[str, Any]]] = []
        for day, row in rows:
            if not current:
                current = [(day, row)]
                continue
            prev_day, prev_row = current[-1]
            same_name = self._holiday_name(prev_row) == self._holiday_name(row)
            if day == prev_day + dt.timedelta(days=1) and same_name:
                current.append((day, row))
                continue
            grouped.append(self._build_holiday(current))
            current = [(day, row)]

        if current:
            grouped.append(self._build_holiday(current))

        return grouped

    def is_statutory_holiday(self, day: dt.date) -> bool:
        return self._is_statutory_holiday(self._fetch_day(day))

    def _build_holiday(self, items: list[tuple[dt.date, dict[str, Any]]]) -> HolidayRange:
        start = items[0][0]
        end = items[-1][0]
        name = self._holiday_name(items[0][1])
        code = f"{start.isoformat()}::{name}"
        return HolidayRange(code=code, name=name, start=start, end=end, days=len(items))

    def _holiday_name(self, row: dict[str, Any]) -> str:
        return str(row.get("info", "")).strip()

    def _is_statutory_holiday(self, row: dict[str, Any]) -> bool:
        info = str(row.get("info", "")).strip()
        return int(row.get("is_workingday", 0)) == 0 and bool(info) and info not in WEEKEND_INFOS

    def _fetch_day(self, day: dt.date, max_retries: int = 30) -> dict[str, Any]:
        date_str = day.isoformat()
        with self._cache_lock:
            if date_str in self._cache:
                return self._cache[date_str]

        query = urlencode({"date": date_str})
        req = Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT})
        for attempt in range(max_retries + 1):
            try:
                with self._direct_opener.open(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                raise HolidayCalendarError(f"法定假日接口请求失败({exc.code})，日期 {date_str}") from exc
            except URLError as exc:
                raise HolidayCalendarError(f"法定假日接口网络错误，日期 {date_str}: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise HolidayCalendarError(f"法定假日接口返回非JSON，日期 {date_str}") from exc

            if data.get("status") == 1:
                with self._cache_lock:
                    self._cache[date_str] = data
                    self._save_cache()
                return data

            info = str(data.get("info", ""))
            retry_seconds = self._parse_retry_seconds(info)
            if retry_seconds is not None and attempt < max_retries:
                time.sleep(retry_seconds + 1)
                continue
            raise HolidayCalendarError(f"法定假日接口返回异常，日期 {date_str}: {data}")

        raise HolidayCalendarError(f"法定假日接口重试次数已用尽，日期 {date_str}")

    def _parse_retry_seconds(self, info: str) -> int | None:
        match = re.search(r"(\d+)秒钟后再试", info)
        if match:
            return int(match.group(1))
        return None

    def _load_cache(self) -> dict[str, Any]:
        if not CACHE_PATH.exists():
            return {}
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
