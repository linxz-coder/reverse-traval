import time

import app as app_module
from app import app as flask_app
from app import nearest_supported_city, nearby_cities_for, normalize_city, parse_bool


def test_nearby_city_helpers_resolve_manual_and_location():
    assert normalize_city("廣州") == "广州"
    assert nearest_supported_city(22.54, 114.05) == "深圳"
    assert nearby_cities_for("深圳", limit=4) == ["汕尾", "惠州", "广州", "东莞"]
    assert nearby_cities_for("珠海", limit=2) == ["中山", "江门"]


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
    assert data["result"]["city"] == "广州"
    assert data["result"]["choices"][0]["hotel_name"] == "测试酒店"
