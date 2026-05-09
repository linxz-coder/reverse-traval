from __future__ import annotations

import copy
import json
import math
import os
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


def env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 16) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


JOB_WORKERS = env_int("REVERSE_TRAVEL_JOB_WORKERS", 2, min_value=1, max_value=4)
NEARBY_CITY_WORKERS = env_int("REVERSE_TRAVEL_NEARBY_CITY_WORKERS", 2, min_value=1, max_value=4)

job_executor = ThreadPoolExecutor(max_workers=JOB_WORKERS)
refresh_executor = ThreadPoolExecutor(max_workers=2)
prewarm_executor = ThreadPoolExecutor(max_workers=1)
job_lock = threading.Lock()
prewarm_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
job_signature_index: dict[str, str] = {}
prewarm_state: dict[str, Any] = {
    "status": "idle",
    "message": "缓存预热未启动",
    "updated_at": "",
}
JOB_TTL_SECONDS = 6 * 60 * 60

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

PROVINCE_CITY_OPTIONS = (
    ("北京", ("北京",)),
    ("天津", ("天津",)),
    ("上海", ("上海",)),
    ("重庆", ("重庆",)),
    ("河北", ("石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水")),
    ("山西", ("太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁")),
    ("内蒙古", ("呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布", "兴安盟", "锡林郭勒盟", "阿拉善盟")),
    ("辽宁", ("沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛")),
    ("吉林", ("长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边")),
    ("黑龙江", ("哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化", "大兴安岭")),
    ("江苏", ("南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁")),
    ("浙江", ("杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水")),
    ("安徽", ("合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城")),
    ("福建", ("福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德")),
    ("江西", ("南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶")),
    ("山东", ("济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽")),
    ("河南", ("郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店", "济源")),
    ("湖北", ("武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施", "仙桃", "潜江", "天门", "神农架")),
    ("湖南", ("长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底", "湘西")),
    ("广东", ("广州", "深圳", "珠海", "汕头", "佛山", "韶关", "河源", "梅州", "惠州", "汕尾", "东莞", "中山", "江门", "阳江", "湛江", "茂名", "肇庆", "清远", "潮州", "揭阳", "云浮")),
    ("广西", ("南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左")),
    ("海南", ("海口", "三亚", "三沙", "儋州", "五指山", "琼海", "文昌", "万宁", "东方", "定安", "屯昌", "澄迈", "临高", "白沙", "昌江", "乐东", "陵水", "保亭", "琼中")),
    ("四川", ("成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "阿坝", "甘孜", "凉山")),
    ("贵州", ("贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔西南", "黔东南", "黔南")),
    ("云南", ("昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "楚雄", "红河", "文山", "西双版纳", "大理", "德宏", "怒江", "迪庆")),
    ("西藏", ("拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里")),
    ("陕西", ("西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛")),
    ("甘肃", ("兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "临夏", "甘南")),
    ("青海", ("西宁", "海东", "海北", "黄南", "海南", "果洛", "玉树", "海西")),
    ("宁夏", ("银川", "石嘴山", "吴忠", "固原", "中卫")),
    ("新疆", ("乌鲁木齐", "克拉玛依", "吐鲁番", "哈密", "昌吉", "博尔塔拉", "巴音郭楞", "阿克苏", "克孜勒苏", "喀什", "和田", "伊犁", "塔城", "阿勒泰", "石河子", "阿拉尔", "图木舒克", "五家渠", "北屯", "铁门关", "双河", "可克达拉", "昆玉")),
    ("香港", ("香港",)),
    ("澳门", ("澳门",)),
    ("台湾", ("台北", "新北", "桃园", "台中", "台南", "高雄", "基隆", "新竹", "嘉义", "宜兰", "新竹县", "苗栗", "彰化", "南投", "云林", "嘉义县", "屏东", "台东", "花莲", "澎湖", "金门", "连江")),
)
PROVINCE_CITY_MAP = {province: tuple(cities) for province, cities in PROVINCE_CITY_OPTIONS}
CITY_TO_PROVINCE = {
    city: province
    for province, cities in PROVINCE_CITY_OPTIONS
    for city in cities
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
NATIONAL_NEARBY_FALLBACKS = {
    "北京": ("天津", "廊坊", "承德", "张家口"),
    "天津": ("北京", "唐山", "廊坊", "沧州"),
    "上海": ("苏州", "嘉兴", "无锡", "南通"),
    "重庆": ("成都", "广安", "遵义", "恩施"),
    "香港": ("深圳", "广州", "澳门", "珠海"),
    "澳门": ("珠海", "中山", "香港", "广州"),
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


def province_city_options() -> list[dict[str, Any]]:
    return [
        {"province": province, "cities": list(cities)}
        for province, cities in PROVINCE_CITY_OPTIONS
    ]


def province_nearby_cities(origin_city: str, limit: int) -> list[str]:
    province = CITY_TO_PROVINCE.get(origin_city)
    if not province:
        return []
    cities = list(PROVINCE_CITY_MAP.get(province, ()))
    if len(cities) <= 1:
        return list(NATIONAL_NEARBY_FALLBACKS.get(origin_city, ()))[:limit]
    try:
        index = cities.index(origin_city)
    except ValueError:
        return [city for city in cities if city != origin_city][:limit]
    candidates: list[str] = []
    for offset in range(1, len(cities)):
        for nearby_index in (index - offset, index + offset):
            if 0 <= nearby_index < len(cities):
                city = cities[nearby_index]
                if city != origin_city and city not in candidates:
                    candidates.append(city)
            if len(candidates) >= limit:
                return candidates
    return candidates[:limit]


def nearby_cities_for(origin_city: str, limit: int = 4) -> list[str]:
    city = normalize_city(origin_city)
    configured = [item for item in NEARBY_CITY_GROUPS.get(city, ()) if item != city]
    if not configured and city in CITY_COORDINATES:
        origin_lat, origin_lon = CITY_COORDINATES[city]
        configured = sorted(
            (item for item in CITY_COORDINATES if item != city),
            key=lambda item: haversine_km(origin_lat, origin_lon, CITY_COORDINATES[item][0], CITY_COORDINATES[item][1]),
        )
    if not configured:
        configured = province_nearby_cities(city, max(1, min(limit, 6)))
    if not configured:
        configured = list(NATIONAL_NEARBY_FALLBACKS.get(city, ()))
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


def apply_price_filter_to_result(
    result: dict[str, Any],
    min_price: int | None,
    max_price: int | None,
) -> dict[str, Any]:
    filtered = copy.deepcopy(result)
    choices: list[dict[str, Any]] = []
    for item in filtered.get("choices") or []:
        value = int(item.get("holiday_avg_nightly_tax_total_value") or 0)
        if min_price is not None and value < min_price:
            continue
        if max_price is not None and value > max_price:
            continue
        choices.append(item)
    filtered["choices"] = choices
    filtered["price_filter"] = {"min_price": min_price, "max_price": max_price}
    city_name = filtered.get("city") or filtered.get("origin_city") or ""
    if city_name:
        filtered["area_recommendations"] = finder._build_area_recommendations(choices, city_name)
    return filtered


def canonical_optional_int(value: Any) -> str:
    if value in ("", None):
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value).strip()


def canonical_tri_state(value: Any) -> str:
    try:
        return finder._normalize_tri_state(str(value) if value is not None else None, "筛选项")
    except ReverseTravelFinderError:
        return str(value or "all").strip().lower()


def canonical_job_signature(kind: str, payload: dict[str, Any]) -> str | None:
    if kind not in {"search", "nearby"}:
        return None

    holiday_code = str(payload.get("holiday_code") or "").strip()
    child_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")
    base: dict[str, Any] = {
        "version": 1,
        "kind": kind,
        "holiday_code": holiday_code,
        "min_price": canonical_optional_int(payload.get("min_price")),
        "max_price": canonical_optional_int(payload.get("max_price")),
        "advanced_filter": canonical_tri_state(payload.get("advanced_filter")),
        "pool_filter": canonical_tri_state(payload.get("pool_filter")),
        "child_facility_filter": canonical_tri_state(child_filter),
        "use_cache": parse_bool(payload.get("use_cache"), default=True),
        "cache_only": parse_bool(payload.get("cache_only"), default=False),
    }
    if kind == "search":
        raw_city = str(payload.get("city") or "").strip()
        base["city"] = normalize_city(raw_city) or raw_city
    else:
        origin_city = normalize_city(payload.get("origin_city") or payload.get("city"))
        if not origin_city:
            try:
                origin_city = nearest_supported_city(float(payload.get("lat")), float(payload.get("lon")))
            except (TypeError, ValueError):
                origin_city = ""
        base["origin_city"] = origin_city
        base["nearby_limit"] = canonical_optional_int(payload.get("nearby_limit") or 4)

    return json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cleanup_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with job_lock:
        stale = [job_id for job_id, job in jobs.items() if float(job.get("updated_ts") or 0) < cutoff]
        for job_id in stale:
            job = jobs.pop(job_id, None)
            signature = (job or {}).get("signature")
            if signature and job_signature_index.get(signature) == job_id:
                job_signature_index.pop(signature, None)


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


def normalize_prewarm_city_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = []

    cities: list[str] = []
    seen: set[str] = set()
    for item in items:
        city = normalize_city(item)
        if not city or city in seen:
            continue
        cities.append(city)
        seen.add(city)
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
    cities = normalize_prewarm_city_list(config.get("cities"))
    if not cities:
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
    max_runtime_seconds = parse_optional_int(config.get("max_runtime_seconds"), "预热最长运行秒数")
    if max_runtime_seconds is not None:
        max_runtime_seconds = max(0, max_runtime_seconds)
    completed_count = 0
    stopped_by_time_window = False

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
                "max_runtime_seconds": max_runtime_seconds,
                "skipped_count": 0,
                "events": [],
                "errors": [],
            }
        )
        append_prewarm_event(prewarm_state, "缓存预热已开始", total=total)

    for index, (city, holiday_code, profile_name) in enumerate(targets, start=1):
        if max_runtime_seconds is not None and time.time() - started_at >= max_runtime_seconds:
            stopped_by_time_window = True
            break

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
            completed_count = index
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
            completed_count = index
        if delay_seconds > 0 and index < total:
            time.sleep(delay_seconds)

    elapsed_seconds = round(time.time() - started_at)
    if stopped_by_time_window:
        update_prewarm_state(
            f"缓存预热达到夜间时间窗口：已完成 {completed_count}/{total}，成功 {success_count}，缓存命中 {cache_hits}，新搜索 {live_count}，失败 {error_count}",
            status="succeeded",
            completed=completed_count,
            total=total,
            success_count=success_count,
            cache_hits=cache_hits,
            live_count=live_count,
            error_count=error_count,
            skipped_count=max(0, total - completed_count),
            elapsed_seconds=elapsed_seconds,
            errors=errors[-20:],
        )
        return

    update_prewarm_state(
        f"缓存预热完成：成功 {success_count}，缓存命中 {cache_hits}，新搜索 {live_count}，失败 {error_count}",
        status="succeeded",
        completed=completed_count,
        total=total,
        success_count=success_count,
        cache_hits=cache_hits,
        live_count=live_count,
        error_count=error_count,
        skipped_count=0,
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
        def emit_progress(progress: dict[str, Any]) -> None:
            if progress_callback is None:
                return
            progress_data = copy.deepcopy(progress)
            partial_result = progress_data.get("partial_result")
            if isinstance(partial_result, dict):
                progress_data["partial_result"] = apply_price_filter_to_result(
                    partial_result,
                    min_price_int,
                    max_price_int,
                )
            progress_callback(progress_data)

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
            progress_callback=emit_progress if progress_callback is not None else None,
        )
    except (HolidayCalendarError, ReverseTravelFinderError) as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # pragma: no cover
        return {"error": f"查询失败: {exc}"}, 500
    return result, 200


def cached_search_result_from_payload(payload: dict) -> tuple[dict[str, Any] | None, int]:
    if not parse_bool(payload.get("use_cache"), default=True):
        return None, 404

    city = (payload.get("city") or "").strip()
    holiday_code = (payload.get("holiday_code") or "").strip()
    advanced_filter = payload.get("advanced_filter")
    pool_filter = payload.get("pool_filter")
    child_facility_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")

    if not city or not holiday_code:
        return {"error": "city 和 holiday_code 不能为空"}, 400

    try:
        min_price_int, max_price_int = request_price_filters(payload)
        result = finder.find_cached_choices(
            city=city,
            holiday_code=holiday_code,
            min_price=min_price_int,
            max_price=max_price_int,
            advanced_filter=advanced_filter,
            pool_filter=pool_filter,
            child_facility_filter=child_facility_filter,
        )
    except (HolidayCalendarError, ReverseTravelFinderError) as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # pragma: no cover
        return {"error": f"读取缓存失败: {exc}"}, 500
    return result, 200 if result is not None else 404


def stale_search_result_from_payload(payload: dict) -> tuple[dict[str, Any] | None, int]:
    if not parse_bool(payload.get("use_cache"), default=True):
        return None, 404

    city = (payload.get("city") or "").strip()
    holiday_code = (payload.get("holiday_code") or "").strip()
    advanced_filter = payload.get("advanced_filter")
    pool_filter = payload.get("pool_filter")
    child_facility_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")

    if not city or not holiday_code:
        return {"error": "city 和 holiday_code 不能为空"}, 400

    try:
        min_price_int, max_price_int = request_price_filters(payload)
        result = finder.find_stale_cached_choices(
            city=city,
            holiday_code=holiday_code,
            min_price=min_price_int,
            max_price=max_price_int,
            advanced_filter=advanced_filter,
            pool_filter=pool_filter,
            child_facility_filter=child_facility_filter,
        )
    except (HolidayCalendarError, ReverseTravelFinderError) as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # pragma: no cover
        return {"error": f"读取旧缓存失败: {exc}"}, 500
    if result is not None:
        result["partial"] = {
            "stage": "stale_cache_preview",
            "message": "先显示旧缓存结果，后台正在刷新最新价格。",
            "preliminary": True,
            "displayed_choice_count": len(result.get("choices") or []),
            "total_choice_count": len(result.get("choices") or []),
        }
    return result, 200 if result is not None else 404


def build_nearby_city_result(city: str, result: dict[str, Any]) -> dict[str, Any]:
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
        "area_recommendations": all_areas,
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


def cached_nearby_search_result_from_payload(payload: dict) -> tuple[dict[str, Any] | None, int]:
    if not parse_bool(payload.get("use_cache"), default=True):
        return None, 404

    holiday_code = (payload.get("holiday_code") or "").strip()
    origin_city = normalize_city(payload.get("origin_city") or payload.get("city"))
    advanced_filter = payload.get("advanced_filter")
    pool_filter = payload.get("pool_filter")
    child_facility_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")

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
    for city in target_cities:
        try:
            result = finder.find_cached_choices(
                city=city,
                holiday_code=holiday_code,
                min_price=min_price_int,
                max_price=max_price_int,
                advanced_filter=advanced_filter,
                pool_filter=pool_filter,
                child_facility_filter=child_facility_filter,
            )
        except (HolidayCalendarError, ReverseTravelFinderError) as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # pragma: no cover
            return {"error": f"读取缓存失败: {exc}"}, 500
        if result is None:
            return None, 404
        if first_success is None:
            first_success = result
        if (result.get("cache") or {}).get("hit"):
            cache_hits += 1
        city_results.append(build_nearby_city_result(city, result)["city_result"])

    return (
        build_nearby_response(
            origin_city=origin_city,
            target_cities=target_cities,
            holiday_code=holiday_code,
            min_price_int=min_price_int,
            max_price_int=max_price_int,
            feature_filters_response=feature_filters_response,
            first_success=first_success,
            city_results=city_results,
            cache_hits=cache_hits,
            live_count=0,
            error_count=0,
        ),
        200,
    )


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

        return build_nearby_city_result(city, result)

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


def hotel_name_result_from_payload(payload: dict) -> tuple[dict[str, Any], int]:
    city = (payload.get("city") or payload.get("origin_city") or "").strip()
    choices = payload.get("choices") or []
    if not city:
        return {"error": "city 不能为空"}, 400
    if not isinstance(choices, list):
        return {"error": "choices 必须是列表"}, 400
    try:
        result = finder.enhance_hotel_name_data(city, choices)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"酒店名刷新失败: {exc}"}, 500
    return result, 200


def coverage_result_from_payload(
    payload: dict,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], int]:
    city = (payload.get("city") or "").strip()
    holiday_code = (payload.get("holiday_code") or "").strip()
    choices = payload.get("choices") or []
    advanced_filter = payload.get("advanced_filter")
    pool_filter = payload.get("pool_filter")
    child_facility_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")
    if not city or not holiday_code:
        return {"error": "city 和 holiday_code 不能为空"}, 400
    if not isinstance(choices, list):
        return {"error": "choices 必须是列表"}, 400
    try:
        min_price_int, max_price_int = request_price_filters(payload)
        result = finder.supplement_coverage_choices(
            city=city,
            holiday_code=holiday_code,
            choices=choices,
            min_price=min_price_int,
            max_price=max_price_int,
            advanced_filter=advanced_filter,
            pool_filter=pool_filter,
            child_facility_filter=child_facility_filter,
            progress_callback=progress_callback,
        )
    except (HolidayCalendarError, ReverseTravelFinderError) as exc:
        return {"error": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        return {"error": f"行政区补充失败: {exc}"}, 500
    return result, 200


def cached_result_for_job_start(kind: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    if kind == "search":
        return cached_search_result_from_payload(payload)
    if kind == "nearby":
        return cached_nearby_search_result_from_payload(payload)
    return None, 404


def stale_result_for_job_start(kind: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    if kind == "search":
        return stale_search_result_from_payload(payload)
    return None, 404


def job_start_payload(job: dict[str, Any], *, reused: bool = False, cache_hit: bool = False) -> dict[str, Any]:
    payload = {
        "job_id": job["job_id"],
        "status": job["status"],
        "poll_url": f"/api/jobs/{job['job_id']}",
        "poll_interval_ms": 1500 if job.get("status") == "succeeded" else 2000,
        "reused": reused,
        "cache_hit": cache_hit,
    }
    if job.get("progress"):
        payload["progress"] = job["progress"]
    if job.get("progress_events"):
        payload["progress_events"] = job["progress_events"]
    if job.get("partial_result") is not None:
        payload["partial_result"] = job["partial_result"]
    if job.get("result") is not None:
        payload["result"] = job["result"]
    return payload


def create_completed_job(kind: str, payload: dict[str, Any], result: dict[str, Any], signature: str | None) -> dict[str, Any]:
    now = time.time()
    job_id = uuid.uuid4().hex
    message = "已命中缓存，直接返回结果。"
    job = {
        "job_id": job_id,
        "kind": kind,
        "signature": signature,
        "status": "succeeded",
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "created_ts": now,
        "updated_ts": now,
        "payload": copy.deepcopy(payload),
        "result": result,
        "partial_result": result,
        "progress": {"stage": "cache_hit", "message": message, "percent": 100},
        "progress_events": [{"time": utc_timestamp(), "stage": "cache_hit", "message": message, "percent": 100}],
        "error": "",
        "status_code": 200,
    }
    with job_lock:
        jobs[job_id] = job
        if signature:
            job_signature_index[signature] = job_id
    return job


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
    elif kind == "coverage":
        update_job_progress(job_id, {"stage": "coverage", "message": "基础结果已显示，正在后台补充缺失行政区。", "percent": 5})
        result, status_code = coverage_result_from_payload(payload, progress_callback=progress_callback)
    elif kind == "hotel_names":
        update_job_progress(job_id, {"stage": "hotel_names", "message": "正在后台匹配简体中文酒店名。", "percent": 40})
        result, status_code = hotel_name_result_from_payload(payload)
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
            signature = job.get("signature")
            if signature and job_signature_index.get(signature) == job_id:
                job_signature_index.pop(signature, None)


def start_background_job(kind: str, payload: dict[str, Any]):
    cleanup_jobs()
    now = time.time()
    signature = canonical_job_signature(kind, payload)
    allow_completed_reuse = parse_bool(payload.get("use_cache"), default=True)

    if signature:
        with job_lock:
            existing_id = job_signature_index.get(signature)
            existing_job = jobs.get(existing_id or "")
            if existing_job and (
                existing_job.get("status") in {"queued", "running"}
                or (allow_completed_reuse and existing_job.get("status") == "succeeded")
            ):
                if existing_job.get("status") in {"queued", "running"}:
                    append_job_progress_event(
                        existing_job,
                        {"stage": "deduped", "message": "已复用同条件查询任务，等待同一份结果。"},
                    )
                    existing_job["updated_at"] = utc_timestamp()
                    existing_job["updated_ts"] = time.time()
                reused_job = copy.deepcopy(existing_job)
                status_code = 200 if reused_job.get("status") == "succeeded" else 202
                return jsonify(job_start_payload(reused_job, reused=True)), status_code

    cached_result, cached_status_code = cached_result_for_job_start(kind, payload)
    if cached_status_code == 200 and cached_result is not None:
        job = create_completed_job(kind, payload, cached_result, signature)
        return jsonify(job_start_payload(job, cache_hit=True)), 200
    if cached_status_code not in {200, 404}:
        return jsonify(cached_result or {"error": "缓存读取失败"}), cached_status_code

    stale_result, stale_status_code = stale_result_for_job_start(kind, payload)
    if stale_status_code not in {200, 404}:
        return jsonify(stale_result or {"error": "旧缓存读取失败"}), stale_status_code

    job_id = uuid.uuid4().hex
    initial_progress = (
        {"stage": "stale_cache_preview", "message": "已先显示旧缓存，正在后台刷新最新价格。", "percent": 8}
        if stale_result is not None
        else {"stage": "queued", "message": "查询任务已创建，正在等待执行。"}
    )
    job = {
        "job_id": job_id,
        "kind": kind,
        "signature": signature,
        "status": "queued",
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "created_ts": now,
        "updated_ts": now,
        "payload": copy.deepcopy(payload),
        "result": None,
        "partial_result": stale_result,
        "progress": initial_progress,
        "progress_events": [{"time": utc_timestamp(), **initial_progress}],
        "error": "",
        "status_code": None,
    }
    with job_lock:
        jobs[job_id] = job
        if signature:
            job_signature_index[signature] = job_id
    executor = refresh_executor if kind in {"areas", "hotel_names"} else job_executor
    executor.submit(run_job, job_id, kind, copy.deepcopy(payload))
    return jsonify(job_start_payload(job)), 202


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
    return render_template("index.html", province_city_options=province_city_options())


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
            "cities": sorted(CITY_TO_PROVINCE),
            "province_cities": province_city_options(),
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


@app.post("/api/hotel-names/start")
def hotel_names_start():
    payload = request.get_json(silent=True) or {}
    return start_background_job("hotel_names", payload)


@app.post("/api/coverage/start")
def coverage_start():
    payload = request.get_json(silent=True) or {}
    return start_background_job("coverage", payload)


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
