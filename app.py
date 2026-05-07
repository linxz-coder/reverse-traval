from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from holiday_helper import HolidayCalendar, HolidayCalendarError
from reverse_travel import ReverseTravelFinder, ReverseTravelFinderError

app = Flask(__name__)
calendar = HolidayCalendar()
finder = ReverseTravelFinder(calendar)
job_executor = ThreadPoolExecutor(max_workers=2)
job_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
JOB_TTL_SECONDS = 6 * 60 * 60

CITY_COORDINATES = {
    "深圳": (22.5431, 114.0579),
    "广州": (23.1291, 113.2644),
    "东莞": (23.0207, 113.7518),
    "惠州": (23.1118, 114.4162),
    "汕尾": (22.7862, 115.3753),
    "中山": (22.5170, 113.3927),
    "佛山": (23.0215, 113.1214),
    "江门": (22.5791, 113.0815),
    "河源": (23.7437, 114.7010),
    "肇庆": (23.0472, 112.4651),
    "珠海": (22.2707, 113.5767),
    "韶关": (24.8104, 113.5975),
    "清远": (23.6820, 113.0560),
    "云浮": (22.9151, 112.0445),
}

CITY_ALIASES = {
    "廣州": "广州",
    "東莞": "东莞",
    "江門": "江门",
    "肇慶": "肇庆",
    "韶關": "韶关",
    "雲浮": "云浮",
}

NEARBY_CITY_GROUPS = {
    "深圳": ("汕尾", "惠州", "广州", "东莞"),
    "广州": ("佛山", "东莞", "惠州", "中山"),
    "东莞": ("深圳", "惠州", "广州", "中山"),
    "惠州": ("深圳", "汕尾", "东莞", "河源"),
    "汕尾": ("惠州", "深圳", "河源", "东莞"),
    "珠海": ("中山", "江门", "广州", "深圳"),
    "中山": ("珠海", "江门", "广州", "佛山"),
    "佛山": ("广州", "中山", "江门", "肇庆"),
    "江门": ("中山", "珠海", "佛山", "广州"),
    "肇庆": ("广州", "佛山", "云浮", "江门"),
    "河源": ("惠州", "深圳", "韶关", "汕尾"),
    "韶关": ("广州", "清远", "河源", "肇庆"),
    "清远": ("广州", "韶关", "肇庆", "佛山"),
    "云浮": ("肇庆", "佛山", "江门", "广州"),
}


def parse_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否"}:
        return False
    return default


def parse_optional_int(value, field_name: str) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ReverseTravelFinderError(f"{field_name}必须是整数") from exc


