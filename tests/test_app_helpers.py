import json
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


def test_background_search_jobs_are_isolated_by_client_id(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    release = threading.Event()
    started_all = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def fake_cached_choices(**kwargs):
        return None

    def fake_stale_cached_choices(**kwargs):
        return None

    def fake_find_choices(**kwargs):
        nonlocal calls
        with call_lock:
            calls += 1
            if calls == 2:
                started_all.set()
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
            "choices": [{"hotel_id": str(calls), "hotel_name": "隔离任务酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_choices", fake_stale_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    client = flask_app.test_client()
    payload = {
        "city": "深圳",
        "holiday_code": "2026-06-19::端午节",
        "advanced_filter": "all",
        "pool_filter": "all",
        "child_facility_filter": "all",
        "use_cache": "true",
    }

    first = client.post("/api/search/start", json={**payload, "client_id": "client-a"})
    second = client.post("/api/search/start", json={**payload, "client_id": "client-b"})
    first_data = first.get_json()
    second_data = second.get_json()

    assert first.status_code == 202
    assert second.status_code == 202
    assert second_data["job_id"] != first_data["job_id"]
    assert second_data["reused"] is False
    assert "client_id=client-a" in first_data["poll_url"]
    assert "client_id=client-b" in second_data["poll_url"]
    assert started_all.wait(timeout=2)

    release.set()
    for poll_url in (first_data["poll_url"], second_data["poll_url"]):
        final = None
        for _ in range(50):
            final = client.get(poll_url).get_json()
            if final["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert final["status"] == "succeeded"
    assert calls == 2


def test_job_poll_rejects_wrong_client_id(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    release = threading.Event()

    def fake_cached_choices(**kwargs):
        return None

    def fake_stale_cached_choices(**kwargs):
        return None

    def fake_find_choices(**kwargs):
        release.wait(timeout=2)
        return {
            "city": kwargs["city"],
            "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
            "price_filter": {"min_price": kwargs["min_price"], "max_price": kwargs["max_price"]},
            "feature_filters": {},
            "comparison_windows": [],
            "area_recommendations": [],
            "choices": [{"hotel_id": "1", "hotel_name": "受保护任务酒店"}],
            "cache": {"source": "live", "hit": False},
        }

    monkeypatch.setattr(app_module.finder, "find_cached_choices", fake_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_stale_cached_choices", fake_stale_cached_choices)
    monkeypatch.setattr(app_module.finder, "find_choices", fake_find_choices)
    client = flask_app.test_client()

    response = client.post(
        "/api/search/start",
        json={
            "city": "广州",
            "holiday_code": "2026-06-19::端午节",
            "advanced_filter": "all",
            "pool_filter": "all",
            "child_facility_filter": "all",
            "client_id": "client-a",
        },
    )
    start_data = response.get_json()

    wrong = client.get(f"/api/jobs/{start_data['job_id']}?client_id=client-b")
    assert wrong.status_code == 404
    assert wrong.get_json()["error"] == "查询任务不存在或已过期"

    release.set()
    final = None
    for _ in range(50):
        final_response = client.get(start_data["poll_url"])
        assert final_response.status_code == 200
        final = final_response.get_json()
        if final["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert final["status"] == "succeeded"


def test_job_poll_since_version_omits_unchanged_payload(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    started = threading.Event()
    release = threading.Event()

    def fake_cached_choices(**kwargs):
        return None

    def fake_stale_cached_choices(**kwargs):
        return None

    def fake_find_choices(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback(
                {
                    "stage": "preview",
                    "message": "先展示一批结果",
                    "percent": 40,
                    "partial_result": {
                        "city": kwargs["city"],
                        "holiday": {"code": kwargs["holiday_code"], "name": "端午节"},
                        "price_filter": {"min_price": None, "max_price": None},
                        "feature_filters": {},
                        "comparison_windows": [],
                        "area_recommendations": [],
                        "choices": [{"hotel_id": "1", "hotel_name": "增量酒店"}],
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
            "choices": [{"hotel_id": "1", "hotel_name": "增量酒店"}],
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
            "client_id": "client-a",
        },
    )
    start_data = response.get_json()
    assert started.wait(timeout=2)

    first_poll = client.get(start_data["poll_url"]).get_json()
    assert first_poll["version"] > start_data["version"]
    assert first_poll["partial_result"]["choices"][0]["hotel_name"] == "增量酒店"

    unchanged = client.get(f"{start_data['poll_url']}&since_version={first_poll['version']}").get_json()
    assert unchanged["unchanged"] is True
    assert unchanged["version"] == first_poll["version"]
    assert "partial_result" not in unchanged
    assert "result" not in unchanged

    release.set()
    final = None
    for _ in range(50):
        final = client.get(f"{start_data['poll_url']}&since_version={first_poll['version']}").get_json()
        if final["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert final["status"] == "succeeded"
    assert final["version"] > first_poll["version"]
    assert "result" not in final
    assert final["result_delta"]["choice_order"]
    assert final["result_delta"]["meta"]["cache"]["source"] == "live"


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


def test_mysql_price_preview_builds_preliminary_result(monkeypatch):
    captured = {}

    class FakeStore:
        def latest_price_preview(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "hotel_id": "mysql-1",
                    "hotel_name": "缓存中文酒店",
                    "area_name": "缓存片区",
                    "room_type": "king",
                    "room_type_label": "大床房",
                    "holiday_avg_nightly_tax_total_value": 500,
                    "holiday_avg_nightly_tax_total_price": "CNY 500",
                    "price_diff_nightly": 20,
                    "price_diff_nightly_text": "+CNY 20",
                    "detail_url": "https://www.trip.com/hotels/detail/?hotelId=mysql-1",
                }
            ]

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())

    result = app_module.mysql_price_preview_for_search_payload(
        {
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "advanced_filter": "yes",
            "pool_filter": "all",
            "child_facility_filter": "yes",
            "min_price": "400",
            "max_price": "800",
            "use_cache": "true",
        }
    )

    assert result is not None
    assert result["partial"]["stage"] == "mysql_price_preview"
    assert result["choices"][0]["hotel_name"] == "缓存中文酒店"
    assert result["cache"]["source"] == "mysql_price"
    assert captured["advanced_filter"] == "yes"
    assert captured["child_facility_filter"] == "yes"
    assert captured["min_price"] == 400
    assert captured["max_price"] == 800


def test_mysql_price_preview_sanitizes_bad_cached_hotel_name(monkeypatch):
    class FakeStore:
        def latest_price_preview(self, **kwargs):
            return [
                {
                    "hotel_id": "mysql-1",
                    "hotel_name": "深圳",
                    "hotel_original_name": "Hampton by Hilton Shenzhen Guangming",
                    "hotel_name_source": "MySQL价格缓存",
                    "room_type": "king",
                    "room_type_label": "大床房",
                    "holiday_avg_nightly_tax_total_value": 500,
                    "holiday_avg_nightly_tax_total_price": "CNY 500",
                    "price_diff_nightly": 20,
                    "price_diff_nightly_text": "+CNY 20",
                    "detail_url": "https://www.trip.com/hotels/detail/?hotelId=mysql-1",
                }
            ]

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())

    result = app_module.mysql_price_preview_for_search_payload(
        {
            "city": "深圳",
            "holiday_code": "2026-06-19::端午节",
            "use_cache": "true",
        }
    )

    assert result is not None
    choice = result["choices"][0]
    assert choice["hotel_name"] == "Hampton by Hilton Shenzhen Guangming"
    assert choice["hotel_name_source"] == ""
    assert choice["hotel_name_needs_refresh"] is True


def test_hotel_name_correction_submission_queues_review(monkeypatch):
    captured = {}

    class FakeStore:
        def submit_hotel_name_correction(self, payload):
            captured.update(payload)
            return {"ok": True, "id": 7, "status": "pending"}

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-name-corrections",
        json={
            "hotel_id": "777",
            "city": "深圳",
            "current_name": "Hampton by Hilton Shenzhen Guangming",
            "hotel_original_name": "Hampton by Hilton Shenzhen Guangming",
            "suggested_name": "深圳光明希爾頓歡朋酒店",
            "area_name": "光明区",
            "detail_url": "https://www.trip.com/hotels/detail/?hotelId=777",
            "client_id": "client-1",
        },
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "pending"
    assert captured["hotel_id"] == "777"
    assert captured["suggested_hotel_name_zh"] == "深圳光明希尔顿欢朋酒店"
    assert captured["client_id"] == "client-1"


def test_hotel_name_correction_rejects_city_only_name():
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-name-corrections",
        json={"hotel_id": "777", "city": "深圳", "current_name": "Hampton", "suggested_name": "深圳"},
    )

    assert response.status_code == 400
    assert "太短" in response.get_json()["error"]


def test_admin_approves_hotel_name_correction_and_refreshes_cache(monkeypatch):
    captured = {}

    class FakeStore:
        def review_hotel_name_correction(self, correction_id, action, reviewer_note=""):
            captured["review"] = (correction_id, action, reviewer_note)
            return {
                "ok": True,
                "status": "approved",
                "correction": {
                    "id": correction_id,
                    "hotel_id": "777",
                    "suggested_hotel_name_zh": "深圳光明希尔顿欢朋酒店",
                },
            }

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    monkeypatch.setattr(app_module, "cache_approved_hotel_name", lambda correction: captured.setdefault("cache", correction))
    client = flask_app.test_client()

    response = client.post(
        "/api/admin/hotel-name-corrections/7/review",
        json={"action": "approve", "reviewer_note": "ok"},
    )

    assert response.status_code == 200
    assert captured["review"] == (7, "approve", "ok")
    assert captured["cache"]["hotel_id"] == "777"


def test_approved_hotel_name_corrections_api_returns_public_records(monkeypatch):
    captured = {}

    class FakeStore:
        def approved_hotel_name_records(self, hotel_ids):
            captured["hotel_ids"] = hotel_ids
            return {
                "777": {
                    "hotel_name": "深圳光明希尔顿欢朋酒店",
                    "hotel_name_original": "Hampton by Hilton Shenzhen Guangming",
                    "review_id": 7,
                    "detail_url": "https://hidden.example",
                },
            }

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-name-corrections/approved",
        json={"choices": [{"hotel_id": "777"}, {"trip_hotel_id": "888"}]},
    )

    assert response.status_code == 200
    assert captured["hotel_ids"] == ["777", "888"]
    data = response.get_json()
    assert data["records"] == [
        {
            "hotel_id": "777",
            "hotel_name": "深圳光明希尔顿欢朋酒店",
            "hotel_name_original": "Hampton by Hilton Shenzhen Guangming",
            "review_id": 7,
        }
    ]


def test_cache_approved_hotel_name_preserves_approved_area(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.finder, "cache_dir", tmp_path)
    monkeypatch.setattr(
        app_module.finder,
        "_hotel_name_cache",
        {
            "777": {
                "hotel_name": "旧酒店名",
                "area_name": "深圳福田中心片区",
                "area_source": "人工审核片区",
            }
        },
    )

    app_module.cache_approved_hotel_name(
        {
            "hotel_id": "777",
            "suggested_hotel_name_zh": "深圳光明希尔顿欢朋酒店",
            "hotel_name_original": "Hampton by Hilton Shenzhen Guangming",
        }
    )

    cached = app_module.finder._hotel_name_cache["777"]
    assert cached["hotel_name"] == "深圳光明希尔顿欢朋酒店"
    assert cached["source"] == "人工审核中文名"
    assert cached["area_name"] == "深圳福田中心片区"
    assert cached["area_source"] == "人工审核片区"


def test_hotel_area_correction_submission_queues_review(monkeypatch):
    captured = {}

    class FakeStore:
        def submit_hotel_area_correction(self, payload):
            captured.update(payload)
            return {"ok": True, "id": 8, "status": "pending"}

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-area-corrections",
        json={
            "hotel_id": "777",
            "city": "深圳",
            "hotel_name": "深圳光明希尔顿欢朋酒店",
            "hotel_original_name": "Hampton by Hilton Shenzhen Guangming",
            "current_area_name": "南山科技园",
            "suggested_area_name": "光明区",
            "detail_url": "https://www.trip.com/hotels/detail/?hotelId=777",
            "client_id": "client-1",
        },
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "pending"
    assert captured["hotel_id"] == "777"
    assert captured["suggested_area_name_zh"] == "光明区片区"
    assert captured["current_area_name_zh"] == "南山科技园片区"
    assert captured["client_id"] == "client-1"


def test_approved_hotel_area_corrections_api_returns_public_records(monkeypatch):
    captured = {}

    class FakeStore:
        def approved_hotel_area_records(self, hotel_ids):
            captured["hotel_ids"] = hotel_ids
            return {
                "777": {"area_name": "深圳福田中心片区", "review_id": 8, "detail_url": "https://hidden.example"},
            }

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-area-corrections/approved",
        json={"choices": [{"hotel_id": "777"}, {"trip_hotel_id": "888"}]},
    )

    assert response.status_code == 200
    assert captured["hotel_ids"] == ["777", "888"]
    data = response.get_json()
    assert data["records"] == [{"hotel_id": "777", "area_name": "深圳福田中心片区", "review_id": 8}]


def test_hotel_area_correction_rejects_generic_area():
    client = flask_app.test_client()

    response = client.post(
        "/api/hotel-area-corrections",
        json={
            "hotel_id": "777",
            "city": "深圳",
            "hotel_name": "深圳光明希尔顿欢朋酒店",
            "suggested_area_name": "热门酒店片区",
        },
    )

    assert response.status_code == 400
    assert "具体" in response.get_json()["error"]


def test_admin_approves_hotel_area_correction_and_refreshes_cache(monkeypatch):
    captured = {}

    class FakeStore:
        def review_hotel_area_correction(self, correction_id, action, reviewer_note=""):
            captured["review"] = (correction_id, action, reviewer_note)
            return {
                "ok": True,
                "status": "approved",
                "correction": {
                    "id": correction_id,
                    "hotel_id": "777",
                    "suggested_area_name_zh": "光明区片区",
                },
            }

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    monkeypatch.setattr(app_module, "cache_approved_hotel_area", lambda correction: captured.setdefault("cache", correction))
    client = flask_app.test_client()

    response = client.post(
        "/api/admin/hotel-area-corrections/8/review",
        json={"action": "approve", "reviewer_note": "ok"},
    )

    assert response.status_code == 200
    assert captured["review"] == (8, "approve", "ok")
    assert captured["cache"]["hotel_id"] == "777"


def test_area_merge_correction_submission_queues_review(monkeypatch):
    captured = {}

    class FakeStore:
        def submit_area_merge_correction(self, payload):
            captured.update(payload)
            return {"ok": True, "id": 9, "status": "pending"}

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/area-merge-corrections",
        json={
            "city": "深圳",
            "suggested_area_name": "光明虹桥",
            "source_areas": [
                {"area_name": "光明虹桥公园", "recommend_city": "深圳", "hotel_count": 2},
                {"area_name": "光明云谷", "recommend_city": "深圳", "hotel_count": 1},
            ],
            "hotels": [
                {
                    "hotel_id": "777",
                    "city": "深圳",
                    "hotel_name": "深圳光明希尔顿欢朋酒店",
                    "current_area_name": "光明虹桥公园",
                    "detail_url": "https://www.trip.com/hotels/detail/?hotelId=777",
                },
                {
                    "hotel_id": "888",
                    "city": "深圳",
                    "hotel_name": "深圳光明云谷酒店",
                    "current_area_name": "光明云谷",
                },
            ],
            "client_id": "client-1",
        },
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "pending"
    assert captured["suggested_area_name_zh"] == "光明虹桥片区"
    assert [item["area_name"] for item in captured["source_areas"]] == ["光明虹桥公园片区", "光明云谷片区"]
    assert [item["hotel_id"] for item in captured["hotels"]] == ["777", "888"]
    assert captured["client_id"] == "client-1"


def test_area_rename_correction_submission_queues_review(monkeypatch):
    captured = {}

    class FakeStore:
        def submit_area_merge_correction(self, payload):
            captured.update(payload)
            return {"ok": True, "id": 10, "status": "pending"}

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/area-merge-corrections",
        json={
            "city": "佛山",
            "suggested_area_name": "佛山三龙湾",
            "source_areas": [
                {"area_name": "佛山南海", "recommend_city": "佛山", "hotel_count": 2},
            ],
            "hotels": [
                {
                    "hotel_id": "80911801",
                    "city": "佛山",
                    "hotel_name": "佛山三龙湾希尔顿欢朋酒店",
                    "current_area_name": "佛山南海",
                },
            ],
            "client_id": "client-1",
        },
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "pending"
    assert captured["suggested_area_name_zh"] == "佛山三龙湾片区"
    assert [item["area_name"] for item in captured["source_areas"]] == ["佛山南海片区"]
    assert [item["hotel_id"] for item in captured["hotels"]] == ["80911801"]


def test_active_area_merge_corrections_api_returns_city_records(monkeypatch):
    captured = {}

    class FakeStore:
        def active_area_merge_corrections(self, city_names, limit):
            captured["args"] = (city_names, limit)
            return [
                {
                    "id": 12,
                    "status": "pending",
                    "city_name_zh": "深圳",
                    "suggested_area_name_zh": "东门老街片区",
                    "source_area_names": ["东门步行街片区", "老街地铁站片区"],
                    "source_areas": [],
                    "hotels": [],
                    "hotel_count": 4,
                }
            ]

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.get("/api/area-merge-corrections/active?city=深圳&city=惠州")

    assert response.status_code == 200
    assert captured["args"] == (["深圳", "惠州"], 120)
    assert response.get_json()["corrections"][0]["suggested_area_name_zh"] == "东门老街片区"


def test_area_merge_correction_rejects_cross_city_selection():
    client = flask_app.test_client()

    response = client.post(
        "/api/area-merge-corrections",
        json={
            "suggested_area_name": "湾区度假片区",
            "source_areas": [
                {"area_name": "光明虹桥公园", "recommend_city": "深圳"},
                {"area_name": "双月湾", "recommend_city": "惠州"},
            ],
            "hotels": [
                {"hotel_id": "777", "city": "深圳", "current_area_name": "光明虹桥公园"},
                {"hotel_id": "888", "city": "惠州", "current_area_name": "双月湾"},
            ],
        },
    )

    assert response.status_code == 400
    assert "同一个推荐城市" in response.get_json()["error"]


def test_admin_approves_area_merge_correction_and_refreshes_cache(monkeypatch):
    captured = {}

    class FakeStore:
        def review_area_merge_correction(self, correction_id, action, reviewer_note=""):
            captured["review"] = (correction_id, action, reviewer_note)
            return {
                "ok": True,
                "status": "approved",
                "correction": {
                    "id": correction_id,
                    "suggested_area_name_zh": "光明虹桥片区",
                    "hotels": [{"hotel_id": "777"}, {"hotel_id": "888"}],
                },
                "approved_count": 2,
            }

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    monkeypatch.setattr(app_module, "cache_approved_area_merge", lambda correction: captured.setdefault("cache", correction))
    client = flask_app.test_client()

    response = client.post(
        "/api/admin/area-merge-corrections/9/review",
        json={"action": "approve", "reviewer_note": "ok"},
    )

    assert response.status_code == 200
    assert captured["review"] == (9, "approve", "ok")
    assert [item["hotel_id"] for item in captured["cache"]["hotels"]] == ["777", "888"]


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


def test_nearby_search_forwards_city_partial_results(monkeypatch):
    def fake_find_choices(**kwargs):
        city = kwargs["city"]
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback(
                {
                    "stage": "pricing_preview",
                    "message": "先展示部分酒店",
                    "percent": 55,
                    "partial_result": {
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
                        "area_recommendations": [{"area_name": f"{city}预览片区", "hotel_count": 1}],
                        "choices": [
                            {
                                "hotel_id": f"{city}-preview",
                                "hotel_name": f"{city}预览酒店",
                                "holiday_avg_nightly_tax_total_value": 520,
                                "price_diff_nightly": -30,
                            }
                        ],
                    },
                }
            )
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
            "area_recommendations": [{"area_name": f"{city}最终片区", "hotel_count": 1}],
            "choices": [
                {
                    "hotel_id": f"{city}-final",
                    "hotel_name": f"{city}最终酒店",
                    "holiday_avg_nightly_tax_total_value": 500,
                    "price_diff_nightly": -50,
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

    preview_events = [event for event in events if event.get("stage") == "nearby_city_preview"]
    assert status_code == 200
    assert preview_events
    first_preview = preview_events[0]["partial_result"]
    assert first_preview["choices"]
    assert first_preview["choices"][0]["recommend_city"] in {"汕尾", "惠州"}
    assert any(item["hotel_id"].endswith("-final") for item in result["choices"])
    assert not any(item["hotel_id"].endswith("-preview") for item in result["choices"])


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


def test_nearby_response_prioritizes_area_hotel_count_before_discounts():
    result = app_module.build_nearby_response(
        origin_city="深圳",
        target_cities=["惠州"],
        holiday_code="2026-06-19::端午节",
        min_price_int=None,
        max_price_int=None,
        feature_filters_response={},
        first_success={"holiday": {"code": "2026-06-19::端午节", "name": "端午节"}},
        city_results=[
            {
                "city": "惠州",
                "choices": [],
                "area_recommendations": [
                    {
                        "area_name": "惠州优惠更强片区",
                        "hotel_count": 1,
                        "lower_price_hotel_count": 1,
                        "lower_price_ratio": 1,
                        "average_price_diff_nightly": -200,
                        "average_holiday_nightly_tax_total_value": 500,
                    },
                    {
                        "area_name": "惠州酒店更多片区",
                        "hotel_count": 3,
                        "lower_price_hotel_count": 1,
                        "lower_price_ratio": 0.33,
                        "average_price_diff_nightly": -20,
                        "average_holiday_nightly_tax_total_value": 700,
                    },
                    {
                        "area_name": "惠州同数更优惠片区",
                        "hotel_count": 3,
                        "lower_price_hotel_count": 2,
                        "lower_price_ratio": 0.66,
                        "average_price_diff_nightly": -80,
                        "average_holiday_nightly_tax_total_value": 650,
                    },
                ],
            }
        ],
        cache_hits=0,
        live_count=1,
        error_count=0,
    )

    assert [item["area_name"] for item in result["area_recommendations"]] == [
        "惠州同数更优惠片区",
        "惠州酒店更多片区",
        "惠州优惠更强片区",
    ]


def test_frontend_area_panel_uses_fixed_scroll_without_more_toggle():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="area-toggle-btn"' not in html
    assert 'data-area-toggle="more"' not in html
    assert "还有 ${hiddenCount} 个片区" not in html
    assert "已显示 ${areas.length} 个推荐片区" in html
    assert "max-height: min(58vh, 620px)" in html
    assert "function areaAliases(area)" in html
    assert "data-area-aliases" in html
    assert "function sortAreaRecommendations(areas)" in html
    assert "Number(b.hotel_count || 0) - Number(a.hotel_count || 0)" in html
    assert "data-area-merge-checkbox" in html
    assert "function submitAreaMergeCorrection()" in html
    assert "function applyPendingAreaMergePreview(suggestedArea, selected, hotels)" in html
    assert "function applyActiveAreaMergeCorrections()" in html
    assert "function applyApprovedHotelAreaCorrections()" in html
    assert "function loadApprovedHotelAreaCorrections(data = null)" in html
    assert "function refreshApprovedHotelAreasForCurrentResults()" in html
    assert "function loadActiveAreaMergeCorrections(data)" in html
    assert "function pendingAreaMergeInfoForArea(area)" in html
    assert "data-area-merge-pending" in html
    assert "/api/area-merge-corrections/active" in html
    assert "/api/hotel-area-corrections/approved" in html
    assert '"/api/area-merge-corrections"' in html
    assert "合并 ${esc(mergedCount + 1)} 个片区" in html
    assert "当前页已临时预览" in html
    assert "已操作，等待审核" in html
    assert "已审核合并" not in html
    assert "后台已审核通过，当前按最新片区展示" not in html
    assert "activeAreaFilter.areas.has(itemArea)" in html


def test_frontend_includes_floating_page_navigation():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="floating-nav"' in html
    assert 'id="page-top"' in html
    assert 'id="hotel-results-panel"' in html
    assert 'href="#area-panel"' in html
    assert 'href="#hotel-results-panel"' in html
    assert 'href="#daily-recommendation"' in html
    assert 'id="floating-area-count">0</span>' in html
    assert 'id="floating-hotel-count">0</span>' in html
    assert "推荐旅游区域 <span>区</span>" not in html
    assert "推荐酒店区域 <span>酒</span>" not in html
    assert "function setFloatingNavOpen(open)" in html
    assert "function updateFloatingNavCounts({ areaCount, hotelCount } = {})" in html
    assert "updateFloatingNavCounts({ areaCount: areas.length })" in html
    assert "updateFloatingNavCounts({ hotelCount: items.length })" in html
    assert "target.scrollIntoView({ behavior: \"smooth\", block: \"start\" })" in html


def test_frontend_async_jobs_send_client_id():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "const clientId = getClientId()" in html
    assert "window.sessionStorage.setItem(key, value)" in html
    assert "client_id: clientId" in html
    assert '"X-Reverse-Travel-Client": clientId' in html
    assert "fetch(pollUrlWithVersion(pollUrl, jobVersion), { cache: \"no-store\", headers: jobHeaders() })" in html
    assert "delete basePayload.city" in html
    assert "new FormData(nearbyForm)" in html
    assert "if (data.city_results) return" in html


def test_frontend_area_cards_use_refreshed_hotel_names():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "function representativeHotelsForArea(area, items)" in html
    assert "const name = simplifyChineseText(item.hotel_name || item.hotel_original_name || \"\")" in html
    assert "const representativeHotels = representativeHotelsForArea(area, items)" in html


def test_frontend_uses_incremental_polling_and_batched_hotel_names():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "const HOTEL_NAME_BATCH_SIZE = 8" in html
    assert "function pollUrlWithVersion(pollUrl, version)" in html
    assert "since_version" in html
    assert "function mergeResultDelta(base, delta)" in html
    assert "data.result_delta" in html
    assert "function renderChoiceRows(items)" in html
    assert "data-choice-key" in html
    assert "function hotelNameRefreshBatches(choices)" in html
    assert "function hotelNameNeedsRefresh(item)" in html
    assert "function displayHotelName(item)" in html
    assert "function loadApprovedHotelNameCorrections(data = null)" in html
    assert "function applyApprovedHotelNameCorrections()" in html
    assert "function submitHotelNameCorrection(item)" in html
    assert "function submitHotelAreaCorrection(item)" in html
    assert "function submitHotelCorrection(item)" in html
    assert "function openAreaCorrectionDialog(item)" in html
    assert "function areaCorrectionSuggestions(item, query)" in html
    assert "function coordinateDistanceKm(left, right)" in html
    assert 'id="area-correction-dialog"' in html
    assert 'id="hotel-correction-name-input"' in html
    assert "纠正酒店信息" in html
    assert "建议使用已有片区" in html
    assert 'data-action="edit-hotel-correction"' in html
    assert 'aria-label="纠正酒店名称或片区"' in html
    assert 'data-action="suggest-hotel-name"' not in html
    assert 'data-action="suggest-area-name"' not in html
    assert '"/api/hotel-name-corrections/approved"' in html
    assert '"/api/hotel-name-corrections"' in html
    assert '"/api/hotel-area-corrections"' in html
    assert '"/api/area-merge-corrections"' in html
    assert 'data-area-rename' in html
    assert 'aria-label="修改片区名称"' in html
    assert "选择用于合并" not in html
    assert "function submitAreaRenameCorrection(card)" in html
    assert "片区改名申请已提交" in html
    assert "if (item.hotel_name_source) return false" not in html
    assert 'delete existing.hotel_name_needs_refresh' in html
    assert "正在分批匹配简体中文酒店名" in html
    assert "refreshLocalAreaRecommendations()" in html
    assert "mergeAreaLists(lastServerAreas, buildAreasFromChoices(lastChoices))" in html


def test_frontend_hotel_list_has_client_side_sort_and_price_filters():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="result-sort"' in html
    assert 'id="result-min-price"' in html
    assert 'id="result-max-price"' in html
    assert 'id="result-filter-clear"' in html
    assert "价格低到高" in html
    assert "优惠大到小" in html
    assert "function choicePriceValue(item)" in html
    assert "function sortVisibleChoices(items)" in html
    assert "function resultPriceFilterBounds()" in html
    assert "本次结果价格：CNY" in html


def test_frontend_can_export_current_results_to_pdf():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="export-pdf-btn"' in html
    assert "导出 PDF" in html
    assert "function exportCurrentResultsPdf()" in html
    assert "const items = visibleChoices()" in html
    assert "buildPdfExportHtml(items)" in html
    assert "printWindow.print()" in html
    assert "当前没有可导出的酒店结果" in html
    assert "当前前台已显示" in html


def test_admin_dashboard_includes_hotel_name_review_queue():
    client = flask_app.test_client()

    response = client.get("/admin")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<title>反向旅游搜索后台</title>" in html
    assert "<h1>反向旅游搜索后台</h1>" in html
    assert 'rel="icon"' in html
    assert "brand-mark" in html
    assert '"SF Pro Text", "Inter"' in html
    assert "--panel-subtle" in html
    assert ".review-group[open] summary" in html
    assert "审核中心" in html
    assert 'id="review-center-count"' in html
    assert 'class="panel-body review-center-body"' in html
    assert 'class="review-group" id="hotel-name-review-group"' in html
    assert 'class="review-group" id="hotel-area-review-group"' in html
    assert 'class="review-group" id="area-merge-review-group"' in html
    assert "function updateReviewCenterSummary()" in html
    assert "function initializeReviewCenterGroups()" in html
    assert "酒店名审核" in html
    assert "片区审核" in html
    assert "合并片区审核" in html
    assert "<th style=\"width: 96px;\">PDF</th>" in html
    assert "function jobPdfLink(job" in html
    assert "下载 PDF" in html
    assert "pdf_available" in html
    assert 'id="recent-jobs-more"' in html
    assert 'id="recent-job-cards"' in html
    assert "function renderRecentJobCard(job)" in html
    assert "function recentJobPreviewLimit()" in html
    assert "function formatAdminCardDateTime(value)" in html
    assert ".recent-table-wrap { display: none; }" in html
    assert ".recent-job-card-grid { grid-template-columns: 1fr; }" in html
    assert "const previewLimit = recentJobPreviewLimit()" in html
    assert "prewarm-target-details" in html
    assert "prewarm-summary-details" in html
    assert "function renderPrewarmSummaryDetails(items, openState = null)" in html
    assert "function snapshotPrewarmSummaryOpenState()" in html
    assert "function isAdminMobileLayout()" in html
    assert "const summaryOpenState = snapshotPrewarmSummaryOpenState()" in html
    assert "renderPrewarmSummaryDetails([" in html
    assert "function renderPrewarmTargetCard(target, prewarm)" in html
    assert "prewarm-city-list" in html
    assert "prewarm-city-details" in html
    assert "function groupedPrewarmTargetsByCity(prewarm)" in html
    assert "function renderPrewarmCityDetails(group, prewarm, openState = null)" in html
    assert "function snapshotPrewarmCityOpenState()" in html
    assert ".prewarm-target-table," in html
    assert ".review-table-wrap { display: none; }" in html
    assert 'id="hotel-name-review-cards"' in html
    assert 'id="hotel-area-review-cards"' in html
    assert 'id="area-merge-review-cards"' in html
    assert "function renderHotelNameReviewCard(item)" in html
    assert "function renderHotelAreaReviewCard(item)" in html
    assert "function renderAreaMergeReviewCard(item)" in html
    assert "RECENT_JOB_PREVIEW_LIMIT = 5" in html
    assert "function updateRecentJobs(recent)" in html
    assert "function openRecentJobsModal()" in html
    assert "hotel-name-reviews" in html
    assert "hotel-area-reviews" in html
    assert "area-merge-reviews" in html
    assert "/api/admin/hotel-name-corrections" in html
    assert "/api/admin/hotel-area-corrections" in html
    assert "/api/admin/area-merge-corrections" in html
    assert "/api/admin/hotel-name-corrections/batch-review" in html
    assert "/api/admin/hotel-area-corrections/batch-review" in html
    assert "/api/admin/area-merge-corrections/batch-review" in html
    assert "data-review-action" in html
    assert "data-area-review-action" in html
    assert "data-area-merge-review-action" in html
    assert "data-review-batch-section" in html
    assert "全部通过" in html
    assert "全部拒绝" in html
    assert "通过并保存" in html
    assert "review-inline-actions" not in html
    assert "review-action-cell" in html
    assert ".badge.approved" in html
    assert ".badge.rejected" in html
    assert "function formatAdminDateTime(value)" in html
    assert "const hasTimezone" in html
    assert "最后更新 ${formatAdminDateTime(data.generated_at)}" in html
    assert "job.idle_seconds" not in html
    assert "REVIEW_PREVIEW_LIMIT = 3" in html
    assert 'id="review-modal"' in html
    assert 'data-review-more="hotelName"' in html
    assert 'data-review-more="hotelArea"' in html
    assert 'data-review-more="areaMerge"' in html
    assert "function renderPrewarmTargets(prewarm, openState = null, cityOpenState = null)" in html
    assert "target_results" in html
    assert "预热清单" in html
    assert "先按城市展示" in html
    assert "function sortedPrewarmTargets(prewarm)" in html
    assert "function snapshotPrewarmTargetScroll()" in html
    assert "function snapshotPrewarmTargetOpenState()" in html
    assert "restorePrewarmTargetScroll(scrollSnapshot)" in html
    assert "const targetOpenState = snapshotPrewarmTargetOpenState()" in html
    assert "const cityOpenState = snapshotPrewarmCityOpenState()" in html
    assert "renderPrewarmTargets(prewarm, targetOpenState, cityOpenState)" in html
    assert "时段 ${prewarmPeriodText(prewarm)}" in html
    assert "实际新搜索" in html
    assert "安排在半夜的这次缓存预热" in html
    assert "失败城市：" in html
    assert "data-prewarm-failures" in html
    assert "查看原因" in html
    assert "function prewarmFailureCitySummary(failed)" in html
    assert "function renderPrewarmFailureCard(item)" in html
    assert "prewarm-failure-cards" in html
    assert "prewarm-failure-table" in html
    assert "function renderPrewarmFailureModal()" in html
    assert ".prewarm-failure-line" in html
    assert 'item.city || "-")}${item.error' not in html
    assert "events.map" not in html


def test_admin_batch_approves_hotel_area_corrections_and_refreshes_cache(monkeypatch):
    captured = {"reviewed": [], "cached": []}

    class FakeStore:
        def __init__(self):
            self.pending_ids = [8, 9]

        def hotel_area_corrections(self, status, limit):
            assert status == "pending"
            return [{"id": item_id} for item_id in self.pending_ids]

        def review_hotel_area_correction(self, correction_id, action, reviewer_note=""):
            captured["reviewed"].append((correction_id, action, reviewer_note))
            self.pending_ids = [item_id for item_id in self.pending_ids if item_id != correction_id]
            return {
                "ok": True,
                "status": "approved",
                "correction": {
                    "id": correction_id,
                    "hotel_id": str(correction_id),
                    "suggested_area_name_zh": "深圳福田中心片区",
                },
            }

    fake_store = FakeStore()
    monkeypatch.setattr(app_module, "get_mysql_store", lambda: fake_store)
    monkeypatch.setattr(app_module, "cache_approved_hotel_area", lambda correction: captured["cached"].append(correction))
    client = flask_app.test_client()

    response = client.post(
        "/api/admin/hotel-area-corrections/batch-review",
        json={"action": "approve", "reviewer_note": "batch"},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["reviewed_count"] == 2
    assert data["approved_count"] == 2
    assert captured["reviewed"] == [(8, "approve", "batch"), (9, "approve", "batch")]
    assert [item["hotel_id"] for item in captured["cached"]] == ["8", "9"]


def test_admin_batch_rejects_hotel_name_corrections(monkeypatch):
    captured = []

    class FakeStore:
        def __init__(self):
            self.pending_ids = [7]

        def hotel_name_corrections(self, status, limit):
            assert status == "pending"
            return [{"id": item_id} for item_id in self.pending_ids]

        def review_hotel_name_correction(self, correction_id, action, reviewer_note=""):
            captured.append((correction_id, action, reviewer_note))
            self.pending_ids = []
            return {"ok": True, "status": "rejected", "correction": {"id": correction_id}}

    monkeypatch.setattr(app_module, "get_mysql_store", lambda: FakeStore())
    client = flask_app.test_client()

    response = client.post(
        "/api/admin/hotel-name-corrections/batch-review",
        json={"action": "reject", "reviewer_note": "bad"},
    )

    assert response.status_code == 200
    assert response.get_json()["rejected_count"] == 1
    assert captured == [(7, "reject", "bad")]


def test_frontend_loads_daily_recommendation_and_stage_labels():
    client = flask_app.test_client()

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="daily-recommendation"' in html
    assert "/api/daily-recommendation" in html
    assert "function renderDailyRecommendation(data)" in html
    assert "function stageLabel(stage)" in html
    assert "mysql_price_preview: \"价格缓存\"" in html
    assert "function choiceStageLabel(item)" in html
    assert "function isCompleteCoverageResult(data)" in html
    assert "if (isCompleteCoverageResult(data)) return" in html
    assert "result-stage-pill" in html
    assert "阶段：${stageLabel(lastResultStage)}" in html
    assert "desktop-detail" in html
    assert "hotel-detail-link" in html
    assert "打开 Trip.com 详情</a></div>" in html
    assert "grid-template-columns: 1fr 1fr" in html
    assert "scheduleRenderChoices(80)" in html
    assert "stage-pill" in html
    assert "daily-photo" in html
    assert "酒店照片待更新" in html
    assert "daily-feature-row" in html
    assert "function featureLabel" in html


def test_daily_recommendation_reads_cached_search_records(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    search_dir = cache_dir / "search"
    search_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module.finder, "cache_dir", cache_dir)
    record = {
        "cache_key": ["search", "深圳", "2026-06-19::端午节"],
        "created_at": time.time(),
        "result": {
            "city": "深圳",
            "holiday": {
                "code": "2026-06-19::端午节",
                "name": "端午节",
                "check_in": "2026-06-19",
                "check_out": "2026-06-21",
            },
            "feature_filters": {},
            "choices": [
                {
                    "hotel_id": "hotel-1",
                    "hotel_name": "深圳每日推荐酒店",
                    "detail_url": "https://example.test/hotel-1",
                    "image_url": "https://images.example.test/hotel-1.jpg",
                    "area_name": "南山科技园",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": True,
                    "room_type_label": "高级大床房",
                    "holiday_avg_nightly_tax_total_value": 520,
                    "holiday_avg_nightly_tax_total_price": "CNY 520",
                    "holiday_tax_total_price": "CNY 1040",
                    "comparison_average_nightly_tax_total_price": "CNY 610",
                    "comparison_lowest_nightly_tax_total_price": "CNY 590",
                    "comparison_lowest_check_in": "2026-06-22",
                    "comparison_lowest_check_out": "2026-06-24",
                    "comparison_sample_count": 3,
                    "price_diff_nightly": -90,
                    "price_diff_nightly_text": "CNY -90",
                }
            ],
        },
    }
    (search_dir / "daily.json").write_text(json.dumps(record), encoding="utf-8")
    client = flask_app.test_client()

    response = client.get("/api/daily-recommendation")
    data = response.get_json()

    assert response.status_code == 200
    assert data["available"] is True
    assert data["city"] == "深圳"
    assert data["holiday"]["name"] == "端午节"
    assert data["hotel"]["hotel_name"] == "深圳每日推荐酒店"
    assert data["hotel"]["image_url"] == "https://images.example.test/hotel-1.jpg"
    assert data["hotel"]["is_advanced"] is True
    assert data["hotel"]["has_pool"] is True
    assert data["hotel"]["has_child_facility"] is True
    assert data["hotel"]["price_diff_nightly"] == -90
    assert data["hotel"]["comparison_average_nightly_tax_total_price"] == "CNY 610"


def test_daily_recommendation_prefers_detail_photo_over_cached_thumbnail(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    search_dir = cache_dir / "search"
    search_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module.finder, "cache_dir", cache_dir)
    monkeypatch.setattr(
        app_module,
        "fetch_daily_hotel_image_url",
        lambda detail_url: "https://ak-d.tripcdn.com/images/real-hotel-photo.jpg",
    )
    record = {
        "cache_key": ["search", "佛山", "2026-06-19::端午节"],
        "created_at": time.time(),
        "result": {
            "city": "佛山",
            "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
            "feature_filters": {},
            "choices": [
                {
                    "hotel_id": "hampton",
                    "hotel_name": "佛山希尔顿欢朋酒店",
                    "detail_url": "https://www.trip.com/hotels/detail/?hotelId=80911801",
                    "image_url": "https://dimg04.tripcdn.com/images/blurry-search-card.png",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": True,
                    "holiday_avg_nightly_tax_total_value": 520,
                    "price_diff_nightly": -90,
                }
            ],
        },
    }
    (search_dir / "daily.json").write_text(json.dumps(record), encoding="utf-8")

    data = app_module.daily_recommendation_payload()

    assert data["available"] is True
    assert data["hotel"]["image_url"] == "https://ak-d.tripcdn.com/images/real-hotel-photo.jpg"


def test_daily_image_url_filters_non_hotel_and_low_quality_sources():
    assert app_module.normalize_daily_image_url("https://dimg04.tripcdn.com/images/1re1e12000f3ia5caBF98.png") == ""
    assert app_module.normalize_daily_image_url("https://assets.example.test/logo-hotel.png") == ""
    assert (
        app_module.normalize_daily_image_url(
            "https://dimg04.c-ctrip.com/images/1mc1t12000noprlym6814_R_250_250_R5_D.jpg_.webp"
        )
        == "https://dimg04.c-ctrip.com/images/1mc1t12000noprlym6814_R_960_660_R5_D.jpg_.webp"
    )
    assert app_module.normalize_daily_image_url("https://images.example.test/clear-hotel-photo.jpg")


def test_daily_recommendation_does_not_fallback_to_suspicious_cached_image(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    search_dir = cache_dir / "search"
    search_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module.finder, "cache_dir", cache_dir)
    monkeypatch.setattr(app_module, "fetch_daily_hotel_image_url", lambda detail_url: "")
    record = {
        "cache_key": ["search", "深圳", "2026-06-19::端午节"],
        "created_at": time.time(),
        "result": {
            "city": "深圳",
            "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
            "feature_filters": {},
            "choices": [
                {
                    "hotel_id": "345032",
                    "hotel_name": "深圳香格里拉大酒店",
                    "detail_url": "https://www.trip.com/hotels/detail/?hotelId=345032",
                    "image_url": "https://dimg04.tripcdn.com/images/1re1e12000f3ia5caBF98.png",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": True,
                    "holiday_avg_nightly_tax_total_value": 609,
                    "price_diff_nightly": -31,
                }
            ],
        },
    }
    (search_dir / "daily.json").write_text(json.dumps(record), encoding="utf-8")

    data = app_module.daily_recommendation_payload()

    assert data["available"] is True
    assert data["hotel"]["hotel_name"] == "深圳香格里拉大酒店"
    assert data["hotel"]["image_url"] == ""


def test_daily_recommendation_applies_approved_hotel_name(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    search_dir = cache_dir / "search"
    search_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module.finder, "cache_dir", cache_dir)
    monkeypatch.setattr(app_module.finder, "_hotel_name_cache", {})

    class FakeStore:
        def hotel_name_records(self, hotel_ids):
            return {}

        def approved_hotel_name_records(self, hotel_ids):
            assert hotel_ids == ["80911801"]
            return {
                "80911801": {
                    "hotel_name": "佛山三龙湾希尔顿欢朋酒店",
                    "hotel_name_original": "Hampton by Hilton Foshan Sanlong Bay",
                    "review_id": 2,
                }
            }

        def approved_hotel_area_records(self, hotel_ids):
            return {}

    monkeypatch.setattr(app_module.finder, "_mysql_store", FakeStore())
    record = {
        "cache_key": ["search", "佛山", "2026-06-19::端午节"],
        "created_at": time.time(),
        "result": {
            "city": "佛山",
            "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
            "feature_filters": {},
            "choices": [
                {
                    "hotel_id": "80911801",
                    "hotel_name": "希尔顿欢朋酒店",
                    "hotel_original_name": "Hampton by Hilton Foshan Sanlong Bay",
                    "detail_url": "https://example.test/hotel-80911801",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": True,
                    "holiday_avg_nightly_tax_total_value": 520,
                    "price_diff_nightly": -90,
                }
            ],
        },
    }
    (search_dir / "daily.json").write_text(json.dumps(record), encoding="utf-8")

    data = app_module.daily_recommendation_payload()

    assert data["available"] is True
    assert data["hotel"]["hotel_name"] == "佛山三龙湾希尔顿欢朋酒店"
    assert data["hotel"]["hotel_name_source"] == "人工审核中文名"


def test_daily_recommendation_prefers_chinese_name_from_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    search_dir = cache_dir / "search"
    search_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module.finder, "cache_dir", cache_dir)
    record = {
        "cache_key": ["search", "深圳", "2026-06-19::端午节"],
        "created_at": time.time(),
        "result": {
            "city": "深圳",
            "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
            "feature_filters": {},
            "choices": [
                {
                    "hotel_id": "english",
                    "hotel_name": "English Test Hotel",
                    "detail_url": "https://example.test/english",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": True,
                    "holiday_avg_nightly_tax_total_value": 500,
                    "price_diff_nightly": -200,
                },
                *[
                    {
                        "hotel_id": f"english-{index}",
                        "hotel_name": f"English Test Hotel {index}",
                        "detail_url": f"https://example.test/english-{index}",
                        "is_advanced": True,
                        "has_pool": True,
                        "has_child_facility": True,
                        "holiday_avg_nightly_tax_total_value": 500 + index,
                        "price_diff_nightly": -190 + index,
                    }
                    for index in range(45)
                ],
                {
                    "hotel_id": "chinese",
                    "hotel_name": "深圳中文推荐酒店",
                    "detail_url": "https://example.test/chinese",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": True,
                    "holiday_avg_nightly_tax_total_value": 700,
                    "price_diff_nightly": 20,
                },
            ],
        },
    }
    (search_dir / "daily.json").write_text(json.dumps(record), encoding="utf-8")

    data = app_module.daily_recommendation_payload()

    assert data["available"] is True
    assert data["hotel"]["hotel_name"] == "深圳中文推荐酒店"


def test_daily_recommendation_requires_quality_pool_and_child_facilities(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    search_dir = cache_dir / "search"
    search_dir.mkdir(parents=True)
    monkeypatch.setattr(app_module.finder, "cache_dir", cache_dir)
    record = {
        "cache_key": ["search", "深圳", "2026-06-19::端午节"],
        "created_at": time.time(),
        "result": {
            "city": "深圳",
            "holiday": {"code": "2026-06-19::端午节", "name": "端午节"},
            "feature_filters": {},
            "choices": [
                {
                    "hotel_id": "pool-no-child",
                    "hotel_name": "深圳泳池酒店",
                    "detail_url": "https://example.test/pool-no-child",
                    "is_advanced": True,
                    "has_pool": True,
                    "has_child_facility": False,
                    "holiday_avg_nightly_tax_total_value": 500,
                    "price_diff_nightly": -200,
                },
                {
                    "hotel_id": "quality",
                    "hotel_name": "深圳亲子泳池高级酒店",
                    "detail_url": "https://example.test/quality",
                    "is_advanced": "1",
                    "has_pool": "true",
                    "has_child_facility": "是",
                    "holiday_avg_nightly_tax_total_value": 800,
                    "price_diff_nightly": 10,
                },
            ],
        },
    }
    (search_dir / "daily.json").write_text(json.dumps(record), encoding="utf-8")

    data = app_module.daily_recommendation_payload()

    assert data["available"] is True
    assert data["hotel"]["hotel_id"] == "quality"


def test_daily_prewarm_config_uses_prewarm_cities_and_holidays(monkeypatch):
    monkeypatch.setattr(
        app_module.finder,
        "list_holidays",
        lambda: [
            {"code": "2026-06-19::端午节"},
            {"code": "2026-10-01::国庆节"},
        ],
    )

    config = app_module.daily_prewarm_config({"city_limit": "2", "holiday_limit": "1", "delay_seconds": "0"})

    assert config["preset"] == "daily"
    assert len(config["cities"]) == 5
    assert config["cities"][:2] == ["深圳", "广州"]
    assert len([city for city in config["cities"][2:] if city in app_module.INTERNATIONAL_PREWARM_CITIES]) == 3
    assert config["holiday_codes"] == ["2026-06-19::端午节"]
    assert config["profiles"] == ["quality"]


def test_admin_status_reports_jobs_and_memory():
    client = flask_app.test_client()

    response = client.get("/api/admin/status")

    assert response.status_code == 200
    data = response.get_json()
    assert "memory" in data
    assert "rss_mb" in data["memory"]
    assert "peak_rss_mb" in data["memory"]
    assert "summary" in data
    assert "jobs" in data
    assert set(data["jobs"]) == {"active", "recent"}


def test_admin_status_exposes_job_pdf_download(monkeypatch):
    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()

    monkeypatch.setattr(app_module.finder, "_apply_cached_hotel_names_to_choices", lambda choices, city_name="": None)
    monkeypatch.setattr(app_module.finder, "_refresh_choice_area_names", lambda choices, city_name: None)
    monkeypatch.setattr(
        app_module.finder,
        "_build_area_recommendations",
        lambda choices, city_name: [
            {
                "area_name": "广州天河片区",
                "recommend_city": "广州",
                "hotel_count": len(choices),
                "lower_price_hotel_count": 1,
                "average_holiday_nightly_tax_total_price": "CNY 600",
                "average_price_diff_nightly_text": "-CNY 80",
            }
        ],
    )
    now = time.time()
    with app_module.job_lock:
        app_module.jobs["pdf-job"] = {
            "job_id": "pdf-job",
            "kind": "search",
            "status": "succeeded",
            "created_at": "2026-05-20T10:00:00Z",
            "updated_at": "2026-05-20T10:01:02Z",
            "created_ts": now,
            "updated_ts": now,
            "payload": {"city": "广州", "holiday_code": "2026-06-19::端午节"},
            "progress": {"stage": "succeeded", "message": "查询完成。", "percent": 100},
            "progress_events": [{"time": "2026-05-20T10:01:02Z", "message": "查询完成。"}],
            "result": {
                "city": "广州",
                "holiday": {"code": "2026-06-19::端午节", "name": "端午节", "days": 2},
                "comparison_windows": [],
                "choices": [
                    {
                        "hotel_id": "h1",
                        "hotel_name": "测试酒店",
                        "recommend_city": "广州",
                        "area_name": "广州天河片区",
                        "room_type_label": "高级房",
                        "holiday_avg_nightly_tax_total_price": "CNY 600",
                        "comparison_average_nightly_tax_total_price": "CNY 680",
                        "price_diff_nightly_text": "-CNY 80",
                    }
                ],
            },
            "partial_result": None,
        }

    client = flask_app.test_client()
    status_response = client.get("/api/admin/status")
    status_data = status_response.get_json()
    job = status_data["jobs"]["recent"][0]

    assert status_response.status_code == 200
    assert job["pdf_available"] is True
    assert job["pdf_url"] == "/api/admin/jobs/pdf-job/pdf"

    html_response = client.get("/api/admin/jobs/pdf-job/pdf?format=html")
    html = html_response.get_data(as_text=True)

    assert html_response.status_code == 200
    assert "反向旅游搜索任务 - 广州 - 端午节" in html
    assert "测试酒店" in html
    assert "广州天河片区" in html
    assert "最终结果" in html

    monkeypatch.setattr(app_module, "render_pdf_bytes", lambda html_text: b"%PDF-1.4\nfake\n")
    pdf_response = client.get("/api/admin/jobs/pdf-job/pdf")

    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"
    assert pdf_response.data.startswith(b"%PDF")
    assert "attachment;" in pdf_response.headers["Content-Disposition"]

    with app_module.job_lock:
        app_module.jobs.clear()
        app_module.job_signature_index.clear()


def test_admin_status_allows_public_host_without_token():
    client = flask_app.test_client()

    response = client.get("/api/admin/status", base_url="https://public.example.test")
    prewarm = client.get("/api/admin/prewarm/status", base_url="https://public.example.test")

    assert response.status_code == 200
    assert prewarm.status_code == 200


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
    stored_coverage = []

    def fake_store_completed_coverage_result(**kwargs):
        stored_coverage.append(kwargs)
        return True

    monkeypatch.setattr(app_module.finder, "store_completed_coverage_result", fake_store_completed_coverage_result)
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
    assert stored_coverage
    assert stored_coverage[0]["city"] == "北京"
    assert stored_coverage[0]["holiday_code"] == "2026-06-19::端午节"


def test_cache_prewarm_background_state(monkeypatch, tmp_path):
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
    monkeypatch.setattr(app_module, "PREWARM_STATE_PATH", tmp_path / "prewarm_state.json")
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
    assert final_state["run_started_local"]
    assert final_state["run_finished_local"]
    assert final_state["run_period_label"]
    assert " - " in final_state["run_period_label"]
    assert "缓存预热完成（" in final_state["message"]
    assert final_state["total"] == 1
    assert final_state["success_count"] == 1
    assert final_state["target_result_count"] == 1
    assert final_state["target_results"][0]["city"] == "深圳"
    assert final_state["target_results"][0]["holiday_code"] == "2026-06-19::端午节"
    assert final_state["target_results"][0]["profile_label"] == "默认条件"
    assert final_state["target_results"][0]["status"] == "live"
    assert final_state["target_results"][0]["choice_count"] == 1
    assert (tmp_path / "prewarm_state.json").exists()
    assert any("正在预热测试缓存" in event["message"] for event in final_state["events"])


def test_cache_prewarm_respects_runtime_window(monkeypatch, tmp_path):
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
    monkeypatch.setattr(app_module, "PREWARM_STATE_PATH", tmp_path / "prewarm_state.json")
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
    assert "缓存预热达到夜间时间窗口（" in final_state["message"]
    assert final_state["run_finished_local"]
    assert final_state["completed"] == 0
    assert final_state["target_result_count"] == 1
    assert final_state["target_results"][0]["status"] == "skipped"
    assert final_state["target_results"][0]["city"] == "深圳"
    assert final_state["skipped_count"] == 1
    assert calls == 0


def test_public_prewarm_state_sorts_recent_targets_first():
    with app_module.prewarm_lock:
        app_module.prewarm_state.clear()
        app_module.prewarm_state.update(
            {
                "status": "succeeded",
                "message": "测试排序",
                "target_results": [
                    {"city": "深圳", "completed_at": "2026-05-20T01:00:00Z"},
                    {"city": "广州", "status": "live", "completed_at": "2026-05-20T03:00:00Z"},
                    {"city": "东莞", "status": "failed", "error": "接口超时", "completed_at": "2026-05-20T02:00:00Z"},
                ],
            }
        )

    state = app_module.public_prewarm_state()

    assert [item["city"] for item in state["target_results"]] == ["广州", "东莞", "深圳"]
    assert state["summary"]["success_city_count"] == 1
    assert state["summary"]["failed_city_count"] == 1
    assert state["summary"]["failed_cities"][0]["city"] == "东莞"
