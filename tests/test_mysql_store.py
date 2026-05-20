import json

from mysql_store import MySQLHotelStore


def test_daily_recommended_hotel_row_normalizes_payload():
    store = MySQLHotelStore()

    row = store._daily_recommended_hotel_row(
        {
            "date": "2026-06-19",
            "refresh_slot": "2026-06-19T06",
            "refresh_hours": 6,
            "city": "深圳",
            "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
            "feature_filters": {"advanced_filter": "yes"},
            "cache": {"created_at": "2026-05-21T08:40:12+08:00", "age_seconds": "72"},
            "cache_key": ["search", "深圳", "2026-06-19::端午节"],
            "hotel": {
                "hotel_id": "daily-1",
                "hotel_name": "深圳落库推荐酒店",
                "hotel_original_name": "Daily Test Hotel Shenzhen",
                "hotel_name_source": "人工审核中文名",
                "area_name": "福田中心",
                "detail_url": "https://example.test/daily-1",
                "image_url": "https://images.example.test/daily-1.jpg",
                "room_type_label": "高级大床房",
                "is_advanced": True,
                "has_pool": "true",
                "has_child_facility": "是",
                "holiday_avg_nightly_tax_total_price": "CNY 680",
                "holiday_tax_total_price": "CNY 1,360",
                "comparison_average_nightly_tax_total_price": "CNY 760",
                "comparison_lowest_nightly_tax_total_price": "CNY 750",
                "comparison_lowest_check_in": "2026-06-22",
                "comparison_lowest_check_out": "2026-06-24",
                "comparison_sample_count": "4",
                "price_diff_nightly_text": "CNY -80",
            },
        }
    )

    assert row["refresh_slot"] == "2026-06-19T06"
    assert row["hotel_id"] == "daily-1"
    assert row["hotel_name_zh"] == "深圳落库推荐酒店"
    assert row["is_advanced"] == 1
    assert row["has_pool"] == 1
    assert row["has_child_facility"] == 1
    assert row["holiday_avg_nightly_tax_total_cny"] == 680
    assert row["holiday_tax_total_cny"] == 1360
    assert row["price_diff_nightly_cny"] == -80
    assert row["cache_created_at"].startswith("2026-05-21 08:40:12")
    assert row["cache_age_seconds"] == 72
    assert json.loads(row["cache_key_json"]) == ["search", "深圳", "2026-06-19::端午节"]
    assert json.loads(row["feature_filters_json"]) == {"advanced_filter": "yes"}


def test_daily_recommended_hotel_sql_targets_separate_table():
    sql = MySQLHotelStore._daily_recommended_hotel_upsert_sql()

    assert "INSERT INTO daily_recommended_hotels" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