def normalize_city(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw in CITY_ALIASES:
        return CITY_ALIASES[raw]
    if raw in CITY_COORDINATES:
        return raw
    normalized = finder._normalize_city_label(raw)
    return CITY_ALIASES.get(normalized, normalized)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_supported_city(lat: float, lon: float) -> str:
    return min(
        CITY_COORDINATES,
        key=lambda city: haversine_km(lat, lon, CITY_COORDINATES[city][0], CITY_COORDINATES[city][1]),
    )


def nearby_cities_for(origin_city: str, limit: int = 4) -> list[str]:
    city = normalize_city(origin_city)
    configured = [item for item in NEARBY_CITY_GROUPS.get(city, ()) if item != city]
    if not configured and city in CITY_COORDINATES:
        origin_lat, origin_lon = CITY_COORDINATES[city]
        configured = sorted(
            (item for item in CITY_COORDINATES if item != city),
            key=lambda item: haversine_km(origin_lat, origin_lon, CITY_COORDINATES[item][0], CITY_COORDINATES[item][1]),
        )
    return configured[: max(1, min(limit, 6))]


def holiday_meta(holiday_code: str) -> dict:
    for item in finder.list_holidays():
        if item["code"] == holiday_code:
            return {
                "code": item["code"],
                "name": item["name"],
                "check_in": item["start"],
                "check_out": item["end"],
                "days": item["days"],
            }
    return {"code": holiday_code, "name": "", "check_in": "", "check_out": "", "days": 0}


def request_price_filters(payload: dict) -> tuple[int | None, int | None]:
    return (
        parse_optional_int(payload.get("min_price"), "最低每晚含税"),
        parse_optional_int(payload.get("max_price"), "最高每晚含税"),
    )


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cleanup_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with job_lock:
        stale = [job_id for job_id, job in jobs.items() if float(job.get("updated_ts") or 0) < cutoff]
        for job_id in stale:
            jobs.pop(job_id, None)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    data = {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job.get("result") is not None:
        data["result"] = job["result"]
    if job.get("error"):
        data["error"] = job["error"]
    if job.get("status_code"):
        data["status_code"] = job["status_code"]
    return data


def search_result_from_payload(payload: dict) -> tuple[dict[str, Any], int]:
    city = (payload.get("city") or "").strip()
    holiday_code = (payload.get("holiday_code") or "").strip()
    advanced_filter = payload.get("advanced_filter")
    pool_filter = payload.get("pool_filter")
    child_facility_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")
    use_cache = parse_bool(payload.get("use_cache"), default=True)
    cache_only = parse_bool(payload.get("cache_only"), default=False)

    if not city or not holiday_code:
        return {"error": "city 和 holiday_code 不能为空"}, 400

    try:
        min_price_int, max_price_int = request_price_filters(payload)
        result = finder.find_choices(
            city=city,
            holiday_code=holiday_code,
            min_price=min_price_int,
            max_price=max_price_int,
            advanced_filter=advanced_filter,
            pool_filter=pool_filter,
            child_facility_filter=child_facility_filter,
            use_cache=use_cache,
            cache_only=cache_only,
        )
    except (HolidayCalendarError, ReverseTravelFinderError) as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # pragma: no cover
        return {"error": f"查询失败: {exc}"}, 500
    return result, 200


def nearby_search_result_from_payload(payload: dict) -> tuple[dict[str, Any], int]:
    holiday_code = (payload.get("holiday_code") or "").strip()
    origin_city = normalize_city(payload.get("origin_city") or payload.get("city"))
    advanced_filter = payload.get("advanced_filter")
    pool_filter = payload.get("pool_filter")
    child_facility_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")
    use_cache = parse_bool(payload.get("use_cache"), default=True)
    cache_only = parse_bool(payload.get("cache_only"), default=False)

    if not origin_city:
        try:
            origin_city = nearest_supported_city(float(payload.get("lat")), float(payload.get("lon")))
        except (TypeError, ValueError):
            return {"error": "请选择所在城市，或允许浏览器读取当前位置"}, 400
    if not holiday_code:
        return {"error": "holiday_code 不能为空"}, 400

    try:
        min_price_int, max_price_int = request_price_filters(payload)
        limit = parse_optional_int(payload.get("nearby_limit"), "附近城市数量") or 4
        feature_filters_response = finder._normalize_feature_filters(
            advanced_filter,
            pool_filter,
            child_facility_filter,
        ).to_response()
    except ReverseTravelFinderError as exc:
        return {"error": str(exc)}, 400

    target_cities = nearby_cities_for(origin_city, limit=limit)
    if not target_cities:
        return {"error": "暂时没有配置该城市的附近推荐城市"}, 400

    city_results = []
    all_choices = []
    all_areas = []
    first_success = None
    cache_hits = 0
    live_count = 0
    error_count = 0

    for city in target_cities:
        try:
            result = finder.find_choices(
                city=city,
                holiday_code=holiday_code,
                min_price=min_price_int,
                max_price=max_price_int,
                advanced_filter=advanced_filter,
                pool_filter=pool_filter,
                child_facility_filter=child_facility_filter,
                use_cache=use_cache,
                cache_only=cache_only,
            )
        except (HolidayCalendarError, ReverseTravelFinderError) as exc:
            error_count += 1
            city_results.append({"city": city, "error": str(exc), "choices": [], "area_recommendations": []})
            continue

        if first_success is None:
            first_success = result
        cache = result.get("cache") or {}
        if cache.get("hit"):
            cache_hits += 1
        elif cache.get("source") == "live":
            live_count += 1

        city_choices = []
        for item in result.get("choices") or []:
            choice = copy.deepcopy(item)
            choice["recommend_city"] = city
            city_choices.append(choice)
            all_choices.append(choice)

        city_areas = []
        for area in result.get("area_recommendations") or []:
            area_item = copy.deepcopy(area)
            area_item["recommend_city"] = city
            city_areas.append(area_item)
            all_areas.append(area_item)

        city_results.append(
            {
                "city": city,
                "result_city": result.get("city") or city,
                "cache": cache,
                "choice_count": len(city_choices),
                "area_recommendations": city_areas,
                "choices": city_choices,
            }
        )

    all_choices.sort(
        key=lambda item: (
            int(item.get("price_diff_nightly") or 0),
            int(item.get("holiday_avg_nightly_tax_total_value") or 0),
        )
    )
    all_areas.sort(
        key=lambda item: (
            -float(item.get("lower_price_ratio") or 0),
            -int(item.get("lower_price_hotel_count") or 0),
            -int(item.get("hotel_count") or 0),
            int(item.get("average_price_diff_nightly") or 0),
            int(item.get("average_holiday_nightly_tax_total_value") or 0),
        )
    )

    response = {
        "city": f"{origin_city}周边",
        "origin_city": origin_city,
        "nearby_cities": target_cities,
        "holiday": (first_success or {}).get("holiday") or holiday_meta(holiday_code),
        "price_filter": {"min_price": min_price_int, "max_price": max_price_int},
        "feature_filters": (first_success or {}).get("feature_filters") or feature_filters_response,
        "comparison_windows": (first_success or {}).get("comparison_windows") or [],
        "area_recommendations": all_areas[:10],
        "choices": all_choices,
        "city_results": city_results,
        "cache": {
            "summary_label": f"附近推荐：{cache_hits} 城缓存，{live_count} 城新搜索，{error_count} 城无结果",
            "hit": cache_hits > 0,
            "source": "nearby",
            "source_label": "附近推荐",
            "age_seconds": 0,
        },
    }
    return response, 200


def run_job(job_id: str, kind: str, payload: dict[str, Any]) -> None:
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["updated_at"] = utc_timestamp()
        job["updated_ts"] = time.time()

    if kind == "search":
        result, status_code = search_result_from_payload(payload)
    else:
        result, status_code = nearby_search_result_from_payload(payload)

    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["updated_at"] = utc_timestamp()
        job["updated_ts"] = time.time()
        job["status_code"] = status_code
        if status_code == 200:
            job["status"] = "succeeded"
            job["result"] = result
        else:
            job["status"] = "failed"
            job["error"] = result.get("error") or "查询失败"


def start_background_job(kind: str, payload: dict[str, Any]):
    cleanup_jobs()
    now = time.time()
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "kind": kind,
        "status": "queued",
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "created_ts": now,
        "updated_ts": now,
        "result": None,
        "error": "",
        "status_code": None,
    }
    with job_lock:
        jobs[job_id] = job
    job_executor.submit(run_job, job_id, kind, copy.deepcopy(payload))
    return (
        jsonify(
            {
                "job_id": job_id,
                "status": "queued",
                "poll_url": f"/api/jobs/{job_id}",
                "poll_interval_ms": 2000,
            }
        ),
        202,
    )


@app.errorhandler(HTTPException)
def api_http_error(exc: HTTPException):
    if request.path.startswith("/api/"):
        return jsonify({"error": exc.description or exc.name}), exc.code or 500
    return exc


@app.errorhandler(Exception)
def api_unhandled_error(exc: Exception):
    if request.path.startswith("/api/"):
        return jsonify({"error": f"服务异常: {exc}"}), 500
    raise exc


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/holidays")
def holidays():
    try:
        items = finder.list_holidays()
    except HolidayCalendarError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"holidays": items})


