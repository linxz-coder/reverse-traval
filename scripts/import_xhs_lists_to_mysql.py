#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    import pymysql
except ImportError:  # pragma: no cover
    pymysql = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "database" / "schema.sql"
DEFAULT_SOURCE_GLOB = "exports/xhs_*_duanwu_2026/source_data.json"
LIST_TYPES = ("star_no_rise", "family_no_rise", "discount_star")
LIST_FILTERS = {
    "star_no_rise": ("yes", "all", "all"),
    "family_no_rise": ("yes", "yes", "yes"),
    "discount_star": ("yes", "all", "all"),
}
CITY_FROM_PATH = {
    "shenzhen": "深圳",
    "guangzhou": "广州",
    "foshan": "佛山",
}
TRADITIONAL_CHARS = set(
    "廣國際會萬樂從宮順燈鵝賓館豐悅麗雲門華爾頓溫動長設計荔蓮鳳亞優選鐵獅龍聯創產業楓鷺歡嶺東區凱爾"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import generated Xiaohongshu hotel lists into local MySQL.")
    parser.add_argument("sources", nargs="*", help="source_data.json files or folders containing source_data.json")
    parser.add_argument("--host", default=os.getenv("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("MYSQL_USER", "root"))
    parser.add_argument("--password", default=os.getenv("MYSQL_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("MYSQL_DATABASE", "reverse_travel_archive"))
    parser.add_argument("--create-schema", action="store_true", help="create/update MySQL tables before importing")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--replace", action="store_true", help="replace a snapshot if the same payload hash was imported")
    parser.add_argument("--dry-run", action="store_true", help="parse and validate data without connecting to MySQL")
    return parser.parse_args()


def discover_sources(values: list[str]) -> list[Path]:
    if not values:
        return sorted(ROOT.glob(DEFAULT_SOURCE_GLOB))
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            candidate = path / "source_data.json"
            if candidate.exists():
                paths.append(candidate)
            else:
                paths.extend(sorted(path.glob("*/source_data.json")))
        else:
            paths.append(path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_generated_id(city: str, name: str) -> str:
    digest = hashlib.sha1(f"{city}:{name}".encode("utf-8")).hexdigest()[:16]
    return f"manual-{digest}"


def infer_city(path: Path, data: dict[str, Any]) -> str:
    city = str(data.get("city") or "").strip()
    if city:
        return city
    lowered = str(path).lower()
    for token, label in CITY_FROM_PATH.items():
        if token in lowered:
            return label
    raise ValueError(f"Cannot infer city for {path}")


def has_traditional_chinese(value: str) -> bool:
    return any(char in TRADITIONAL_CHARS for char in str(value or ""))


def normalize_bool(value: Any) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def normalize_source(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    city = infer_city(path, data)
    if "lists" in data:
        lists = data["lists"]
        created_texts = data.get("created_at_texts") or []
    else:
        lists = {key: data.get(key, []) for key in LIST_TYPES}
        created_texts = ["2026-05-06 至 2026-05-08"] if city == "深圳" else []
    normalized_lists: dict[str, list[dict[str, Any]]] = {}
    name_errors: list[str] = []
    for list_type in LIST_TYPES:
        normalized_lists[list_type] = []
        for index, raw in enumerate(lists.get(list_type) or [], start=1):
            name = str(raw.get("name") or raw.get("hotel_name") or "").strip()
            if not name:
                raise ValueError(f"{path}: missing hotel name in {list_type} rank {index}")
            if has_traditional_chinese(name):
                name_errors.append(f"{city} {list_type} #{index}: {name}")
            price = raw.get("price") or raw.get("holiday_avg_nightly_tax_total_value")
            diff = raw.get("diff") or raw.get("price_diff_nightly") or 0
            if price is None:
                raise ValueError(f"{path}: missing price for {name}")
            hotel_id = str(raw.get("hotel_id") or raw.get("external_hotel_id") or stable_generated_id(city, name))
            item = {
                "external_hotel_id": hotel_id,
                "hotel_name_zh_cn": name,
                "hotel_name_original": str(raw.get("hotel_name_original") or raw.get("original_name") or "").strip() or None,
                "hotel_name_source": str(raw.get("hotel_name_source") or "XHS榜单人工/脚本确认").strip(),
                "city_name": city,
                "area_name": str(raw.get("area") or raw.get("area_name") or "").strip() or None,
                "rank_no": index,
                "holiday_avg_nightly_tax_total_cny": float(price),
                "price_diff_nightly_cny": float(diff),
                "comparison_avg_nightly_tax_total_cny": float(price) - float(diff),
                "recommendation_reason": str(raw.get("note") or raw.get("recommendation_reason") or "").strip() or None,
                "room_type_label": str(raw.get("room_type_label") or "").strip() or None,
                "is_advanced": 1,
                "has_pool": normalize_bool(raw.get("pool") if "pool" in raw else raw.get("has_pool")),
                "has_child_facility": normalize_bool(
                    raw.get("child") if "child" in raw else raw.get("has_child_facility")
                ),
            }
            if list_type == "family_no_rise":
                item["has_pool"] = 1 if item["has_pool"] is None else item["has_pool"]
                item["has_child_facility"] = 1 if item["has_child_facility"] is None else item["has_child_facility"]
            normalized_lists[list_type].append(item)
    if name_errors:
        details = "\n".join(f"  - {line}" for line in name_errors[:20])
        raise ValueError(f"{path}: hotel names must be simplified Chinese before import:\n{details}")
    return {
        "path": str(path),
        "payload_hash": sha256_text(text),
        "city": city,
        "created_texts": created_texts,
        "lists": normalized_lists,
    }


def split_sql_statements(sql: str) -> list[str]:
    cleaned: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        cleaned.append(line)
    statements = []
    current: list[str] = []
    for chunk in "\n".join(cleaned).split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
        current.clear()
    return statements


def connect(args: argparse.Namespace, with_database: bool = True):
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed. Run: pip install PyMySQL")
    return pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database if with_database else None,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def create_schema(args: argparse.Namespace) -> None:
    schema_path = Path(args.schema)
    if not schema_path.is_absolute():
        schema_path = ROOT / schema_path
    sql = schema_path.read_text(encoding="utf-8").replace("reverse_travel_archive", args.database)
    conn = connect(args, with_database=False)
    try:
        with conn.cursor() as cursor:
            for statement in split_sql_statements(sql):
                cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()


def upsert_hotel(cursor, item: dict[str, Any]) -> int:
    cursor.execute(
        """
        INSERT INTO hotels (
          external_source, external_hotel_id, city_name, area_name, hotel_name_zh_cn,
          hotel_name_original, hotel_name_source, is_advanced, has_pool, has_child_facility
        ) VALUES (
          'trip.com', %(external_hotel_id)s, %(city_name)s, %(area_name)s, %(hotel_name_zh_cn)s,
          %(hotel_name_original)s, %(hotel_name_source)s, %(is_advanced)s, %(has_pool)s, %(has_child_facility)s
        )
        ON DUPLICATE KEY UPDATE
          id = LAST_INSERT_ID(id),
          city_name = VALUES(city_name),
          area_name = COALESCE(VALUES(area_name), area_name),
          hotel_name_zh_cn = VALUES(hotel_name_zh_cn),
          hotel_name_original = COALESCE(VALUES(hotel_name_original), hotel_name_original),
          hotel_name_source = VALUES(hotel_name_source),
          is_advanced = COALESCE(VALUES(is_advanced), is_advanced),
          has_pool = COALESCE(VALUES(has_pool), has_pool),
          has_child_facility = COALESCE(VALUES(has_child_facility), has_child_facility)
        """,
        item,
    )
    hotel_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT IGNORE INTO hotel_name_aliases (hotel_id, alias_name, alias_type, source)
        VALUES (%s, %s, 'simplified', %s)
        """,
        (hotel_id, item["hotel_name_zh_cn"], item["hotel_name_source"]),
    )
    if item.get("hotel_name_original") and item["hotel_name_original"] != item["hotel_name_zh_cn"]:
        cursor.execute(
            """
            INSERT IGNORE INTO hotel_name_aliases (hotel_id, alias_name, alias_type, source)
            VALUES (%s, %s, 'platform', %s)
            """,
            (hotel_id, item["hotel_name_original"], item["hotel_name_source"]),
        )
    return hotel_id


def insert_snapshot(cursor, source: dict[str, Any], replace: bool) -> int | None:
    cursor.execute("SELECT id FROM list_snapshots WHERE source_payload_hash=%s", (source["payload_hash"],))
    existing = cursor.fetchone()
    if existing and not replace:
        return None
    if existing and replace:
        cursor.execute("DELETE FROM list_snapshots WHERE id=%s", (existing["id"],))
    snapshot_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"reverse-travel:{source['payload_hash']}"))
    cursor.execute(
        """
        INSERT INTO list_snapshots (
          snapshot_uuid, source_payload_hash, city_name, holiday_name, holiday_code,
          check_in, check_out, nights, data_source, source_path, source_cache_created_texts,
          filter_advanced, filter_pool, filter_child_facility, notes
        ) VALUES (
          %s, %s, %s, '端午节', '2026-06-19::端午节',
          '2026-06-19', '2026-06-22', 3, 'Trip.com', %s, %s,
          'yes', 'all', 'all', %s
        )
        """,
        (
            snapshot_uuid,
            source["payload_hash"],
            source["city"],
            source["path"],
            ", ".join(source["created_texts"]),
            "小红书榜单归档；价格为每晚含税参考价，实际以各大软件订购为准。",
        ),
    )
    return int(cursor.lastrowid)


def import_source(conn, source: dict[str, Any], replace: bool) -> tuple[int, int, int]:
    with conn.cursor() as cursor:
        snapshot_id = insert_snapshot(cursor, source, replace=replace)
        if snapshot_id is None:
            return (0, 0, 0)
        hotel_count = 0
        entry_count = 0
        seen_hotels: set[int] = set()
        for list_type, items in source["lists"].items():
            filter_advanced, filter_pool, filter_child = LIST_FILTERS[list_type]
            for item in items:
                hotel_pk = upsert_hotel(cursor, item)
                if hotel_pk not in seen_hotels:
                    seen_hotels.add(hotel_pk)
                    hotel_count += 1
                cursor.execute(
                    """
                    INSERT INTO list_entries (
                      snapshot_id, list_type, rank_no, hotel_id,
                      filter_advanced, filter_pool, filter_child_facility,
                      is_advanced, has_pool, has_child_facility,
                      holiday_avg_nightly_tax_total_cny, comparison_avg_nightly_tax_total_cny,
                      price_diff_nightly_cny, room_type_label, recommendation_reason
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        snapshot_id,
                        list_type,
                        item["rank_no"],
                        hotel_pk,
                        filter_advanced,
                        filter_pool,
                        filter_child,
                        item["is_advanced"],
                        item["has_pool"],
                        item["has_child_facility"],
                        item["holiday_avg_nightly_tax_total_cny"],
                        item["comparison_avg_nightly_tax_total_cny"],
                        item["price_diff_nightly_cny"],
                        item["room_type_label"],
                        item["recommendation_reason"],
                    ),
                )
                entry_pk = int(cursor.lastrowid)
                cursor.execute(
                    """
                    INSERT INTO price_observations (
                      snapshot_id, hotel_id, list_entry_id, check_in, check_out, nights,
                      room_type_label, holiday_avg_nightly_tax_total_cny,
                      comparison_avg_nightly_tax_total_cny, price_diff_nightly_cny
                    ) VALUES (
                      %s, %s, %s, '2026-06-19', '2026-06-22', 3,
                      %s, %s, %s, %s
                    )
                    """,
                    (
                        snapshot_id,
                        hotel_pk,
                        entry_pk,
                        item["room_type_label"],
                        item["holiday_avg_nightly_tax_total_cny"],
                        item["comparison_avg_nightly_tax_total_cny"],
                        item["price_diff_nightly_cny"],
                    ),
                )
                entry_count += 1
        conn.commit()
        return (1, hotel_count, entry_count)


def main() -> int:
    args = parse_args()
    paths = discover_sources(args.sources)
    if not paths:
        print(f"No source_data.json files found. Default glob: {DEFAULT_SOURCE_GLOB}", file=sys.stderr)
        return 1
    sources = [normalize_source(path) for path in paths]
    if args.dry_run:
        for source in sources:
            entry_count = sum(len(source["lists"][key]) for key in LIST_TYPES)
            hotel_ids = {
                item["external_hotel_id"]
                for key in LIST_TYPES
                for item in source["lists"][key]
            }
            print(f"DRY RUN {source['city']}: {len(hotel_ids)} hotels, {entry_count} entries, {source['path']}")
        return 0
    if args.create_schema:
        create_schema(args)
    conn = connect(args, with_database=True)
    try:
        total_snapshots = total_hotels = total_entries = 0
        for source in sources:
            snapshots, hotels, entries = import_source(conn, source, replace=args.replace)
            total_snapshots += snapshots
            total_hotels += hotels
            total_entries += entries
            if snapshots:
                print(f"Imported {source['city']}: {hotels} hotels, {entries} entries")
            else:
                print(f"Skipped {source['city']}: same payload already imported")
        print(f"Done: {total_snapshots} snapshots, {total_hotels} hotels, {total_entries} entries")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
