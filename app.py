from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from holiday_helper import HolidayCalendar, HolidayCalendarError
from reverse_travel import ReverseTravelFinder, ReverseTravelFinderError

app = Flask(__name__)
calendar = HolidayCalendar()
finder = ReverseTravelFinder(calendar)
job_executor = ThreadPoolExecutor(max_workers=2)
prewarm_executor = ThreadPoolExecutor(max_workers=1)
job_lock = threading.Lock()
prewarm_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
prewarm_state: dict[str, Any] = {
    "status": "idle",
    "message": "缓存预热未启动",
    "updated_at": "",
}
JOB_TTL_SECONDS = 6 * 60 * 60
NEARBY_CITY_WORKERS = 2

PREWARM_MAJOR_CITIES = (
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "重庆", "武汉",
    "西安", "长沙", "郑州", "天津", "青岛", "厦门", "福州", "宁波", "无锡", "合肥",
    "济南", "昆明", "贵阳", "南宁", "海口", "三亚", "大连", "沈阳", "哈尔滨", "长春",
    "石家庄", "太原", "呼和浩特", "兰州", "银川", "西宁", "乌鲁木齐", "拉萨",
    "东莞", "佛山", "惠州", "珠海", "中山", "江门", "汕尾", "韶关", "肇庆", "河源",
    "清远", "云浮",
)
PREWARM_FILTER_PROFILES = {
    "default": {
        "label": "默认条件",
        "advanced_filter": "all",
        "pool_filter": "all",
        "child_facility_filter": "all",
    },
    "quality": {
        "label": "高级+泳池+儿童设施",
        "advanced_filter": "yes",
        "pool_filter": "yes",
        "child_facility_filter": "yes",
    },
}

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
    if job.get("progress"):
        data["progress"] = job["progress"]
    if job.get("progress_events"):
        data["progress_events"] = job["progress_events"]
    if job.get("partial_result"):
        data["partial_result"] = job["partial_result"]
    return data


def compact_progress_event(progress: dict[str, Any]) -> dict[str, Any]:
    event = {
        "time": utc_timestamp(),
        "stage": progress.get("stage") or "",
        "message": progress.get("message") or "",
    }
    for key in ("percent", "city", "completed", "total"):
        if progress.get(key) not in ("", None):
            event[key] = progress[key]
    inner = progress.get("inner")
    if isinstance(inner, dict):
        for key in ("percent", "stage"):
            if inner.get(key) not in ("", None) and key not in event:
                event[key] = inner[key]
    return event


def append_job_progress_event(job: dict[str, Any], progress: dict[str, Any]) -> None:
    event = compact_progress_event(progress)
    if not event["message"]:
        return
    events = list(job.get("progress_events") or [])
    if events and events[-1].get("message") == event["message"] and events[-1].get("stage") == event["stage"]:
        events[-1] = event
    else:
        events.append(event)
    job["progress_events"] = events[-12:]


def is_local_request() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


def normalize_prewarm_profiles(value: Any) -> list[str]:
    if not value:
        return ["default"]
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = []
    profiles = [item for item in items if item in PREWARM_FILTER_PROFILES]
    return profiles or ["default"]


def prewarm_city_list(preset: str = "major", limit: int | None = None) -> list[str]:
    if preset != "major":
        return []
    cities = list(PREWARM_MAJOR_CITIES)
    if limit is not None and limit > 0:
        return cities[:limit]
    return cities


def public_prewarm_state() -> dict[str, Any]:
    with prewarm_lock:
        return copy.deepcopy(prewarm_state)


def append_prewarm_event(state: dict[str, Any], message: str, **extra: Any) -> None:
    event = {"time": utc_timestamp(), "message": message}
    event.update({key: value for key, value in extra.items() if value not in ("", None)})
    events = list(state.get("events") or [])
    if events and events[-1].get("message") == message:
        events[-1] = event
    else:
        events.append(event)
    state["events"] = events[-30:]


def update_prewarm_state(message: str, **extra: Any) -> None:
    with prewarm_lock:
        prewarm_state["message"] = message
        prewarm_state["updated_at"] = utc_timestamp()
        prewarm_state.update(extra)
        append_prewarm_event(prewarm_state, message, **extra)


