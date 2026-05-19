from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from typing import Any

try:  # Optional in tests and non-MySQL deploys.
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover
    pymysql = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment]


MYSQL_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MYSQL_PRICE_RE = re.compile(r"-?\d[\d,]*")
MYSQL_SOURCE_VALUES = {"dom", "api", "cache", "manual", "import"}
HOTEL_NAME_REVIEW_STATUSES = {"pending", "approved", "rejected"}
AREA_REVIEW_STATUSES = {"pending", "approved", "rejected"}


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit and text else text


def _parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    match = MYSQL_DATE_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _nights(check_in: Any, check_out: Any, fallback: int = 1) -> int:
    start = _parse_date(check_in)
    end = _parse_date(check_out)
    if start and end and end > start:
        return max(1, (end - start).days)
    return max(1, int(fallback or 1))


def _parse_price(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value or "")
    match = MYSQL_PRICE_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _format_cny(value: Any) -> str:
    price = _parse_price(value)
    return f"CNY {price:,}" if price is not None else ""


def _format_cny_diff(value: Any) -> str:
    price = _parse_price(value)
    if price is None:
        return ""
    return f"+CNY {price:,}" if price > 0 else f"CNY {price:,}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_list_loads(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def choice_identity(item: dict[str, Any], fallback_index: int = 0) -> str:
    recommend_city = _clean_text(item.get("recommend_city"), 64)
    hotel_id = _clean_text(item.get("hotel_id") or item.get("trip_hotel_id"), 64)
    room_type = _clean_text(item.get("room_type") or item.get("room_name_zh") or item.get("holiday_room_name"), 128)
    if hotel_id:
        return f"{recommend_city}:{hotel_id}:{room_type}"
    detail_url = _clean_text(item.get("detail_url"), 1024)
    if detail_url:
        return f"{recommend_city}:{detail_url}:{room_type}"
    return f"{recommend_city}:{_clean_text(item.get('hotel_name'), 255)}:{room_type}:{fallback_index}"


