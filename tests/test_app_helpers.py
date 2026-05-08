import time

import app as app_module
from app import app as flask_app
from app import nearest_supported_city, nearby_cities_for, normalize_city, parse_bool


def test_nearby_city_helpers_resolve_manual_and_location():
    assert normalize_city("廣州") == "广州"
    assert nearest_supported_city(22.54, 114.05) == "深圳"
    assert nearby_cities_for("深圳", limit=4) == ["汕尾", "惠州", "广州", "东莞"]
    assert nearby_cities_for("珠海", limit=2) == ["中山", "江门"]
    assert nearby_cities_for("苏州", limit=4)
    assert "苏州" not in nearby_cities_for("苏州", limit=4)
    assert nearby_cities_for("北京", limit=2) == ["天津", "廊坊"]


def test_nearby_city_api_returns_national_province_city_options():
    client = flask_app.test_client()

    response = client.get("/api/nearby-cities")

    assert response.status_code == 200
    data = response.get_json()
    province_map = {item["province"]: item["cities"] for item in data["province_cities"]}
    assert "深圳" in province_map["广东"]
    assert "苏州" in province_map["江苏"]
    assert "北京" in province_map["北京"]


def test_parse_bool_accepts_form_values():
    assert parse_bool("true") is True
    assert parse_bool("on") is True
    assert parse_bool("false") is False
    assert parse_bool(None, default=False) is False


def test_api_errors_return_json():
    client = flask_app.test_client()

    response = client.get("/api/not-found")

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"]


def test_background_search_job_returns_result(monkeypatch):
    def fake_find_choices(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({"stage": "fake", "message": "正在测试后台进度", "percent": 55})
        return {
            "city": kwargs["city"],
            "holiday": {
                "code": kwargs["holiday_code"],
                "name": "端午节",
                "check_in": "2026-06-19",
                "check_out": "2026-06-21",
                "days": 3,
            },
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "1", "hotel_name": "测试酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/search/start",
        json={
            "city": "广州",
            "holiday_code": "2026-06-19::端午节",
            "min_price": "",
            "max_price": "",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
        },
    )

    assert response.status_code == 202
    start_data = response.get_json()
    poll_url = start_data["poll_url"]
    data = None
    for _ in range(50):
        poll_response = client.get(poll_url)
        assert poll_response.is_json
        data = poll_response.get_json()
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert data["status"] == "succeeded"
    assert data["progress"]["stage"] == "succeeded"
    assert any(event["message"] == "正在测试后台进度" for event in data["progress_events"])
    assert data["result"]["city"] == "广州"
    assert data["result"]["choices"][0]["hotel_name"] == "测试酒店"


def test_nearby_search_reports_partial_progress(monkeypatch):
    def fake_find_choices(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({"stage": "fake_city", "message": "正在抓取测试城市", "percent": 50})
        city = kwargs["city"]
        return {
            "city": city,
            "holiday": {
                "code": kwargs["holiday_code"],
                "name": "端午节",
                "check_in": "2026-06-19",
                "check_out": "2026-06-21",
                "days": 3,
            },
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [{"check_in": "2026-06-22", "check_out": "2026-06-25"}],
            "area_recommendations": [
                {
                    "area_name": f"{city}测试片区",
                    "hotel_count": 1,
                    "lower_price_hotel_count": 1,
                    "lower_price_ratio": 1,
                    "average_price_diff_nightly": -10,
                    "average_holiday_nightly_tax_total_value": 500,
                }
            ],
            "choices": [
                {
                    "hotel_id": city,
                    "hotel_name": f"{city}测试酒店",
                    "holiday_avg_nightly_tax_total_value": 500,
                    "price_diff_nightly": -10,
                }
            ],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    events = []

    result, status_code = app_module.nearby_search_result_from_payload(
        {
            "origin_city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "nearby_limit": "2",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
        },
        progress_callback=events.append,
    )

    assert status_code == 200
    assert result["nearby_cities"] == ["汕尾", "惠州"]
    assert [item["recommend_city"] for item in result["choices"]] == ["汕尾", "惠州"]
    assert any(event.get("partial_result") for event in events)
    assert any(event.get("completed") == 2 for event in events)


def test_area_refresh_job_returns_normalized_choices(monkeypatch):
    def fake_enhance_area_data(city, choices):
        return {
            "city": city,
            "choices": [{**choices[0], "area_name": "芝加哥卢普片区"}],
            "area_recommendations": [{"area_name": "芝加哥卢普片区", "hotel_count": 1}],
            "area_refresh": {"status": "succeeded", "source": "local"},
        }

    monkeypatch.setattr(app_module.finder, "enhance_area_data", fake_enhance_area_data)
    client = flask_app.test_client()

    response = client.post(
        "/api/areas/start",
        json={"city": "Chicago", "choices": [{"hotel_name": "Loop Hotel", "area_name": "芝加哥Loop片区"}]},
    )

    assert response.status_code == 202
    poll_url = response.get_json()["poll_url"]
    data = None
    for _ in range(50):
        poll_response = client.get(poll_url)
        assert poll_response.is_json
        data = poll_response.get_json()
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert data["status"] == "succeeded"
    assert data["result"]["choices"][0]["area_name"] == "芝加哥卢普片区"


def test_cache_prewarm_background_state(monkeypatch):
    def fake_find_choices(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({"stage": "fake", "message": "正在预热测试缓存", "percent": 40})
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": None, "max_price": None},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "1", "hotel_name": "预热酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    with app_module.prewarm_lock:
        app_module.prewarm_state.clear()
        app_module.prewarm_state.update({"status": "idle", "message": "测试前空闲"})

    state, status_code = app_module.start_cache_prewarm(
        {
            "city_limit": "1",
            "holiday_codes": ["2026-06-19::端午节"],
            "profiles": ["default"],
            "delay_seconds": "0",
        }
    )

    assert status_code == 202
    assert state["status"] in {"queued", "running", "succeeded"}
    final_state = None
    for _ in range(50):
        final_state = app_module.public_prewarm_state()
        if final_state.get("status") == "succeeded":
            break
        time.sleep(0.02)

    assert final_state["status"] == "succeeded"
    assert final_state["total"] == 1
    assert final_state["success_count"] == 1
    assert any("正在预热测试缓存" in event["message"] for event in final_state["events"])