def run_cache_prewarm(config: dict[str, Any]) -> None:
    cities = prewarm_city_list(
        preset=str(config.get("city_preset") or "major"),
        limit=parse_optional_int(config.get("city_limit"), "预热城市数量"),
    )
    profiles = normalize_prewarm_profiles(config.get("profiles"))
    configured_holidays = config.get("holiday_codes")
    if isinstance(configured_holidays, str):
        holiday_codes = [item.strip() for item in configured_holidays.split(",") if item.strip()]
    elif isinstance(configured_holidays, list):
        holiday_codes = [str(item).strip() for item in configured_holidays if str(item).strip()]
    else:
        holiday_codes = [item["code"] for item in finder.list_holidays()]

    targets = [
        (city, holiday_code, profile_name)
        for holiday_code in holiday_codes
        for city in cities
        for profile_name in profiles
    ]
    total = len(targets)
    started_at = time.time()
    success_count = 0
    cache_hits = 0
    live_count = 0
    error_count = 0
    errors: list[dict[str, str]] = []
    delay_seconds = parse_optional_int(config.get("delay_seconds"), "预热间隔秒数")
    if delay_seconds is None:
        delay_seconds = 1

    with prewarm_lock:
        prewarm_state.clear()
        prewarm_state.update(
            {
                "status": "running",
                "message": "缓存预热已开始",
                "created_at": utc_timestamp(),
                "updated_at": utc_timestamp(),
                "total": total,
                "completed": 0,
                "success_count": 0,
                "cache_hits": 0,
                "live_count": 0,
                "error_count": 0,
                "city_count": len(cities),
                "holiday_count": len(holiday_codes),
                "profiles": profiles,
                "events": [],
                "errors": [],
            }
        )
        append_prewarm_event(prewarm_state, "缓存预热已开始", total=total)

    for index, (city, holiday_code, profile_name) in enumerate(targets, start=1):
        profile = PREWARM_FILTER_PROFILES[profile_name]
        label = profile["label"]
        update_prewarm_state(
            f"正在预热 {index}/{total}：{city}，{holiday_code}，{label}",
            current_city=city,
            current_holiday_code=holiday_code,
            current_profile=profile_name,
            completed=index - 1,
            total=total,
        )

        def progress_callback(progress: dict[str, Any]) -> None:
            message = progress.get("message")
            if message:
                update_prewarm_state(f"{city}：{message}", completed=index - 1, total=total)

        try:
            result = finder.find_choices(
                city=city,
                holiday_code=holiday_code,
                min_price=None,
                max_price=None,
                advanced_filter=profile["advanced_filter"],
                pool_filter=profile["pool_filter"],
                child_facility_filter=profile["child_facility_filter"],
                use_cache=True,
                cache_only=False,
                progress_callback=progress_callback,
            )
        except Exception as exc:  # noqa: BLE001
            error_count += 1
            errors.append({"city": city, "holiday_code": holiday_code, "profile": profile_name, "error": str(exc)})
            update_prewarm_state(
                f"预热失败 {index}/{total}：{city}，{str(exc)}",
                completed=index,
                total=total,
                error_count=error_count,
                errors=errors[-20:],
            )
        else:
            success_count += 1
            cache = result.get("cache") or {}
            if cache.get("hit"):
                cache_hits += 1
            elif cache.get("source") == "live":
                live_count += 1
            update_prewarm_state(
                f"已预热 {index}/{total}：{city}，命中 {len(result.get('choices') or [])} 家",
                completed=index,
                total=total,
                success_count=success_count,
                cache_hits=cache_hits,
                live_count=live_count,
                error_count=error_count,
                errors=errors[-20:],
            )
        if delay_seconds > 0 and index < total:
            time.sleep(delay_seconds)

    elapsed_seconds = round(time.time() - started_at)
    update_prewarm_state(
        f"缓存预热完成：成功 {success_count}，缓存命中 {cache_hits}，新搜索 {live_count}，失败 {error_count}",
        status="succeeded",
        completed=total,
        total=total,
        success_count=success_count,
        cache_hits=cache_hits,
        live_count=live_count,
        error_count=error_count,
        elapsed_seconds=elapsed_seconds,
        errors=errors[-20:],
    )


def start_cache_prewarm(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    with prewarm_lock:
        if prewarm_state.get("status") == "running":
            return copy.deepcopy(prewarm_state), 202
        prewarm_state.clear()
        prewarm_state.update(
            {
                "status": "queued",
                "message": "缓存预热已排队",
                "created_at": utc_timestamp(),
                "updated_at": utc_timestamp(),
                "events": [{"time": utc_timestamp(), "message": "缓存预热已排队"}],
            }
        )
    prewarm_executor.submit(run_cache_prewarm, copy.deepcopy(config))
    return public_prewarm_state(), 202


def update_job_progress(job_id: str, progress: dict[str, Any]) -> None:
    progress_data = copy.deepcopy(progress)
    partial_result = progress_data.pop("partial_result", None)
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["progress"] = progress_data
        append_job_progress_event(job, progress_data)
        if partial_result is not None:
            job["partial_result"] = partial_result
        job["updated_at"] = utc_timestamp()
        job["updated_ts"] = time.time()


def search_result_from_payload(
    payload: dict,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], int]:
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
            progress_callback=progress_callback,
        )
    except (HolidayCalendarError, ReverseTravelFinderError) as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # pragma: no cover
        return {"error": f"查询失败: {exc}"}, 500
    return result, 200