class MySQLHotelStore:
    def __init__(self) -> None:
        self.enabled = _env_bool("REVERSE_TRAVEL_MYSQL_ENABLED", True)
        self.host = os.environ.get("REVERSE_TRAVEL_MYSQL_HOST", "127.0.0.1")
        self.port = _env_int("REVERSE_TRAVEL_MYSQL_PORT", 3306)
        self.user = os.environ.get("REVERSE_TRAVEL_MYSQL_USER", "reverse_travel_app")
        self.password = os.environ.get("REVERSE_TRAVEL_MYSQL_PASSWORD", "")
        self.database = os.environ.get("REVERSE_TRAVEL_MYSQL_DATABASE", "reverse_travel_rankings")
        self.connect_timeout = _env_int("REVERSE_TRAVEL_MYSQL_CONNECT_TIMEOUT", 2)
        self._lock = threading.Lock()
        self._disabled_reason = ""
        self._hotel_name_corrections_table_ready = False
        self._hotel_area_corrections_table_ready = False
        self._hotel_area_merge_corrections_table_ready = False

    def is_configured(self) -> bool:
        return bool(self.enabled and pymysql is not None and self.user and self.database and self.password)

    def disabled_reason(self) -> str:
        if not self.enabled:
            return "disabled"
        if pymysql is None:
            return "pymysql_missing"
        if not self.password:
            return "password_missing"
        return self._disabled_reason

    def _connect(self):
        if not self.is_configured():
            return None
        try:
            return pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset="utf8mb4",
                autocommit=True,
                connect_timeout=self.connect_timeout,
                read_timeout=4,
                write_timeout=4,
                cursorclass=DictCursor,
            )
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return None

    def hotel_name_records(self, hotel_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [_clean_text(value, 64) for value in hotel_ids if _clean_text(value, 64)]
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        approved_records = self.approved_hotel_name_records(ids)
        approved_area_records = self.approved_hotel_area_records(ids)
        conn = self._connect()
        rows = []
        if conn is not None:
            placeholders = ", ".join(["%s"] * len(ids))
            sql = f"""
                SELECT hotel_id, hotel_name_zh, hotel_name_original, area_name_zh, detail_url
                FROM hotel_profiles
                WHERE hotel_id IN ({placeholders})
            """
            try:
                with conn:
                    with conn.cursor() as cursor:
                        cursor.execute(sql, ids)
                        rows = cursor.fetchall()
            except Exception as exc:  # noqa: BLE001
                self._disabled_reason = str(exc)
                rows = []
        records: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            hotel_id = _clean_text(row.get("hotel_id"), 64)
            if not hotel_id:
                continue
            records[hotel_id] = {
                "hotel_name": _clean_text(row.get("hotel_name_zh"), 255),
                "hotel_name_simplified": _clean_text(row.get("hotel_name_zh"), 255),
                "hotel_name_original": _clean_text(row.get("hotel_name_original"), 255),
                "source": "MySQL酒店名缓存",
                "area_name": _clean_text(row.get("area_name_zh"), 128),
                "detail_url": _clean_text(row.get("detail_url"), 1024),
            }
        for hotel_id, record in approved_records.items():
            existing = records.get(hotel_id) or {}
            records[hotel_id] = {
                **existing,
                "hotel_name": record["hotel_name"],
                "hotel_name_simplified": record["hotel_name"],
                "hotel_name_original": existing.get("hotel_name_original") or record.get("hotel_name_original", ""),
                "source": "人工审核中文名",
                "detail_url": existing.get("detail_url") or record.get("detail_url", ""),
            }
        for hotel_id, record in approved_area_records.items():
            existing = records.get(hotel_id) or {}
            records[hotel_id] = {
                **existing,
                "area_name": record["area_name"],
                "area_source": "人工审核片区",
                "detail_url": existing.get("detail_url") or record.get("detail_url", ""),
            }
        return records

    def approved_hotel_name_records(self, hotel_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [_clean_text(value, 64) for value in hotel_ids if _clean_text(value, 64)]
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        conn = self._connect()
        if conn is None:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"""
            SELECT c.hotel_id, c.suggested_hotel_name_zh, c.hotel_name_original, c.detail_url, c.id
            FROM hotel_name_corrections c
            INNER JOIN (
              SELECT hotel_id, MAX(id) AS id
              FROM hotel_name_corrections
              WHERE status = 'approved'
                AND hotel_id IN ({placeholders})
              GROUP BY hotel_id
            ) latest ON latest.id = c.id
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    self._ensure_hotel_name_corrections_table(cursor)
                    cursor.execute(sql, ids)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {}
        records: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            hotel_id = _clean_text(row.get("hotel_id"), 64)
            name = _clean_text(row.get("suggested_hotel_name_zh"), 255)
            if not hotel_id or not name:
                continue
            records[hotel_id] = {
                "hotel_name": name,
                "hotel_name_original": _clean_text(row.get("hotel_name_original"), 255),
                "detail_url": _clean_text(row.get("detail_url"), 1024),
                "review_id": row.get("id"),
            }
        return records

    def approved_hotel_area_records(self, hotel_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [_clean_text(value, 64) for value in hotel_ids if _clean_text(value, 64)]
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        conn = self._connect()
        if conn is None:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"""
            SELECT c.hotel_id, c.suggested_area_name_zh, c.detail_url, c.id
            FROM hotel_area_corrections c
            INNER JOIN (
              SELECT hotel_id, MAX(id) AS id
              FROM hotel_area_corrections
              WHERE status = 'approved'
                AND hotel_id IN ({placeholders})
              GROUP BY hotel_id
            ) latest ON latest.id = c.id
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    self._ensure_hotel_area_corrections_table(cursor)
                    cursor.execute(sql, ids)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {}
        records: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            hotel_id = _clean_text(row.get("hotel_id"), 64)
            area_name = _clean_text(row.get("suggested_area_name_zh"), 128)
            if not hotel_id or not area_name:
                continue
            records[hotel_id] = {
                "area_name": area_name,
                "detail_url": _clean_text(row.get("detail_url"), 1024),
                "review_id": row.get("id"),
            }
        return records

    def store_search_result(self, result: dict[str, Any], *, search_job_key: str = "", source: str = "api") -> int:
        if not isinstance(result, dict):
            return 0
        choices = [item for item in result.get("choices") or [] if isinstance(item, dict)]
        if not choices:
            return 0
        conn = self._connect()
        if conn is None:
            return 0

        profiles = self._profile_rows(result, choices)
        observations = self._observation_rows(result, choices, search_job_key=search_job_key, source=source)
        if not profiles and not observations:
            return 0

        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        if profiles:
                            cursor.executemany(self._profile_upsert_sql(), profiles)
                        if observations:
                            cursor.executemany(self._observation_insert_sql(), observations)
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return 0
        return len(observations)

    def latest_price_preview(
        self,
        *,
        city_name: str,
        holiday_code: str,
        check_in: str,
        check_out: str,
        min_price: int | None = None,
        max_price: int | None = None,
        advanced_filter: str | None = "all",
        pool_filter: str | None = "all",
        child_facility_filter: str | None = "all",
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        city = _clean_text(city_name, 64)
        holiday = _clean_text(holiday_code, 64)
        check_in_text = _date_text(check_in)
        check_out_text = _date_text(check_out)
        if not city or not holiday or not check_in_text or not check_out_text:
            return []
        conn = self._connect()
        if conn is None:
            return []

        sql = """
            SELECT *
            FROM (
              SELECT
                hpo.*,
                hp.image_url AS profile_image_url,
                ROW_NUMBER() OVER (
                  PARTITION BY
                    hpo.price_role,
                    COALESCE(NULLIF(hpo.hotel_id, ''), NULLIF(hpo.trip_hotel_id, ''), hpo.hotel_name_zh),
                    COALESCE(hpo.room_name_zh, ''),
                    hpo.check_in_date,
                    hpo.check_out_date
                  ORDER BY hpo.observed_at DESC, hpo.id DESC
                ) AS row_num
              FROM hotel_price_observations hpo
              LEFT JOIN hotel_profiles hp
                ON hp.hotel_id = COALESCE(NULLIF(hpo.hotel_id, ''), NULLIF(hpo.trip_hotel_id, ''))
              WHERE hpo.city_name_zh = %s
                AND hpo.holiday_code = %s
                AND hpo.is_available = 1
                AND (
                  (
                    hpo.price_role = 'holiday'
                    AND hpo.check_in_date = %s
                    AND hpo.check_out_date = %s
                  )
                  OR hpo.price_role = 'comparison'
                )
            ) latest
            WHERE latest.row_num = 1
            ORDER BY latest.observed_at DESC
            LIMIT 400
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, (city, holiday, check_in_text, check_out_text))
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return []

        approved_records = self.approved_hotel_name_records(
            [_clean_text(row.get("hotel_id") or row.get("trip_hotel_id"), 64) for row in rows or []]
        )
        approved_area_records = self.approved_hotel_area_records(
            [_clean_text(row.get("hotel_id") or row.get("trip_hotel_id"), 64) for row in rows or []]
        )
        holiday_rows: list[dict[str, Any]] = []
        comparison_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        comparison_by_hotel: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            row = dict(row)
            row_hotel_id = _clean_text(row.get("hotel_id") or row.get("trip_hotel_id"), 64)
            approved = approved_records.get(row_hotel_id)
            if approved:
                row["hotel_name_zh"] = approved["hotel_name"]
                row["hotel_name_source"] = "人工审核中文名"
            approved_area = approved_area_records.get(row_hotel_id)
            if approved_area:
                row["area_name_zh"] = approved_area["area_name"]
                row["area_source"] = "人工审核片区"
            role = row.get("price_role")
            hotel_key = _clean_text(row.get("hotel_id") or row.get("trip_hotel_id") or row.get("hotel_name_zh"), 255)
            room_key = _clean_text(row.get("room_name_zh"), 255)
            if not hotel_key:
                continue
            if role == "holiday":
                holiday_rows.append(row)
            elif role == "comparison":
                comparison_by_key.setdefault((hotel_key, room_key), row)
                comparison_by_hotel.setdefault(hotel_key, row)

        choices: list[dict[str, Any]] = []
        for row in holiday_rows:
            if not self._row_matches_filters(row, advanced_filter, pool_filter, child_facility_filter):
                continue
            holiday_value = _parse_price(row.get("avg_nightly_tax_included_price_cny") or row.get("tax_included_price_cny"))
            if holiday_value is None:
                continue
            if min_price is not None and holiday_value < min_price:
                continue
            if max_price is not None and holiday_value > max_price:
                continue

            hotel_key = _clean_text(row.get("hotel_id") or row.get("trip_hotel_id") or row.get("hotel_name_zh"), 255)
            room_key = _clean_text(row.get("room_name_zh"), 255)
            comparison = comparison_by_key.get((hotel_key, room_key)) or comparison_by_hotel.get(hotel_key)
            if not comparison:
                continue
            compare_value = _parse_price(
                comparison.get("avg_nightly_tax_included_price_cny") or comparison.get("tax_included_price_cny")
            )
            if compare_value is None:
                continue
            diff = holiday_value - compare_value
            if diff > 100:
                continue
            raw = _json_loads(row.get("raw_price_json"))
            comparison_raw = _json_loads(comparison.get("raw_price_json"))
            choices.append(
                self._choice_from_preview_row(
                    row,
                    comparison,
                    raw=raw,
                    comparison_raw=comparison_raw,
                    holiday_value=holiday_value,
                    compare_value=compare_value,
                    diff=diff,
                )
            )

        choices.sort(
            key=lambda item: (
                int(item.get("price_diff_nightly") or 0),
                int(item.get("holiday_avg_nightly_tax_total_value") or 0),
            )
        )
        return choices[: max(1, limit)]

    def _row_matches_filters(
        self,
        row: dict[str, Any],
        advanced_filter: str | None,
        pool_filter: str | None,
        child_facility_filter: str | None,
    ) -> bool:
        for field, value in (
            ("is_advanced", advanced_filter),
            ("has_pool", pool_filter),
            ("has_child_facility", child_facility_filter),
        ):
            normalized = str(value or "all").strip().lower()
            if normalized == "yes" and row.get(field) != 1:
                return False
            if normalized == "no" and row.get(field) == 1:
                return False
        return True

    def _choice_from_preview_row(
        self,
        row: dict[str, Any],
        comparison: dict[str, Any],
        *,
        raw: dict[str, Any],
        comparison_raw: dict[str, Any],
        holiday_value: int,
        compare_value: int,
        diff: int,
    ) -> dict[str, Any]:
        hotel_name = _clean_text(row.get("hotel_name_zh"), 255) or _clean_text(raw.get("hotel_name"), 255)
        original_name = _clean_text(row.get("hotel_name_original"), 255) or _clean_text(raw.get("hotel_original_name"), 255)
        room_name = _clean_text(row.get("room_name_zh"), 255) or _clean_text(raw.get("holiday_room_name"), 255)
        comparison_check_in = _date_text(comparison.get("check_in_date")) or _clean_text(comparison_raw.get("comparison_lowest_check_in"))
        comparison_check_out = _date_text(comparison.get("check_out_date")) or _clean_text(comparison_raw.get("comparison_lowest_check_out"))
        return {
            "hotel_id": _clean_text(row.get("hotel_id") or row.get("trip_hotel_id"), 64),
            "hotel_name": hotel_name,
            "hotel_original_name": original_name,
            "hotel_name_source": _clean_text(row.get("hotel_name_source"), 64) or "MySQL价格缓存",
            "area_name": _clean_text(row.get("area_name_zh"), 128) or _clean_text(raw.get("area_name"), 128),
            "area_hint": _clean_text(raw.get("area_hint"), 255),
            "area_source": _clean_text(raw.get("area_source"), 64) or "MySQL价格缓存",
            "is_advanced": row.get("is_advanced"),
            "has_pool": row.get("has_pool"),
            "has_child_facility": row.get("has_child_facility"),
            "room_type": _clean_text(raw.get("room_type"), 64),
            "room_type_label": _clean_text(raw.get("room_type_label"), 64) or _clean_text(room_name, 64),
            "holiday_room_name": room_name,
            "holiday_room_price": _format_cny(row.get("base_price_cny")) or _clean_text(raw.get("holiday_room_price")),
            "holiday_tax_total_price": _format_cny(row.get("tax_included_price_cny")),
            "holiday_tax_total_value": _parse_price(row.get("tax_included_price_cny")) or 0,
            "holiday_avg_nightly_tax_total_price": _format_cny(holiday_value),
            "holiday_avg_nightly_tax_total_value": holiday_value,
            "comparison_average_nightly_tax_total_price": _format_cny(compare_value),
            "comparison_average_nightly_tax_total_value": compare_value,
            "comparison_sample_count": int(raw.get("comparison_sample_count") or 1),
            "comparison_lowest_room_name": _clean_text(comparison.get("room_name_zh"), 255)
            or _clean_text(comparison_raw.get("comparison_lowest_room_name"), 255),
            "comparison_room_type_fallback": bool(raw.get("comparison_room_type_fallback")),
            "comparison_lowest_room_price": _format_cny(comparison.get("base_price_cny"))
            or _clean_text(comparison_raw.get("comparison_lowest_room_price")),
            "comparison_lowest_tax_total_price": _format_cny(comparison.get("tax_included_price_cny")),
            "comparison_lowest_tax_total_value": _parse_price(comparison.get("tax_included_price_cny")) or 0,
            "comparison_lowest_nightly_tax_total_price": _format_cny(compare_value),
            "comparison_lowest_nightly_tax_total_value": compare_value,
            "comparison_lowest_check_in": comparison_check_in or "",
            "comparison_lowest_check_out": comparison_check_out or "",
            "price_diff_nightly": diff,
            "price_diff_nightly_text": _format_cny_diff(diff),
            "detail_url": _clean_text(row.get("detail_url") or raw.get("detail_url"), 1024),
            "image_url": _clean_text(raw.get("image_url") or row.get("profile_image_url"), 1024),
            "price_cache_preview": True,
        }

    def submit_hotel_name_correction(self, item: dict[str, Any]) -> dict[str, Any]:
        conn = self._connect()
        if conn is None:
            return {"ok": False, "error": self.disabled_reason() or "mysql_unavailable"}
        row = {
            "hotel_id": _clean_text(item.get("hotel_id"), 64),
            "trip_hotel_id": _clean_text(item.get("trip_hotel_id") or item.get("hotel_id"), 64),
            "city_name_zh": _clean_text(item.get("city_name_zh") or item.get("city"), 64),
            "current_hotel_name_zh": _clean_text(item.get("current_hotel_name_zh") or item.get("current_name"), 255),
            "hotel_name_original": _clean_text(item.get("hotel_name_original"), 255),
            "suggested_hotel_name_zh": _clean_text(item.get("suggested_hotel_name_zh") or item.get("suggested_name"), 255),
            "area_name_zh": _clean_text(item.get("area_name_zh") or item.get("area_name"), 128),
            "detail_url": _clean_text(item.get("detail_url"), 1024),
            "user_note": _clean_text(item.get("user_note"), 500),
            "client_id": _clean_text(item.get("client_id"), 80),
        }
        if not row["hotel_id"] or not row["suggested_hotel_name_zh"]:
            return {"ok": False, "error": "missing_required_fields"}
        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        self._ensure_hotel_name_corrections_table(cursor)
                        cursor.execute(
                            """
                            SELECT id
                            FROM hotel_name_corrections
                            WHERE hotel_id = %s
                              AND suggested_hotel_name_zh = %s
                              AND status = 'pending'
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (row["hotel_id"], row["suggested_hotel_name_zh"]),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            return {"ok": True, "id": existing["id"], "status": "pending", "duplicate": True}
                        cursor.execute(
                            """
                            INSERT INTO hotel_name_corrections (
                              hotel_id, trip_hotel_id, city_name_zh, current_hotel_name_zh,
                              hotel_name_original, suggested_hotel_name_zh, area_name_zh,
                              detail_url, user_note, client_id, status
                            ) VALUES (
                              %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(current_hotel_name_zh)s,
                              %(hotel_name_original)s, %(suggested_hotel_name_zh)s, %(area_name_zh)s,
                              %(detail_url)s, %(user_note)s, %(client_id)s, 'pending'
                            )
                            """,
                            row,
                        )
                        correction_id = cursor.lastrowid
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": correction_id, "status": "pending", "duplicate": False}

    def hotel_name_corrections(self, status: str = "pending", limit: int = 30) -> list[dict[str, Any]]:
        normalized_status = _clean_text(status, 32).lower()
        if normalized_status not in HOTEL_NAME_REVIEW_STATUSES and normalized_status != "all":
            normalized_status = "pending"
        safe_limit = max(1, min(100, int(limit or 30)))
        conn = self._connect()
        if conn is None:
            return []
        where_sql = "" if normalized_status == "all" else "WHERE status = %s"
        params: list[Any] = [] if normalized_status == "all" else [normalized_status]
        params.append(safe_limit)
        sql = f"""
            SELECT id, hotel_id, trip_hotel_id, city_name_zh, current_hotel_name_zh,
                   hotel_name_original, suggested_hotel_name_zh, area_name_zh,
                   detail_url, user_note, client_id, status, reviewer_note,
                   reviewed_at, created_at, updated_at
            FROM hotel_name_corrections
            {where_sql}
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              created_at DESC,
              id DESC
            LIMIT %s
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    self._ensure_hotel_name_corrections_table(cursor)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return []
        return [self._serialize_hotel_name_correction(row) for row in rows or []]

    def hotel_name_correction_summary(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"pending": [], "recent": [], "disabled_reason": self.disabled_reason()}
        return {
            "pending": self.hotel_name_corrections("pending", 30),
            "recent": self.hotel_name_corrections("all", 30),
            "disabled_reason": self.disabled_reason(),
        }

    def review_hotel_name_correction(
        self,
        correction_id: int,
        action: str,
        reviewer_note: str = "",
    ) -> dict[str, Any]:
        normalized_action = _clean_text(action, 32).lower()
        status = {"approve": "approved", "approved": "approved", "reject": "rejected", "rejected": "rejected"}.get(
            normalized_action
        )
        if not status:
            return {"ok": False, "error": "invalid_action"}
        conn = self._connect()
        if conn is None:
            return {"ok": False, "error": self.disabled_reason() or "mysql_unavailable"}
        note = _clean_text(reviewer_note, 500)
        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        self._ensure_hotel_name_corrections_table(cursor)
                        cursor.execute(
                            """
                            SELECT *
                            FROM hotel_name_corrections
                            WHERE id = %s
                            LIMIT 1
                            """,
                            (int(correction_id),),
                        )
                        row = cursor.fetchone()
                        if not row:
                            return {"ok": False, "error": "not_found"}
                        if status == "approved":
                            self._upsert_approved_hotel_name(cursor, row)
                        cursor.execute(
                            """
                            UPDATE hotel_name_corrections
                            SET status = %s,
                                reviewer_note = %s,
                                reviewed_at = NOW(),
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (status, note, int(correction_id)),
                        )
                        cursor.execute("SELECT * FROM hotel_name_corrections WHERE id = %s", (int(correction_id),))
                        updated = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {"ok": False, "error": str(exc)}
        correction = self._serialize_hotel_name_correction(updated or row)
        return {"ok": True, "status": status, "correction": correction}

    def submit_hotel_area_correction(self, item: dict[str, Any]) -> dict[str, Any]:
        conn = self._connect()
        if conn is None:
            return {"ok": False, "error": self.disabled_reason() or "mysql_unavailable"}
        row = {
            "hotel_id": _clean_text(item.get("hotel_id"), 64),
            "trip_hotel_id": _clean_text(item.get("trip_hotel_id") or item.get("hotel_id"), 64),
            "city_name_zh": _clean_text(item.get("city_name_zh") or item.get("city"), 64),
            "hotel_name_zh": _clean_text(item.get("hotel_name_zh") or item.get("hotel_name"), 255),
            "hotel_name_original": _clean_text(item.get("hotel_name_original"), 255),
            "current_area_name_zh": _clean_text(item.get("current_area_name_zh") or item.get("current_area_name"), 128),
            "suggested_area_name_zh": _clean_text(item.get("suggested_area_name_zh") or item.get("suggested_area_name"), 128),
            "detail_url": _clean_text(item.get("detail_url"), 1024),
            "user_note": _clean_text(item.get("user_note"), 500),
            "client_id": _clean_text(item.get("client_id"), 80),
        }
        if not row["hotel_id"] or not row["suggested_area_name_zh"]:
            return {"ok": False, "error": "missing_required_fields"}
        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        self._ensure_hotel_area_corrections_table(cursor)
                        cursor.execute(
                            """
                            SELECT id
                            FROM hotel_area_corrections
                            WHERE hotel_id = %s
                              AND suggested_area_name_zh = %s
                              AND status = 'pending'
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (row["hotel_id"], row["suggested_area_name_zh"]),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            return {"ok": True, "id": existing["id"], "status": "pending", "duplicate": True}
                        cursor.execute(
                            """
                            INSERT INTO hotel_area_corrections (
                              hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh,
                              hotel_name_original, current_area_name_zh, suggested_area_name_zh,
                              detail_url, user_note, client_id, status
                            ) VALUES (
                              %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(hotel_name_zh)s,
                              %(hotel_name_original)s, %(current_area_name_zh)s, %(suggested_area_name_zh)s,
                              %(detail_url)s, %(user_note)s, %(client_id)s, 'pending'
                            )
                            """,
                            row,
                        )
                        correction_id = cursor.lastrowid
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": correction_id, "status": "pending", "duplicate": False}

    def hotel_area_corrections(self, status: str = "pending", limit: int = 30) -> list[dict[str, Any]]:
        normalized_status = _clean_text(status, 32).lower()
        if normalized_status not in AREA_REVIEW_STATUSES and normalized_status != "all":
            normalized_status = "pending"
        safe_limit = max(1, min(100, int(limit or 30)))
        conn = self._connect()
        if conn is None:
            return []
        where_sql = "" if normalized_status == "all" else "WHERE status = %s"
        params: list[Any] = [] if normalized_status == "all" else [normalized_status]
        params.append(safe_limit)
        sql = f"""
            SELECT id, hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh,
                   hotel_name_original, current_area_name_zh, suggested_area_name_zh,
                   detail_url, user_note, client_id, status, reviewer_note,
                   reviewed_at, created_at, updated_at
            FROM hotel_area_corrections
            {where_sql}
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              created_at DESC,
              id DESC
            LIMIT %s
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    self._ensure_hotel_area_corrections_table(cursor)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return []
        return [self._serialize_hotel_area_correction(row) for row in rows or []]

    def hotel_area_correction_summary(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"pending": [], "recent": [], "disabled_reason": self.disabled_reason()}
        return {
            "pending": self.hotel_area_corrections("pending", 30),
            "recent": self.hotel_area_corrections("all", 30),
            "disabled_reason": self.disabled_reason(),
        }

    def review_hotel_area_correction(
        self,
        correction_id: int,
        action: str,
        reviewer_note: str = "",
    ) -> dict[str, Any]:
        normalized_action = _clean_text(action, 32).lower()
        status = {"approve": "approved", "approved": "approved", "reject": "rejected", "rejected": "rejected"}.get(
            normalized_action
        )
        if not status:
            return {"ok": False, "error": "invalid_action"}
        conn = self._connect()
        if conn is None:
            return {"ok": False, "error": self.disabled_reason() or "mysql_unavailable"}
        note = _clean_text(reviewer_note, 500)
        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        self._ensure_hotel_area_corrections_table(cursor)
                        cursor.execute(
                            """
                            SELECT *
                            FROM hotel_area_corrections
                            WHERE id = %s
                            LIMIT 1
                            """,
                            (int(correction_id),),
                        )
                        row = cursor.fetchone()
                        if not row:
                            return {"ok": False, "error": "not_found"}
                        if status == "approved":
                            self._upsert_approved_hotel_area(cursor, row)
                        cursor.execute(
                            """
                            UPDATE hotel_area_corrections
                            SET status = %s,
                                reviewer_note = %s,
                                reviewed_at = NOW(),
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (status, note, int(correction_id)),
                        )
                        cursor.execute("SELECT * FROM hotel_area_corrections WHERE id = %s", (int(correction_id),))
                        updated = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {"ok": False, "error": str(exc)}
        correction = self._serialize_hotel_area_correction(updated or row)
        return {"ok": True, "status": status, "correction": correction}

    def submit_area_merge_correction(self, item: dict[str, Any]) -> dict[str, Any]:
        conn = self._connect()
        if conn is None:
            return {"ok": False, "error": self.disabled_reason() or "mysql_unavailable"}
        source_areas = item.get("source_areas") if isinstance(item.get("source_areas"), list) else []
        hotels = item.get("hotels") if isinstance(item.get("hotels"), list) else []
        row = {
            "city_name_zh": _clean_text(item.get("city_name_zh") or item.get("city"), 64),
            "suggested_area_name_zh": _clean_text(item.get("suggested_area_name_zh") or item.get("suggested_area_name"), 128),
            "source_areas_json": _json_dumps(source_areas),
            "hotels_json": _json_dumps(hotels),
            "hotel_count": len(hotels),
            "user_note": _clean_text(item.get("user_note"), 500),
            "client_id": _clean_text(item.get("client_id"), 80),
        }
        if len(source_areas) < 1 or not row["suggested_area_name_zh"] or not hotels:
            return {"ok": False, "error": "missing_required_fields"}
        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        self._ensure_hotel_area_merge_corrections_table(cursor)
                        cursor.execute(
                            """
                            SELECT id
                            FROM hotel_area_merge_corrections
                            WHERE city_name_zh = %s
                              AND suggested_area_name_zh = %s
                              AND source_areas_json = %s
                              AND status = 'pending'
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (row["city_name_zh"], row["suggested_area_name_zh"], row["source_areas_json"]),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            return {"ok": True, "id": existing["id"], "status": "pending", "duplicate": True}
                        cursor.execute(
                            """
                            INSERT INTO hotel_area_merge_corrections (
                              city_name_zh, suggested_area_name_zh, source_areas_json,
                              hotels_json, hotel_count, user_note, client_id, status
                            ) VALUES (
                              %(city_name_zh)s, %(suggested_area_name_zh)s, %(source_areas_json)s,
                              %(hotels_json)s, %(hotel_count)s, %(user_note)s, %(client_id)s, 'pending'
                            )
                            """,
                            row,
                        )
                        correction_id = cursor.lastrowid
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": correction_id, "status": "pending", "duplicate": False}

    def area_merge_corrections(self, status: str = "pending", limit: int = 30) -> list[dict[str, Any]]:
        normalized_status = _clean_text(status, 32).lower()
        if normalized_status not in AREA_REVIEW_STATUSES and normalized_status != "all":
            normalized_status = "pending"
        safe_limit = max(1, min(100, int(limit or 30)))
        conn = self._connect()
        if conn is None:
            return []
        where_sql = "" if normalized_status == "all" else "WHERE status = %s"
        params: list[Any] = [] if normalized_status == "all" else [normalized_status]
        params.append(safe_limit)
        sql = f"""
            SELECT id, city_name_zh, suggested_area_name_zh, source_areas_json,
                   hotels_json, hotel_count, user_note, client_id, status,
                   reviewer_note, reviewed_at, created_at, updated_at
            FROM hotel_area_merge_corrections
            {where_sql}
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              created_at DESC,
              id DESC
            LIMIT %s
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    self._ensure_hotel_area_merge_corrections_table(cursor)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return []
        return [self._serialize_area_merge_correction(row) for row in rows or []]

    def area_merge_correction_summary(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"pending": [], "recent": [], "disabled_reason": self.disabled_reason()}
        return {
            "pending": self.area_merge_corrections("pending", 30),
            "recent": self.area_merge_corrections("all", 30),
            "disabled_reason": self.disabled_reason(),
        }

    def active_area_merge_corrections(self, city_names: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        if conn is None:
            return []
        safe_limit = max(1, min(200, int(limit or 100)))
        cities = list(
            dict.fromkeys(
                _clean_text(city, 64)
                for city in (city_names or [])
                if _clean_text(city, 64)
            )
        )
        params: list[Any] = ["pending", "approved"]
        city_sql = ""
        if cities:
            placeholders = ", ".join(["%s"] * len(cities))
            city_sql = f"AND city_name_zh IN ({placeholders})"
            params.extend(cities)
        params.append(safe_limit)
        sql = f"""
            SELECT id, city_name_zh, suggested_area_name_zh, source_areas_json,
                   hotels_json, hotel_count, user_note, client_id, status,
                   reviewer_note, reviewed_at, created_at, updated_at
            FROM hotel_area_merge_corrections
            WHERE status IN (%s, %s)
              {city_sql}
            ORDER BY id DESC
            LIMIT %s
        """
        try:
            with conn:
                with conn.cursor() as cursor:
                    self._ensure_hotel_area_merge_corrections_table(cursor)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return []
        return [self._serialize_area_merge_correction(row) for row in rows or []]

    def review_area_merge_correction(
        self,
        correction_id: int,
        action: str,
        reviewer_note: str = "",
    ) -> dict[str, Any]:
        normalized_action = _clean_text(action, 32).lower()
        status = {"approve": "approved", "approved": "approved", "reject": "rejected", "rejected": "rejected"}.get(
            normalized_action
        )
        if not status:
            return {"ok": False, "error": "invalid_action"}
        conn = self._connect()
        if conn is None:
            return {"ok": False, "error": self.disabled_reason() or "mysql_unavailable"}
        note = _clean_text(reviewer_note, 500)
        approved_count = 0
        try:
            with self._lock:
                with conn:
                    with conn.cursor() as cursor:
                        self._ensure_hotel_area_merge_corrections_table(cursor)
                        cursor.execute(
                            """
                            SELECT *
                            FROM hotel_area_merge_corrections
                            WHERE id = %s
                            LIMIT 1
                            """,
                            (int(correction_id),),
                        )
                        row = cursor.fetchone()
                        if not row:
                            return {"ok": False, "error": "not_found"}
                        if status == "approved":
                            self._ensure_hotel_area_corrections_table(cursor)
                            approved_count = self._approve_area_merge_hotels(cursor, row)
                        cursor.execute(
                            """
                            UPDATE hotel_area_merge_corrections
                            SET status = %s,
                                reviewer_note = %s,
                                reviewed_at = NOW(),
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (status, note, int(correction_id)),
                        )
                        cursor.execute(
                            "SELECT * FROM hotel_area_merge_corrections WHERE id = %s",
                            (int(correction_id),),
                        )
                        updated = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = str(exc)
            return {"ok": False, "error": str(exc)}
        correction = self._serialize_area_merge_correction(updated or row)
        return {"ok": True, "status": status, "correction": correction, "approved_count": approved_count}

    def _profile_rows(self, result: dict[str, Any], choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        result_city = _clean_text(result.get("city"), 64)
        for item in choices:
            hotel_id = _clean_text(item.get("hotel_id"), 64)
            if not hotel_id or hotel_id in seen:
                continue
            seen.add(hotel_id)
            rows.append(
                {
                    "hotel_id": hotel_id,
                    "trip_hotel_id": hotel_id,
                    "city_name_zh": _clean_text(item.get("recommend_city") or result_city, 64),
                    "hotel_name_zh": _clean_text(item.get("hotel_name_simplified") or item.get("hotel_name"), 255),
                    "hotel_name_original": _clean_text(item.get("hotel_original_name"), 255),
                    "area_name_zh": _clean_text(item.get("area_name"), 128),
                    "detail_url": _clean_text(item.get("detail_url"), 1024),
                    "image_url": _clean_text(item.get("image_url"), 1024),
                    "tags_json": _json_dumps(
                        {
                            "hotel_name_source": item.get("hotel_name_source") or "",
                            "room_type": item.get("room_type") or "",
                        }
                    ),
                    "facilities_json": _json_dumps(
                        {
                            "is_advanced": item.get("is_advanced"),
                            "has_pool": item.get("has_pool"),
                            "has_child_facility": item.get("has_child_facility"),
                        }
                    ),
                }
            )
        return rows

    def _observation_rows(
        self,
        result: dict[str, Any],
        choices: list[dict[str, Any]],
        *,
        search_job_key: str,
        source: str,
    ) -> list[dict[str, Any]]:
        holiday = result.get("holiday") or {}
        holiday_code = _clean_text(holiday.get("code"), 64)
        holiday_check_in = _date_text(holiday.get("check_in"))
        holiday_check_out = _date_text(holiday.get("check_out"))
        holiday_nights = _nights(holiday_check_in, holiday_check_out, int(holiday.get("days") or 1))
        source_value = source if source in MYSQL_SOURCE_VALUES else "api"
        rows: list[dict[str, Any]] = []
        result_city = _clean_text(result.get("city"), 64)
        for item in choices:
            city_name = _clean_text(item.get("recommend_city") or result_city, 64)
            if not city_name:
                continue
            if holiday_check_in and holiday_check_out:
                holiday_total = _parse_price(item.get("holiday_tax_total_value") or item.get("holiday_tax_total_price"))
                holiday_avg = _parse_price(
                    item.get("holiday_avg_nightly_tax_total_value")
                    or item.get("holiday_avg_nightly_tax_total_price")
                )
                if holiday_total is not None or holiday_avg is not None:
                    rows.append(
                        self._observation_row(
                            item,
                            city_name=city_name,
                            holiday_code=holiday_code,
                            price_role="holiday",
                            check_in=holiday_check_in,
                            check_out=holiday_check_out,
                            nights=holiday_nights,
                            room_name=item.get("holiday_room_name"),
                            base_price=item.get("holiday_room_price"),
                            tax_total=holiday_total,
                            avg_nightly=holiday_avg,
                            search_job_key=search_job_key,
                            source=source_value,
                        )
                    )
            comparison_check_in = _date_text(item.get("comparison_lowest_check_in"))
            comparison_check_out = _date_text(item.get("comparison_lowest_check_out"))
            if comparison_check_in and comparison_check_out:
                comparison_nights = _nights(comparison_check_in, comparison_check_out, holiday_nights)
                comparison_total = _parse_price(
                    item.get("comparison_lowest_tax_total_value") or item.get("comparison_lowest_tax_total_price")
                )
                comparison_avg = _parse_price(
                    item.get("comparison_lowest_nightly_tax_total_value")
                    or item.get("comparison_lowest_nightly_tax_total_price")
                    or item.get("comparison_average_nightly_tax_total_value")
                    or item.get("comparison_average_nightly_tax_total_price")
                )
                if comparison_total is not None or comparison_avg is not None:
                    rows.append(
                        self._observation_row(
                            item,
                            city_name=city_name,
                            holiday_code=holiday_code,
                            price_role="comparison",
                            check_in=comparison_check_in,
                            check_out=comparison_check_out,
                            nights=comparison_nights,
                            room_name=item.get("comparison_lowest_room_name") or item.get("holiday_room_name"),
                            base_price=item.get("comparison_lowest_room_price"),
                            tax_total=comparison_total,
                            avg_nightly=comparison_avg,
                            search_job_key=search_job_key,
                            source=source_value,
                        )
                    )
        return rows

    def _observation_row(
        self,
        item: dict[str, Any],
        *,
        city_name: str,
        holiday_code: str,
        price_role: str,
        check_in: str,
        check_out: str,
        nights: int,
        room_name: Any,
        base_price: Any,
        tax_total: Any,
        avg_nightly: Any,
        search_job_key: str,
        source: str,
    ) -> dict[str, Any]:
        base_value = _parse_price(base_price)
        tax_value = _parse_price(tax_total)
        avg_value = _parse_price(avg_nightly)
        return {
            "hotel_id": _clean_text(item.get("hotel_id"), 64) or None,
            "trip_hotel_id": _clean_text(item.get("hotel_id"), 64) or None,
            "city_name_zh": city_name,
            "hotel_name_zh": _clean_text(item.get("hotel_name_simplified") or item.get("hotel_name"), 255),
            "hotel_name_original": _clean_text(item.get("hotel_original_name"), 255),
            "area_name_zh": _clean_text(item.get("area_name"), 128),
            "holiday_code": holiday_code or None,
            "price_role": price_role,
            "price_date": check_in,
            "check_in_date": check_in,
            "check_out_date": check_out,
            "nights": nights,
            "room_name_zh": _clean_text(room_name, 255),
            "room_name_original": _clean_text(room_name, 255),
            "base_price_cny": base_value,
            "tax_fee_cny": None,
            "tax_included_price_cny": tax_value if tax_value is not None else (avg_value * nights if avg_value is not None else None),
            "avg_nightly_tax_included_price_cny": avg_value
            if avg_value is not None
            else (round(tax_value / nights) if tax_value is not None and nights else None),
            "is_available": 1,
            "source": source,
            "search_job_key": _clean_text(search_job_key, 32) or None,
            "raw_price_json": _json_dumps(item),
            "is_advanced": self._bool_int(item.get("is_advanced")),
            "has_pool": self._bool_int(item.get("has_pool")),
            "has_child_facility": self._bool_int(item.get("has_child_facility")),
        }

    @staticmethod
    def _bool_int(value: Any) -> int | None:
        if value is True:
            return 1
        if value is False:
            return 0
        if value in (1, "1", "true", "True", "yes", "是"):
            return 1
        if value in (0, "0", "false", "False", "no", "否"):
            return 0
        return None

    def _ensure_hotel_name_corrections_table(self, cursor) -> None:
        if self._hotel_name_corrections_table_ready:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hotel_name_corrections (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              hotel_id VARCHAR(64) NOT NULL,
              trip_hotel_id VARCHAR(64) DEFAULT NULL,
              city_name_zh VARCHAR(64) DEFAULT NULL,
              current_hotel_name_zh VARCHAR(255) DEFAULT NULL,
              hotel_name_original VARCHAR(255) DEFAULT NULL,
              suggested_hotel_name_zh VARCHAR(255) NOT NULL,
              area_name_zh VARCHAR(128) DEFAULT NULL,
              detail_url VARCHAR(1024) DEFAULT NULL,
              user_note VARCHAR(500) DEFAULT NULL,
              client_id VARCHAR(80) DEFAULT NULL,
              status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
              reviewer_note VARCHAR(500) DEFAULT NULL,
              reviewed_at DATETIME DEFAULT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_hotel_name_corrections_status (status, created_at),
              KEY idx_hotel_name_corrections_hotel (hotel_id, status, id),
              KEY idx_hotel_name_corrections_client (client_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        self._hotel_name_corrections_table_ready = True

    def _ensure_hotel_area_corrections_table(self, cursor) -> None:
        if self._hotel_area_corrections_table_ready:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hotel_area_corrections (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              hotel_id VARCHAR(64) NOT NULL,
              trip_hotel_id VARCHAR(64) DEFAULT NULL,
              city_name_zh VARCHAR(64) DEFAULT NULL,
              hotel_name_zh VARCHAR(255) DEFAULT NULL,
              hotel_name_original VARCHAR(255) DEFAULT NULL,
              current_area_name_zh VARCHAR(128) DEFAULT NULL,
              suggested_area_name_zh VARCHAR(128) NOT NULL,
              detail_url VARCHAR(1024) DEFAULT NULL,
              user_note VARCHAR(500) DEFAULT NULL,
              client_id VARCHAR(80) DEFAULT NULL,
              status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
              reviewer_note VARCHAR(500) DEFAULT NULL,
              reviewed_at DATETIME DEFAULT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_hotel_area_corrections_status (status, created_at),
              KEY idx_hotel_area_corrections_hotel (hotel_id, status, id),
              KEY idx_hotel_area_corrections_client (client_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        self._hotel_area_corrections_table_ready = True

    def _ensure_hotel_area_merge_corrections_table(self, cursor) -> None:
        if self._hotel_area_merge_corrections_table_ready:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hotel_area_merge_corrections (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              city_name_zh VARCHAR(64) DEFAULT NULL,
              suggested_area_name_zh VARCHAR(128) NOT NULL,
              source_areas_json LONGTEXT DEFAULT NULL,
              hotels_json LONGTEXT DEFAULT NULL,
              hotel_count INT UNSIGNED NOT NULL DEFAULT 0,
              user_note VARCHAR(500) DEFAULT NULL,
              client_id VARCHAR(80) DEFAULT NULL,
              status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
              reviewer_note VARCHAR(500) DEFAULT NULL,
              reviewed_at DATETIME DEFAULT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_hotel_area_merge_corrections_status (status, created_at),
              KEY idx_hotel_area_merge_corrections_city (city_name_zh, status, id),
              KEY idx_hotel_area_merge_corrections_client (client_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        self._hotel_area_merge_corrections_table_ready = True

    @staticmethod
    def _serialize_hotel_name_correction(row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row or {})
        for key in ("created_at", "updated_at", "reviewed_at"):
            value = data.get(key)
            if isinstance(value, (dt.datetime, dt.date)):
                data[key] = value.isoformat(sep=" ", timespec="seconds") if isinstance(value, dt.datetime) else value.isoformat()
            elif value is None:
                data[key] = ""
            else:
                data[key] = str(value)
        return data

    @staticmethod
    def _serialize_hotel_area_correction(row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row or {})
        for key in ("created_at", "updated_at", "reviewed_at"):
            value = data.get(key)
            if isinstance(value, (dt.datetime, dt.date)):
                data[key] = value.isoformat(sep=" ", timespec="seconds") if isinstance(value, dt.datetime) else value.isoformat()
            elif value is None:
                data[key] = ""
            else:
                data[key] = str(value)
        return data

    @staticmethod
    def _serialize_area_merge_correction(row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row or {})
        data["source_areas"] = _json_list_loads(data.get("source_areas_json"))
        data["hotels"] = _json_list_loads(data.get("hotels_json"))
        source_names = []
        for area in data["source_areas"]:
            if not isinstance(area, dict):
                continue
            name = _clean_text(area.get("area_name"), 128)
            if name:
                source_names.append(name)
        data["source_area_names"] = source_names
        data.pop("source_areas_json", None)
        data.pop("hotels_json", None)
        for key in ("created_at", "updated_at", "reviewed_at"):
            value = data.get(key)
            if isinstance(value, (dt.datetime, dt.date)):
                data[key] = value.isoformat(sep=" ", timespec="seconds") if isinstance(value, dt.datetime) else value.isoformat()
            elif value is None:
                data[key] = ""
            else:
                data[key] = str(value)
        return data

    def _approve_area_merge_hotels(self, cursor, correction: dict[str, Any]) -> int:
        hotels = _json_list_loads(correction.get("hotels_json"))
        suggested_area = _clean_text(correction.get("suggested_area_name_zh"), 128)
        city_name = _clean_text(correction.get("city_name_zh"), 64)
        client_id = _clean_text(correction.get("client_id"), 80)
        merge_id = correction.get("id")
        source_areas = _json_list_loads(correction.get("source_areas_json"))
        operation_label = "片区改名审核" if len(source_areas) == 1 else "合并片区审核"
        if not suggested_area:
            return 0
        approved_count = 0
        seen: set[str] = set()
        for hotel in hotels:
            if not isinstance(hotel, dict):
                continue
            hotel_id = _clean_text(hotel.get("hotel_id") or hotel.get("trip_hotel_id"), 64)
            if not hotel_id or hotel_id in seen:
                continue
            seen.add(hotel_id)
            row = {
                "hotel_id": hotel_id,
                "trip_hotel_id": _clean_text(hotel.get("trip_hotel_id") or hotel_id, 64),
                "city_name_zh": _clean_text(hotel.get("city_name_zh") or hotel.get("city") or hotel.get("recommend_city"), 64)
                or city_name,
                "hotel_name_zh": _clean_text(hotel.get("hotel_name_zh") or hotel.get("hotel_name"), 255),
                "hotel_name_original": _clean_text(hotel.get("hotel_name_original") or hotel.get("hotel_original_name"), 255),
                "current_area_name_zh": _clean_text(
                    hotel.get("current_area_name_zh") or hotel.get("current_area_name") or hotel.get("area_name"),
                    128,
                ),
                "suggested_area_name_zh": suggested_area,
                "detail_url": _clean_text(hotel.get("detail_url"), 1024),
                "user_note": f"来自{operation_label} #{merge_id}",
                "client_id": client_id,
                "reviewer_note": f"{operation_label} #{merge_id}",
            }
            cursor.execute(
                """
                INSERT INTO hotel_area_corrections (
                  hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh,
                  hotel_name_original, current_area_name_zh, suggested_area_name_zh,
                  detail_url, user_note, client_id, status, reviewer_note, reviewed_at
                ) VALUES (
                  %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(hotel_name_zh)s,
                  %(hotel_name_original)s, %(current_area_name_zh)s, %(suggested_area_name_zh)s,
                  %(detail_url)s, %(user_note)s, %(client_id)s, 'approved', %(reviewer_note)s, NOW()
                )
                """,
                row,
            )
            row["id"] = cursor.lastrowid
            self._upsert_approved_hotel_area(cursor, row)
            approved_count += 1
        return approved_count

    def _upsert_approved_hotel_name(self, cursor, correction: dict[str, Any]) -> None:
        row = {
            "hotel_id": _clean_text(correction.get("hotel_id"), 64),
            "trip_hotel_id": _clean_text(correction.get("trip_hotel_id") or correction.get("hotel_id"), 64),
            "city_name_zh": _clean_text(correction.get("city_name_zh"), 64),
            "hotel_name_zh": _clean_text(correction.get("suggested_hotel_name_zh"), 255),
            "hotel_name_original": _clean_text(correction.get("hotel_name_original"), 255),
            "area_name_zh": _clean_text(correction.get("area_name_zh"), 128),
            "detail_url": _clean_text(correction.get("detail_url"), 1024),
            "tags_json": _json_dumps(
                {
                    "hotel_name_source": "人工审核中文名",
                    "hotel_name_review_id": correction.get("id"),
                }
            ),
        }
        if not row["hotel_id"] or not row["hotel_name_zh"]:
            return
        cursor.execute(
            """
            INSERT INTO hotel_profiles (
              hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh, hotel_name_original,
              area_name_zh, detail_url, tags_json, last_seen_at
            ) VALUES (
              %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(hotel_name_zh)s, %(hotel_name_original)s,
              %(area_name_zh)s, %(detail_url)s, %(tags_json)s, NOW()
            )
            ON DUPLICATE KEY UPDATE
              trip_hotel_id = COALESCE(NULLIF(VALUES(trip_hotel_id), ''), trip_hotel_id),
              city_name_zh = COALESCE(NULLIF(VALUES(city_name_zh), ''), city_name_zh),
              hotel_name_zh = VALUES(hotel_name_zh),
              hotel_name_original = COALESCE(NULLIF(VALUES(hotel_name_original), ''), hotel_name_original),
              area_name_zh = COALESCE(NULLIF(VALUES(area_name_zh), ''), area_name_zh),
              detail_url = COALESCE(NULLIF(VALUES(detail_url), ''), detail_url),
              tags_json = VALUES(tags_json),
              last_seen_at = COALESCE(last_seen_at, NOW()),
              updated_at = NOW()
            """,
            row,
        )

    def _upsert_approved_hotel_area(self, cursor, correction: dict[str, Any]) -> None:
        row = {
            "hotel_id": _clean_text(correction.get("hotel_id"), 64),
            "trip_hotel_id": _clean_text(correction.get("trip_hotel_id") or correction.get("hotel_id"), 64),
            "city_name_zh": _clean_text(correction.get("city_name_zh"), 64),
            "hotel_name_zh": _clean_text(correction.get("hotel_name_zh") or correction.get("hotel_name_original"), 255),
            "hotel_name_original": _clean_text(correction.get("hotel_name_original"), 255),
            "area_name_zh": _clean_text(correction.get("suggested_area_name_zh"), 128),
            "detail_url": _clean_text(correction.get("detail_url"), 1024),
            "tags_json": _json_dumps(
                {
                    "area_source": "人工审核片区",
                    "area_review_id": correction.get("id"),
                }
            ),
        }
        if not row["hotel_id"] or not row["area_name_zh"]:
            return
        if not row["hotel_name_zh"]:
            cursor.execute("SELECT hotel_name_zh FROM hotel_profiles WHERE hotel_id = %s", (row["hotel_id"],))
            existing = cursor.fetchone() or {}
            row["hotel_name_zh"] = _clean_text(existing.get("hotel_name_zh"), 255) or row["area_name_zh"]
        cursor.execute(
            """
            INSERT INTO hotel_profiles (
              hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh, hotel_name_original,
              area_name_zh, detail_url, tags_json, last_seen_at
            ) VALUES (
              %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(hotel_name_zh)s, %(hotel_name_original)s,
              %(area_name_zh)s, %(detail_url)s, %(tags_json)s, NOW()
            )
            ON DUPLICATE KEY UPDATE
              trip_hotel_id = COALESCE(NULLIF(VALUES(trip_hotel_id), ''), trip_hotel_id),
              city_name_zh = COALESCE(NULLIF(VALUES(city_name_zh), ''), city_name_zh),
              hotel_name_zh = COALESCE(NULLIF(VALUES(hotel_name_zh), ''), hotel_name_zh),
              hotel_name_original = COALESCE(NULLIF(VALUES(hotel_name_original), ''), hotel_name_original),
              area_name_zh = VALUES(area_name_zh),
              detail_url = COALESCE(NULLIF(VALUES(detail_url), ''), detail_url),
              tags_json = VALUES(tags_json),
              last_seen_at = COALESCE(last_seen_at, NOW()),
              updated_at = NOW()
            """,
            row,
        )

    @staticmethod
    def _profile_upsert_sql() -> str:
        return """
            INSERT INTO hotel_profiles (
              hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh, hotel_name_original,
              area_name_zh, detail_url, image_url, tags_json, facilities_json, last_seen_at
            ) VALUES (
              %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(hotel_name_zh)s, %(hotel_name_original)s,
              %(area_name_zh)s, %(detail_url)s, %(image_url)s, %(tags_json)s, %(facilities_json)s, NOW()
            )
            ON DUPLICATE KEY UPDATE
              trip_hotel_id = COALESCE(VALUES(trip_hotel_id), trip_hotel_id),
              city_name_zh = COALESCE(NULLIF(VALUES(city_name_zh), ''), city_name_zh),
              hotel_name_zh = COALESCE(NULLIF(VALUES(hotel_name_zh), ''), hotel_name_zh),
              hotel_name_original = COALESCE(NULLIF(VALUES(hotel_name_original), ''), hotel_name_original),
              area_name_zh = COALESCE(NULLIF(VALUES(area_name_zh), ''), area_name_zh),
              detail_url = COALESCE(NULLIF(VALUES(detail_url), ''), detail_url),
              image_url = COALESCE(NULLIF(VALUES(image_url), ''), image_url),
              tags_json = VALUES(tags_json),
              facilities_json = VALUES(facilities_json),
              last_seen_at = NOW(),
              updated_at = NOW()
        """

    @staticmethod
    def _observation_insert_sql() -> str:
        return """
            INSERT INTO hotel_price_observations (
              hotel_id, trip_hotel_id, city_name_zh, hotel_name_zh, hotel_name_original,
              area_name_zh, holiday_code, price_role, price_date, check_in_date, check_out_date,
              nights, room_name_zh, room_name_original, currency_code, base_price_cny, tax_fee_cny,
              tax_included_price_cny, avg_nightly_tax_included_price_cny, is_available,
              source, search_job_key, raw_price_json, is_advanced, has_pool, has_child_facility
            ) VALUES (
              %(hotel_id)s, %(trip_hotel_id)s, %(city_name_zh)s, %(hotel_name_zh)s, %(hotel_name_original)s,
              %(area_name_zh)s, %(holiday_code)s, %(price_role)s, %(price_date)s, %(check_in_date)s, %(check_out_date)s,
              %(nights)s, %(room_name_zh)s, %(room_name_original)s, 'CNY', %(base_price_cny)s, %(tax_fee_cny)s,
              %(tax_included_price_cny)s, %(avg_nightly_tax_included_price_cny)s, %(is_available)s,
              %(source)s, %(search_job_key)s, %(raw_price_json)s, %(is_advanced)s, %(has_pool)s, %(has_child_facility)s
            )
        """


_store: MySQLHotelStore | None = None
_store_lock = threading.Lock()


def get_mysql_store() -> MySQLHotelStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MySQLHotelStore()
    return _store
