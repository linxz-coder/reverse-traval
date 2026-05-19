import threading
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


def test_coverage_job_signature_ignores_base_choice_changes():
    first = app_module.canonical_job_signature(
        "coverage",
        {
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "choices": [{"hotel_id": "base-1", "room_type": "king"}],
        },
    )
    second = app_module.canonical_job_signature(
        "coverage",
        {
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "choices": [{"hotel_id": "base-2", "room_type": "twin"}],
        },
    )

    assert first == second


def test_api_errors_return_json():
    client = flask_app.test_client()

    response = client.get("/api/not-found")

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"]


def test_background_search_job_returns_result(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    def fake_cached_choices(**kwargs):
        return None

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

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
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


def test_background_search_job_exception_marks_failed(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    monkeypatch.setattr(app_module.finder, "find_cached_choices", lambda **_kwargs: None)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_choices", lambda **_kwargs: None)

    def fake_find_choices(**_kwargs):
        raise RuntimeError("浏览器连接断开")

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
    poll_url = response.get_json()["poll_url"]
    data = None
    for _ in range(50):
        poll_response = client.get(poll_url)
        assert poll_response.is_json
        data = poll_response.get_json()
        if data["status"] == "failed":
            break
        time.sleep(0.02)

    assert data["status"] == "failed"
    assert "查询中断" in data["error"]
    assert "浏览器连接断开" in data["error"]


def test_slow_running_job_reports_warning_without_stopping():
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()
        job_id = "stalled-job"
        signature = "stalled-signature"
        app_module.jobs[job_id] = {
            "job_id": job_id,
            "kind": "search",
            "signature": signature,
            "status": "running",
            "created_at": app_module.utc_timestamp(),
            "updated_at": app_module.utc_timestamp(),
            "created_ts": time.time() - app_module.JOB_PROGRESS_WARNING_SECONDS - 10,
            "updated_ts": time.time() - app_module.JOB_PROGRESS_WARNING_SECONDS - 10,
            "payload": {},
            "result": None,
            "partial_result": None,
            "progress": {"stage": "running", "message": "正在查询"},
            "progress_events": [],
            "error": "",
            "status_code": None,
        }
        app_module.job_signature_index[signature] = job_id

    client = flask_app.test_client()
    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "running"
    assert data["progress_warning"]["stage"] == "slow_progress"
    assert "后台仍在查询" in data["progress_warning"]["message"]
    assert app_module.job_signature_index[signature] == job_id


def test_background_search_start_reuses_running_same_condition(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_cached_choices(**kwargs):
        return None

    def fake_find_choices(**kwargs):
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
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
            "choices": [{"hotel_id": "1", "hotel_name": "复用任务酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    client = flask_app.test_client()
    payload = {
        "city": "深圳",
        "holiday_code": "2026-06-19::端午节",
        "min_price": "",
        "max_price": "",
        "advanced_filter": "yes",
        "pool_filter": "yes",
        "child_facility_filter": "all",
        "use_cache": "true",
    }

    first = client.post("/api/search/start", json=payload)
    assert first.status_code == 202
    assert started.wait(timeout=2)
    second = client.post("/api/search/start", json=payload)
    second_data = second.get_json()
    assert second.status_code == 202
    assert second_data["job_id"] == first.get_json()["job_id"]
    assert second_data["reused"] is True

    release.set()
    final = None
    for _ in range(50):
        poll_response = client.get(second_data["poll_url"])
        final = poll_response.get_json()
        if final["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert final["status"] == "succeeded"
    assert calls == 1


def test_background_search_start_returns_cached_result_immediately(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    def fake_cached_choices(**kwargs):
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
            "choices": [{"hotel_id": "1", "hotel_name": "缓存酒店"}],
            "cache": {"source": "memory", "hit": True},
        }

    def fake_find_choices(**kwargs):
        raise AssertionError("cached start should not run live search")

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
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
            "use_cache": "true",
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "succeeded"
    assert data["cache_hit"] is True
    assert data["result"]["choices"][0]["hotel_name"] == "缓存酒店"
    assert client.get(data["poll_url"]).get_json()["status"] == "succeeded"

    reused_response = client.post(
        "/api/search/start",
        json={
            "city": "广州",
            "holiday_code": "2026-06-19::端午节",
            "min_price": "",
            "max_price": "",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "use_cache": "true",
        },
    )
    reused_data = reused_response.get_json()
    assert reused_data["reused"] is False
    assert reused_data["cache_hit"] is True


def test_background_search_partial_result_is_price_filtered(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    started = threading.Event()
    release = threading.Event()

    def fake_cached_choices(**kwargs):
        return None

    def fake_find_choices(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback(
                {
                    "stage": "pricing_preview",
                    "message": "先展示部分结果",
                    "percent": 60,
                    "partial_result": {
                        "city": kwargs["city"],
                        "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
                        "price_filter": {"min_price": None, "max_price": None},
                        "feature_filters": {},
                        "comparison_windows": [],
                        "area_recommendations": [],
                        "choices": [
                            {
                                "hotel_id": "1",
                                "hotel_name": "价格内酒店",
                                "area_name": "测试片区",
                                "holiday_avg_nightly_tax_total_value": 700,
                                "holiday_avg_nightly_tax_total_price": "CNY 700",
                                "price_diff_nightly": -10,
                                "price_diff_nightly_text": "CNY -10",
                                "room_type_label": "大床房",
                            },
                            {
                                "hotel_id": "2",
                                "hotel_name": "价格外酒店",
                                "area_name": "测试片区",
                                "holiday_avg_nightly_tax_total_value": 900,
                                "holiday_avg_nightly_tax_total_price": "CNY 900",
                                "price_diff_nightly": -20,
                                "price_diff_nightly_text": "CNY -20",
                                "room_type_label": "双床房",
                            },
                        ],
                    },
                }
            )
        started.set()
        release.wait(timeout=2)
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "1", "hotel_name": "价格内酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/search/start",
        json={
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "min_price": "600",
            "max_price": "800",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "use_cache": "true",
        },
    )
    assert response.status_code == 202
    start_data = response.get_json()
    assert started.wait(timeout=2)

    partial = None
    for _ in range(50):
        data = client.get(start_data["poll_url"]).get_json()
        partial = data.get("partial_result")
        if partial:
            break
        time.sleep(0.02)

    release.set()
    assert partial["price_filter"] == {"min_price": 600, "max_price": 800}
    assert [item["hotel_name"] for item in partial["choices"]] == ["价格内酒店"]

    for _ in range(50):
        data = client.get(start_data["poll_url"]).get_json()
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert data["status"] == "succeeded"


def test_background_search_start_shows_stale_cache_preview(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    release = threading.Event()

    def fake_cached_choices(**kwargs):
        return None

    def fake_stale_cached_choices(**kwargs):
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "1", "hotel_name": "旧缓存酒店"}],
            "cache": {"source": "stale_disk", "hit": True, "stale": True},
        }

    def fake_find_choices(**kwargs):
        release.wait(timeout=2)
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "2", "hotel_name": "最新酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_choices", fake_stale_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/search/start",
        json={
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "use_cache": "true",
        },
    )

    assert response.status_code == 202
    data = response.get_json()
    assert data["partial_result"]["partial"]["stage"] == "stale_cache_preview"
    assert data["partial_result"]["choices"][0]["hotel_name"] == "旧缓存酒店"

    release.set()
    final = None
    for _ in range(50):
        final = client.get(data["poll_url"]).get_json()
        if final["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert final["result"]["choices"][0]["hotel_name"] == "最新酒店"


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


def test_nearby_response_keeps_all_area_recommendations():
    area_recommendations = [
        {
            "area_name": f"城市测试{i}片区",
            "hotel_count": 1,
            "lower_price_hotel_count": 1,
            "lower_price_ratio": 1,
            "average_price_diff_nightly": -i,
            "average_holiday_nightly_tax_total_value": 500 + i,
        }
        for i in range(1, 13)
    ]

    result = app_module.build_nearby_response(
        origin_city="深圳",
        target_cities=["惠州"],
        holiday_code="2026-06-19::端午节",
        min_price_int=None,
        max_price_int=None,
        feature_filters_response={},
        first_success={"holiday": {"code": "2026-06-19::端午节", "name": "端午节"}},
        city_results=[{"city": "惠州", "choices": [], "area_recommendations": area_recommendations}],
        cache_hits=0,
        live_count=1,
        error_count=0,
    )

    assert len(result["area_recommendations"]) == 12
    assert {item["area_name"] for item in result["area_recommendations"]} == {
        f"城市测试{i}片区" for i in range(1, 13)
    }


def test_frontend_area_panel_has_collapsed_full_area_toggle():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "const AREA_COLLAPSED_LIMIT = 8" in html
    assert 'data-area-toggle="more"' in html
    assert "activeAreaFilter && !visible.some" in html


def test_frontend_area_cards_reuse_current_simplified_hotel_names():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "function displayHotelName(item)" in html
    assert "function cleanRepresentativeHotelNames(names)" in html
    assert "representative_hotels: cleanRepresentativeHotelNames(area.representative_hotels || [])" in html
    assert "const hotelName = displayHotelName(item);" in html


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


def test_hotel_name_refresh_job_returns_simplified_choices(monkeypatch):
    def fake_enhance_hotel_name_data(city, choices):
        return {
            "city": city,
            "choices": [{**choices[0], "hotel_name": "深圳光明虹桥希尔顿花园酒店"}],
            "hotel_name_refresh": {"status": "succeeded", "source": "domestic", "domestic_hits": 1},
        }

    monkeypatch.setattr(app_module.finder, "enhance_hotel_name_data", fake_enhance_hotel_name_data)
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-names/start",
        json={"city": "深圳", "choices": [{"hotel_name": "深圳光明虹橋希爾頓花園酒店"}]},
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
    assert data["result"]["choices"][0]["hotel_name"] == "深圳光明虹桥希尔顿花园酒店"


def test_coverage_refresh_job_reports_partial_result(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    def fake_supplement_coverage_choices(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        partial = {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [{"area_name": "北京朝阳片区"}],
            "choices": [
                *kwargs["choices"],
                {"hotel_id": "2", "hotel_name": "北京朝阳补充酒店", "room_type": "king"},
            ],
            "coverage_supplement": {"status": "running", "message": "已补充朝阳区", "completed": 1, "total": 2},
        }
        if progress_callback:
            progress_callback({
                "stage": "coverage_preview",
                "message": "已补充朝阳区",
                "percent": 55,
                "partial_result": partial,
            })
        return {
            **partial,
            "coverage_supplement": {"status": "succeeded", "message": "行政区补充完成", "completed": 2, "total": 2},
        }

    monkeypatch.setattr(app_module.finder, "supplement_coverage_choices", fake_supplement_coverage_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/coverage/start",
        json={
            "city": "北京",
            "holiday_code": "2026-06-19::端午节",
            "choices": [{"hotel_id": "1", "hotel_name": "北京已有酒店", "room_type": "king"}],
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
        },
    )

    assert response.status_code == 202
    poll_url = response.get_json()["poll_url"]
    data = None
    saw_partial = False
    for _ in range(50):
        poll_response = client.get(poll_url)
        assert poll_response.is_json
        data = poll_response.get_json()
        partial = data.get("partial_result") or {}
        if any(choice.get("hotel_id") == "2" for choice in partial.get("choices") or []):
            saw_partial = True
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert saw_partial
    assert data["status"] == "succeeded"
    assert data["result"]["coverage_supplement"]["status"] == "succeeded"
    assert any(choice["hotel_name"] == "北京朝阳补充酒店" for choice in data["result"]["choices"])


def test_coverage_start_returns_stale_preview_while_refreshing(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    stale_result = {
        "city": "深圳",
        "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
        "price_filter": {"min_price": None, "max_price": None},
        "feature_filters": {},
        "comparison_windows": [],
        "area_recommendations": [{"area_name": "光明虹桥公园片区"}],
        "choices": [
            {"hotel_id": "1", "hotel_name": "深圳已有酒店", "room_type": "king"},
            {"hotel_id": "old", "hotel_name": "深圳旧补充酒店", "room_type": "king"},
        ],
        "coverage_supplement": {
            "status": "refreshing",
            "message": "已先显示旧缓存",
            "cache": {"source": "stale_disk", "hit": True, "stale": True},
        },
    }
    final_result = {
        **stale_result,
        "choices": [
            {"hotel_id": "1", "hotel_name": "深圳已有酒店", "room_type": "king"},
            {"hotel_id": "new", "hotel_name": "深圳新补充酒店", "room_type": "king"},
        ],
        "coverage_supplement": {"status": "succeeded", "message": "行政区补充完成"},
    }

    monkeypatch.setattr(app_module.finder, "find_cached_coverage_choices", lambda **_kwargs: None)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_coverage_choices", lambda **_kwargs: stale_result)
    monkeypatch.setattr(app_module.finder, "supplement_coverage_choices", lambda **_kwargs: final_result)
    client = flask_app.test_client()

    response = client.post(
        "/api/coverage/start",
        json={
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "choices": [{"hotel_id": "1", "hotel_name": "深圳已有酒店", "room_type": "king"}],
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
        },
    )

    assert response.status_code == 202
    started = response.get_json()
    assert started["partial_result"]["coverage_supplement"]["status"] == "refreshing"
    assert any(choice["hotel_id"] == "old" for choice in started["partial_result"]["choices"])

    data = None
    for _ in range(50):
        poll_response = client.get(started["poll_url"])
        assert poll_response.is_json
        data = poll_response.get_json()
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert data["status"] == "succeeded"
    assert any(choice["hotel_id"] == "new" for choice in data["result"]["choices"])


def test_coverage_refresh_uses_partial_cache_choices_as_base(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    partial_result = {
        "city": "深圳",
        "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
        "price_filter": {"min_price": None, "max_price": None},
        "feature_filters": {},
        "comparison_windows": [],
        "area_recommendations": [{"area_name": "光明虹桥公园片区"}],
        "choices": [
            {"hotel_id": "base", "hotel_name": "深圳基础酒店", "room_type": "king"},
            {"hotel_id": "partial", "hotel_name": "深圳部分缓存酒店", "room_type": "king"},
        ],
        "coverage_supplement": {
            "status": "refreshing",
            "message": "已先显示部分缓存",
            "cache": {"source": "stale_disk", "hit": True, "stale": True, "partial": True},
        },
    }
    captured_choices = []

    def fake_supplement_coverage_choices(**kwargs):
        captured_choices.extend(kwargs["choices"])
        return {
            **partial_result,
            "choices": [
                *kwargs["choices"],
                {"hotel_id": "new", "hotel_name": "深圳新补充酒店", "room_type": "king"},
            ],
            "coverage_supplement": {"status": "succeeded", "message": "行政区补充完成"},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_coverage_choices", lambda **_kwargs: None)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_coverage_choices", lambda **_kwargs: partial_result)
    monkeypatch.setattr(app_module.finder, "supplement_coverage_choices", fake_supplement_coverage_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/coverage/start",
        json={
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "choices": [{"hotel_id": "base", "hotel_name": "深圳基础酒店", "room_type": "king"}],
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
        },
    )

    assert response.status_code == 202
    started = response.get_json()
    assert any(choice["hotel_id"] == "partial" for choice in started["partial_result"]["choices"])

    data = None
    for _ in range(50):
        poll_response = client.get(started["poll_url"])
        data = poll_response.get_json()
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert data["status"] == "succeeded"
    assert {choice["hotel_id"] for choice in captured_choices} == {"base", "partial"}


def test_coverage_refresh_job_passes_cache_bypass(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    captured = {}

    def unexpected_cache_lookup(**_kwargs):
        raise AssertionError("coverage cache should not be read when use_cache=false")

    def fake_supplement_coverage_choices(**kwargs):
        captured["use_cache"] = kwargs.get("use_cache")
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": kwargs["choices"],
            "coverage_supplement": {"status": "skipped", "message": "当前结果已覆盖主要行政区。"},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_coverage_choices", unexpected_cache_lookup)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_coverage_choices", unexpected_cache_lookup)
    monkeypatch.setattr(app_module.finder, "supplement_coverage_choices", fake_supplement_coverage_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/coverage/start",
        json={
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "choices": [{"hotel_id": "base", "hotel_name": "深圳基础酒店", "room_type": "king"}],
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "use_cache": "false",
        },
    )

    assert response.status_code == 202
    started = response.get_json()
    data = None
    for _ in range(50):
        poll_response = client.get(started["poll_url"])
        data = poll_response.get_json()
        if data["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert data["status"] == "succeeded"
    assert captured["use_cache"] is False


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


def test_cache_prewarm_can_include_coverage_supplement(monkeypatch):
    coverage_calls = 0

    def fake_find_choices(**kwargs):
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": None, "max_price": None},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "1", "hotel_name": "预热酒店"}],
            "cache": {"source": "disk", "hit": True},
        }

    def fake_supplement_coverage_choices(**kwargs):
        nonlocal coverage_calls
        coverage_calls += 1
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({"stage": "coverage", "message": "正在预热行政区补充缓存", "percent": 60})
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": None, "max_price": None},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [
                {"hotel_id": "1", "hotel_name": "预热酒店"},
                {"hotel_id": "2", "hotel_name": "补充酒店"},
            ],
            "coverage_supplement": {
                "status": "succeeded",
                "message": "补充完成",
                "cache": {"source": "live", "hit": False},
            },
        }

    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    monkeypatch.setattr(app_module.finder, "supplement_coverage_choices", fake_supplement_coverage_choices)
    with app_module.prewarm_lock:
        app_module.prewarm_state.clear()
        app_module.prewarm_state.update({"status": "idle", "message": "测试前空闲"})

    state, status_code = app_module.start_cache_prewarm(
        {
            "cities": ["深圳"],
            "holiday_codes": ["2026-06-19::端午节"],
            "profiles": ["default"],
            "delay_seconds": "0",
            "include_coverage": True,
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
    assert coverage_calls == 1
    assert final_state["coverage_success_count"] == 1
    assert final_state["coverage_live_count"] == 1
    assert any("行政区补充缓存" in event["message"] for event in final_state["events"])


def test_cache_prewarm_respects_runtime_window(monkeypatch):
    calls = 0

    def fake_find_choices(**kwargs):
        nonlocal calls
        calls += 1
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": None, "max_price": None},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    with app_module.prewarm_lock:
        app_module.prewarm_state.clear()
        app_module.prewarm_state.update({"status": "idle", "message": "测试前空闲"})

    state, status_code = app_module.start_cache_prewarm(
        {
            "cities": ["深圳"],
            "holiday_codes": ["2026-06-19::端午节"],
            "profiles": ["default"],
            "delay_seconds": "0",
            "max_runtime_seconds": "0",
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
    assert final_state["completed"] == 0
    assert final_state["skipped_count"] == 1
    assert calls == 0