def build_nearby_response(
    *,
    origin_city: str,
    target_cities: list[str],
    holiday_code: str,
    min_price_int: int | None,
    max_price_int: int | None,
    feature_filters_response: dict[str, Any],
    first_success: dict[str, Any] | None,
    city_results: list[dict[str, Any]],
    cache_hits: int,
    live_count: int,
    error_count: int,
) -> dict[str, Any]:
    order = {city: index for index, city in enumerate(target_cities)}
    ordered_city_results = sorted(city_results, key=lambda item: order.get(item.get("city") or "", 999))
    all_choices: list[dict[str, Any]] = []
    all_areas: list[dict[str, Any]] = []
    for item in ordered_city_results:
        all_choices.extend(copy.deepcopy(item.get("choices") or []))
        all_areas.extend(copy.deepcopy(item.get("area_recommendations") or []))

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

    return {
        "city": f"{origin_city}周边",
        "origin_city": origin_city,
        "nearby_cities": target_cities,
        "holiday": (first_success or {}).get("holiday") or holiday_meta(holiday_code),
        "price_filter": {"min_price": min_price_int, "max_price": max_price_int},
        "feature_filters": (first_success or {}).get("feature_filters") or feature_filters_response,
        "comparison_windows": (first_success or {}).get("comparison_windows") or [],
        "area_recommendations": all_areas[:10],
        "choices": all_choices,
        "city_results": ordered_city_results,
        "cache": {
            "summary_label": f"附近推荐：{cache_hits} 城缓存，{live_count} 城新搜索，{error_count} 城无结果",
            "hit": cache_hits > 0,
            "source": "nearby",
            "source_label": "附近推荐",
            "age_seconds": 0,
        },
    }


def nearby_search_result_from_payload(
    payload: dict,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], int]:
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

    city_results: list[dict[str, Any]] = []
    first_success = None
    cache_hits = 0
    live_count = 0
    error_count = 0
    done_count = 0

    def emit_nearby_progress(message: str, stage: str = "nearby", **extra: Any) -> None:
        if progress_callback is None:
            return
        progress_callback({"stage": stage, "message": message, **extra})

    def search_city(city: str) -> dict[str, Any]:
        def city_progress(progress: dict[str, Any]) -> None:
            message = progress.get("message") or "正在查询..."
            emit_nearby_progress(f"{city}：{message}", "nearby_city", city=city, inner=progress)

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
            progress_callback=city_progress,
        )

        cache = result.get("cache") or {}
        city_choices = []
        for item in result.get("choices") or []:
            choice = copy.deepcopy(item)
            choice["recommend_city"] = city
            city_choices.append(choice)

        city_areas = []
        for area in result.get("area_recommendations") or []:
            area_item = copy.deepcopy(area)
            area_item["recommend_city"] = city
            city_areas.append(area_item)

        return {
            "city": city,
            "result": result,
            "cache": cache,
            "city_result": {
                "city": city,
                "result_city": result.get("city") or city,
                "cache": cache,
                "choice_count": len(city_choices),
                "area_recommendations": city_areas,
                "choices": city_choices,
            },
        }

    emit_nearby_progress(
        f"正在并发搜索 {len(target_cities)} 个附近城市，最多同时搜索 {min(NEARBY_CITY_WORKERS, len(target_cities))} 个城市...",
        "nearby_start",
        completed=0,
        total=len(target_cities),
    )

    with ThreadPoolExecutor(max_workers=min(NEARBY_CITY_WORKERS, len(target_cities))) as executor:
        future_map = {executor.submit(search_city, city): city for city in target_cities}
        for future in as_completed(future_map):
            city = future_map[future]
            try:
                city_payload = future.result()
            except (HolidayCalendarError, ReverseTravelFinderError) as exc:
                error_count += 1
                city_results.append({"city": city, "error": str(exc), "choices": [], "area_recommendations": []})
                done_count += 1
                partial = build_nearby_response(
                    origin_city=origin_city,
                    target_cities=target_cities,
                    holiday_code=holiday_code,
                    min_price_int=min_price_int,
                    max_price_int=max_price_int,
                    feature_filters_response=feature_filters_response,
                    first_success=first_success,
                    city_results=city_results,
                    cache_hits=cache_hits,
                    live_count=live_count,
                    error_count=error_count,
                )
                emit_nearby_progress(
                    f"已完成 {done_count}/{len(target_cities)} 个城市，{city} 无结果：{exc}",
                    "nearby_progress",
                    completed=done_count,
                    total=len(target_cities),
                    partial_result=partial,
                )
                continue

            result = city_payload["result"]
            if first_success is None:
                first_success = result
            cache = city_payload["cache"]
            if cache.get("hit"):
                cache_hits += 1
            elif cache.get("source") == "live":
                live_count += 1

            city_results.append(city_payload["city_result"])
            done_count += 1

            partial = build_nearby_response(
                origin_city=origin_city,
                target_cities=target_cities,
                holiday_code=holiday_code,
                min_price_int=min_price_int,
                max_price_int=max_price_int,
                feature_filters_response=feature_filters_response,
                first_success=first_success,
                city_results=city_results,
                cache_hits=cache_hits,
                live_count=live_count,
                error_count=error_count,
            )
            emit_nearby_progress(
                f"已完成 {done_count}/{len(target_cities)} 个城市：{city} 命中 {city_payload['city_result']['choice_count']} 家酒店。",
                "nearby_progress",
                completed=done_count,
                total=len(target_cities),
                partial_result=partial,
            )

    response = build_nearby_response(
        origin_city=origin_city,
        target_cities=target_cities,
        holiday_code=holiday_code,
        min_price_int=min_price_int,
        max_price_int=max_price_int,
        feature_filters_response=feature_filters_response,
        first_success=first_success,
        city_results=city_results,
        cache_hits=cache_hits,
        live_count=live_count,
        error_count=error_count,
    )
    return response, 200