@app.get("/api/nearby-cities")
def nearby_cities():
    return jsonify(
        {
            "cities": sorted(CITY_COORDINATES),
            "nearby": {city: list(values) for city, values in NEARBY_CITY_GROUPS.items()},
        }
    )


@app.post("/api/resolve-location")
def resolve_location():
    payload = request.get_json(silent=True) or {}
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "无法读取当前位置坐标"}), 400
    city = nearest_supported_city(lat, lon)
    return jsonify({"city": city, "lat": lat, "lon": lon})


@app.post("/api/search")
def search():
    payload = request.get_json(silent=True) or {}
    result, status_code = search_result_from_payload(payload)
    return jsonify(result), status_code


@app.post("/api/search/start")
def search_start():
    payload = request.get_json(silent=True) or {}
    return start_background_job("search", payload)


@app.post("/api/nearby-search")
def nearby_search():
    payload = request.get_json(silent=True) or {}
    result, status_code = nearby_search_result_from_payload(payload)
    return jsonify(result), status_code


@app.post("/api/nearby-search/start")
def nearby_search_start():
    payload = request.get_json(silent=True) or {}
    return start_background_job("nearby", payload)


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    cleanup_jobs()
    with job_lock:
        job = copy.deepcopy(jobs.get(job_id))
    if not job:
        return jsonify({"error": "查询任务不存在或已过期"}), 404
    status_code = 200 if job.get("status") != "failed" else int(job.get("status_code") or 500)
    return jsonify(public_job(job)), status_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5012, debug=False)