def area_result_from_payload(payload: dict) -> tuple[dict[str, Any], int]:
    city = (payload.get("city") or payload.get("origin_city") or "").strip()
    choices = payload.get("choices") or []
    if not city:
        return {"error": "city 不能为空"}, 400
    if not isinstance(choices, list):
        return {"error": "choices 必须是列表"}, 400
    try:
        result = finder.enhance_area_data(city, choices)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"区域规范化失败: {exc}"}, 500
    return result, 200


def run_job(job_id: str, kind: str, payload: dict[str, Any]) -> None:
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["progress"] = {"stage": "running", "message": "查询任务已开始。"}
        append_job_progress_event(job, job["progress"])
        job["updated_at"] = utc_timestamp()
        job["updated_ts"] = time.time()

    def progress_callback(progress: dict[str, Any]) -> None:
        update_job_progress(job_id, progress)

    if kind == "search":
        result, status_code = search_result_from_payload(payload, progress_callback=progress_callback)
    elif kind == "nearby":
        result, status_code = nearby_search_result_from_payload(payload, progress_callback=progress_callback)
    else:
        update_job_progress(job_id, {"stage": "areas", "message": "正在规范化推荐旅游区域。", "percent": 40})
        result, status_code = area_result_from_payload(payload)

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
            job["partial_result"] = result
            job["progress"] = {"stage": "succeeded", "message": "查询完成。", "percent": 100}
            append_job_progress_event(job, job["progress"])
        else:
            job["status"] = "failed"
            job["error"] = result.get("error") or "查询失败"
            job["progress"] = {"stage": "failed", "message": job["error"]}
            append_job_progress_event(job, job["progress"])


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
        "partial_result": None,
        "progress": {"stage": "queued", "message": "查询任务已创建，正在等待执行。"},
        "progress_events": [{"time": utc_timestamp(), "stage": "queued", "message": "查询任务已创建，正在等待执行。"}],
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


@app.post("/api/areas/start")
def areas_start():
    payload = request.get_json(silent=True) or {}
    return start_background_job("areas", payload)


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    cleanup_jobs()
    with job_lock:
        job = copy.deepcopy(jobs.get(job_id))
    if not job:
        return jsonify({"error": "查询任务不存在或已过期"}), 404
    status_code = 200 if job.get("status") != "failed" else int(job.get("status_code") or 500)
    return jsonify(public_job(job)), status_code


@app.get("/api/admin/prewarm/status")
def cache_prewarm_status():
    if not is_local_request():
        return jsonify({"error": "缓存预热状态仅允许本机查看"}), 403
    return jsonify(public_prewarm_state())


@app.post("/api/admin/prewarm/start")
def cache_prewarm_start():
    if not is_local_request():
        return jsonify({"error": "缓存预热仅允许本机启动"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        state, status_code = start_cache_prewarm(payload)
    except ReverseTravelFinderError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(state), status_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5012, debug=False)
