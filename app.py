from __future__ import annotations

import copy
import hashlib
import hmac
import html
import json
import math
import os
import re
import resource
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from holiday_helper import HolidayCalendar, HolidayCalendarError
from mysql_store import choice_identity, get_mysql_store
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
daily_image_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
job_signature_index: dict[str, str] = {}
daily_image_cache: dict[str, tuple[float, str]] = {}
PREWARM_STATE_PATH = finder.cache_dir / "prewarm_state.json"


def load_prewarm_state() -> dict[str, Any]:
    try:
        if not PREWARM_STATE_PATH.exists():
            return {}
        data = json.loads(PREWARM_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def default_prewarm_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "message": "缓存预热未启动",
        "updated_at": "",
        "target_results": [],
        "target_result_count": 0,
    }


def persist_prewarm_state(state: dict[str, Any]) -> None:
    try:
        PREWARM_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = PREWARM_STATE_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(PREWARM_STATE_PATH)
    except OSError:
        return


prewarm_state: dict[str, Any] = {**default_prewarm_state(), **load_prewarm_state()}
JOB_TTL_SECONDS = 6 * 60 * 60
DAILY_IMAGE_CACHE_TTL_SECONDS = 24 * 60 * 60
ADMIN_TOKEN = os.environ.get("REVERSE_TRAVEL_ADMIN_TOKEN", "").strip()

GUANGDONG_PREWARM_CITIES = (
    "深圳", "广州", "东莞", "惠州", "佛山", "珠海", "中山", "江门", "汕尾", "肇庆",
    "韶关", "河源", "清远", "云浮",
)
PREWARM_MAJOR_CITIES = (
    *GUANGDONG_PREWARM_CITIES,
    "北京", "上海", "杭州", "南京", "苏州", "成都", "重庆", "武汉",
    "西安", "长沙", "郑州", "天津", "青岛", "厦门", "福州", "宁波", "无锡", "合肥",
    "济南", "昆明", "贵阳", "南宁", "海口", "三亚", "大连", "沈阳", "哈尔滨", "长春",
    "石家庄", "太原", "呼和浩特", "兰州", "银川", "西宁", "乌鲁木齐", "拉萨",
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

DAILY_PREWARM_CITY_LIMIT = env_int("REVERSE_TRAVEL_DAILY_PREWARM_CITY_LIMIT", 12, min_value=1, max_value=40)
DAILY_RECOMMENDATION_SCAN_LIMIT = 200

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


def result_price_fingerprint(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    choices = result.get("choices") if isinstance(result.get("choices"), list) else []
    partial = result.get("partial") if isinstance(result.get("partial"), dict) else {}
    identity = [
        partial.get("stage") or "",
        result.get("city") or "",
        (result.get("holiday") or {}).get("code") if isinstance(result.get("holiday"), dict) else "",
    ]
    for index, item in enumerate(choices[:80]):
        if not isinstance(item, dict):
            continue
        identity.append(
            "|".join(
                [
                    choice_identity(item, index),
                    str(item.get("hotel_name") or ""),
                    str(item.get("holiday_tax_total_value") or item.get("holiday_tax_total_price") or ""),
                    str(item.get("holiday_avg_nightly_tax_total_value") or ""),
                    str(item.get("comparison_lowest_tax_total_value") or ""),
                    str(item.get("comparison_lowest_check_in") or ""),
                    str(item.get("price_diff_nightly") or ""),
                ]
            )
        )
    raw = "\n".join(str(value) for value in identity)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def persist_result_prices(result: Any, *, job_id: str = "", source: str = "api") -> int:
    if not isinstance(result, dict):
        return 0
    try:
        return get_mysql_store().store_search_result(result, search_job_key=job_id, source=source)
    except Exception:  # noqa: BLE001
        return 0


def normalize_hotel_name_correction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    hotel_id = str(payload.get("hotel_id") or payload.get("trip_hotel_id") or "").strip()[:64]
    city = normalize_city(payload.get("city") or payload.get("recommend_city")) or str(
        payload.get("city") or payload.get("recommend_city") or ""
    ).strip()
    suggested_name = finder._to_simplified_chinese(str(payload.get("suggested_name") or "").strip())
    current_name = finder._to_simplified_chinese(str(payload.get("current_name") or payload.get("hotel_name") or "").strip())
    original_name = str(payload.get("hotel_original_name") or "").strip()
    if not hotel_id:
        raise ReverseTravelFinderError("缺少酒店 ID，暂时无法提交名称修改。")
    if not suggested_name:
        raise ReverseTravelFinderError("请输入正确的简体中文酒店名。")
    if not finder._contains_chinese_text(suggested_name):
        raise ReverseTravelFinderError("酒店名需要包含中文。")
    if len(re.findall(r"[\u3400-\u9fff]", suggested_name)) < 4:
        raise ReverseTravelFinderError("酒店名太短，请填写完整的中文酒店名。")
    quality_item = {
        "hotel_id": hotel_id,
        "hotel_name": current_name,
        "hotel_original_name": original_name,
        "recommend_city": city,
    }
    if finder._is_generic_or_city_hotel_name(suggested_name, quality_item, city):
        raise ReverseTravelFinderError("这个名称看起来像城市名或通用词，请填写完整酒店中文名。")
    if current_name and suggested_name == current_name:
        raise ReverseTravelFinderError("新名称和当前显示名称相同，无需提交修改。")
    return {
        "hotel_id": hotel_id,
        "trip_hotel_id": str(payload.get("trip_hotel_id") or hotel_id).strip()[:64],
        "city_name_zh": city[:64],
        "current_hotel_name_zh": current_name[:255],
        "hotel_name_original": original_name[:255],
        "suggested_hotel_name_zh": suggested_name[:255],
        "area_name_zh": finder._to_simplified_chinese(str(payload.get("area_name") or "").strip())[:128],
        "detail_url": str(payload.get("detail_url") or "").strip()[:1024],
        "user_note": str(payload.get("user_note") or "").strip()[:500],
        "client_id": canonical_client_id(payload.get("client_id")),
    }


def normalize_hotel_area_correction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    hotel_id = str(payload.get("hotel_id") or payload.get("trip_hotel_id") or "").strip()[:64]
    city = normalize_city(payload.get("city") or payload.get("recommend_city")) or str(
        payload.get("city") or payload.get("recommend_city") or ""
    ).strip()
    current_area = finder._normalize_area_display_name(
        str(payload.get("current_area_name") or payload.get("area_name") or "").strip(),
        city,
    )
    suggested_area = finder._normalize_area_display_name(str(payload.get("suggested_area_name") or "").strip(), city)
    hotel_name = finder._to_simplified_chinese(str(payload.get("hotel_name") or "").strip())
    original_name = str(payload.get("hotel_original_name") or "").strip()
    if not hotel_id:
        raise ReverseTravelFinderError("缺少酒店 ID，暂时无法提交片区修改。")
    if not suggested_area:
        raise ReverseTravelFinderError("请输入具体的中文片区名称，例如“深圳光明片区”。")
    if not finder._contains_chinese_text(suggested_area):
        raise ReverseTravelFinderError("片区名称需要包含中文。")
    if finder._is_generic_area_name(suggested_area):
        raise ReverseTravelFinderError("这个片区名称过于泛化，请填写更具体的片区。")
    if current_area and suggested_area == current_area:
        raise ReverseTravelFinderError("新片区和当前片区相同，无需提交修改。")
    return {
        "hotel_id": hotel_id,
        "trip_hotel_id": str(payload.get("trip_hotel_id") or hotel_id).strip()[:64],
        "city_name_zh": city[:64],
        "hotel_name_zh": hotel_name[:255],
        "hotel_name_original": original_name[:255],
        "current_area_name_zh": current_area[:128],
        "suggested_area_name_zh": suggested_area[:128],
        "detail_url": str(payload.get("detail_url") or "").strip()[:1024],
        "user_note": str(payload.get("user_note") or "").strip()[:500],
        "client_id": canonical_client_id(payload.get("client_id")),
    }


def normalize_area_merge_correction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def normalized_city(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return finder._to_simplified_chinese(normalize_city(raw) or raw).strip()

    raw_source_areas = payload.get("source_areas")
    raw_hotels = payload.get("hotels")
    if not isinstance(raw_source_areas, list) or not isinstance(raw_hotels, list):
        raise ReverseTravelFinderError("请选择需要改名或合并的推荐片区。")

    fallback_city = normalized_city(payload.get("city") or payload.get("recommend_city"))
    source_areas: list[dict[str, Any]] = []
    source_city_names: set[str] = set()
    seen_area_keys: set[tuple[str, str]] = set()
    for area in raw_source_areas[:20]:
        if not isinstance(area, dict):
            continue
        area_city = normalized_city(area.get("recommend_city") or area.get("city") or fallback_city)
        area_name = finder._normalize_area_display_name(str(area.get("area_name") or "").strip(), area_city)
        if not area_name or finder._is_generic_area_name(area_name):
            continue
        key = (area_city, area_name)
        if key in seen_area_keys:
            continue
        seen_area_keys.add(key)
        if area_city:
            source_city_names.add(area_city)
        aliases = []
        for alias in [area_name, *(area.get("aliases") if isinstance(area.get("aliases"), list) else [])]:
            alias_name = finder._normalize_area_display_name(str(alias or "").strip(), area_city)
            if alias_name and not finder._is_generic_area_name(alias_name) and alias_name not in aliases:
                aliases.append(alias_name[:128])
        source_areas.append(
            {
                "area_name": area_name[:128],
                "recommend_city": area_city[:64],
                "aliases": aliases[:8],
                "hotel_count": int(area.get("hotel_count") or 0) if str(area.get("hotel_count") or "").isdigit() else 0,
            }
        )
    if len(source_areas) < 1:
        raise ReverseTravelFinderError("请选择至少一个具体片区。")
    if len(source_city_names) > 1:
        raise ReverseTravelFinderError("只能合并同一个推荐城市下的片区。")

    city = next(iter(source_city_names), fallback_city)
    suggested_area = finder._normalize_area_display_name(str(payload.get("suggested_area_name") or "").strip(), city)
    if not suggested_area:
        raise ReverseTravelFinderError("请输入新的中文片区名称。")
    if not finder._contains_chinese_text(suggested_area):
        raise ReverseTravelFinderError("新的片区名称需要包含中文。")
    if finder._is_generic_area_name(suggested_area):
        raise ReverseTravelFinderError("新的片区名称过于泛化，请填写更具体的片区。")
    if len(source_areas) == 1 and suggested_area == source_areas[0]["area_name"]:
        raise ReverseTravelFinderError("新的片区名称不能和原片区相同。")

    hotels: list[dict[str, Any]] = []
    hotel_cities: set[str] = set()
    seen_hotels: set[str] = set()
    for hotel in raw_hotels[:300]:
        if not isinstance(hotel, dict):
            continue
        hotel_id = str(hotel.get("hotel_id") or hotel.get("trip_hotel_id") or "").strip()[:64]
        if not hotel_id or hotel_id in seen_hotels:
            continue
        seen_hotels.add(hotel_id)
        hotel_city = normalized_city(hotel.get("city") or hotel.get("recommend_city") or city)
        if hotel_city:
            hotel_cities.add(hotel_city)
        current_area = finder._normalize_area_display_name(
            str(hotel.get("current_area_name") or hotel.get("area_name") or "").strip(),
            hotel_city or city,
        )
        hotels.append(
            {
                "hotel_id": hotel_id,
                "trip_hotel_id": str(hotel.get("trip_hotel_id") or hotel_id).strip()[:64],
                "city_name_zh": (hotel_city or city)[:64],
                "hotel_name_zh": finder._to_simplified_chinese(str(hotel.get("hotel_name") or "").strip())[:255],
                "hotel_name_original": str(hotel.get("hotel_original_name") or "").strip()[:255],
                "current_area_name_zh": current_area[:128],
                "detail_url": str(hotel.get("detail_url") or "").strip()[:1024],
            }
        )
    if not hotels:
        raise ReverseTravelFinderError("合并片区里没有可固定的酒店。")
    if len(hotel_cities) > 1 or (city and any(hotel_city and hotel_city != city for hotel_city in hotel_cities)):
        raise ReverseTravelFinderError("只能合并同一个推荐城市下的酒店。")
    return {
        "city_name_zh": city[:64],
        "suggested_area_name_zh": suggested_area[:128],
        "source_areas": source_areas,
        "hotels": hotels,
        "user_note": str(payload.get("user_note") or "").strip()[:500],
        "client_id": canonical_client_id(payload.get("client_id")),
    }


def cache_approved_hotel_name(correction: dict[str, Any]) -> None:
    hotel_id = str(correction.get("hotel_id") or "").strip()
    name = str(correction.get("suggested_hotel_name_zh") or "").strip()
    if not hotel_id or not name:
        return
    with finder._cache_lock:
        current = copy.deepcopy(finder._hotel_name_cache.get(hotel_id) or {})
        current["hotel_name"] = name
        current["hotel_name_simplified"] = name
        current["source"] = "人工审核中文名"
        current["domestic_checked_at"] = time.time()
        if correction.get("hotel_name_original") and not current.get("hotel_name_original"):
            current["hotel_name_original"] = str(correction.get("hotel_name_original") or "").strip()
        if correction.get("detail_url") and not current.get("detail_url"):
            current["detail_url"] = str(correction.get("detail_url") or "").strip()
        finder._hotel_name_cache[hotel_id] = finder._hotel_name_record_with_search_fields(current)
    finder._save_hotel_name_cache()


def cache_approved_hotel_area(correction: dict[str, Any]) -> None:
    hotel_id = str(correction.get("hotel_id") or "").strip()
    area_name = str(correction.get("suggested_area_name_zh") or "").strip()
    if not hotel_id or not area_name:
        return
    with finder._cache_lock:
        current = copy.deepcopy(finder._hotel_name_cache.get(hotel_id) or {})
        current["area_name"] = area_name
        current["area_source"] = "人工审核片区"
        finder._hotel_name_cache[hotel_id] = finder._hotel_name_record_with_search_fields(current)
    finder._save_hotel_name_cache()


def cache_approved_area_merge(correction: dict[str, Any]) -> None:
    area_name = str(correction.get("suggested_area_name_zh") or "").strip()
    hotels = correction.get("hotels") if isinstance(correction.get("hotels"), list) else []
    if not area_name or not hotels:
        return
    changed = False
    with finder._cache_lock:
        for hotel in hotels:
            if not isinstance(hotel, dict):
                continue
            hotel_id = str(hotel.get("hotel_id") or hotel.get("trip_hotel_id") or "").strip()
            if not hotel_id:
                continue
            current = copy.deepcopy(finder._hotel_name_cache.get(hotel_id) or {})
            current["area_name"] = area_name
            current["area_source"] = "人工审核片区"
            finder._hotel_name_cache[hotel_id] = finder._hotel_name_record_with_search_fields(current)
            changed = True
    if changed:
        finder._save_hotel_name_cache()


def hotel_name_correction_admin_payload() -> dict[str, Any]:
    try:
        return get_mysql_store().hotel_name_correction_summary()
    except Exception as exc:  # noqa: BLE001
        return {"pending": [], "recent": [], "disabled_reason": str(exc)}


def hotel_area_correction_admin_payload() -> dict[str, Any]:
    try:
        return get_mysql_store().hotel_area_correction_summary()
    except Exception as exc:  # noqa: BLE001
        return {"pending": [], "recent": [], "disabled_reason": str(exc)}


def area_merge_correction_admin_payload() -> dict[str, Any]:
    try:
        return get_mysql_store().area_merge_correction_summary()
    except Exception as exc:  # noqa: BLE001
        return {"pending": [], "recent": [], "disabled_reason": str(exc)}


def public_area_merge_correction(correction: dict[str, Any]) -> dict[str, Any]:
    hotels = []
    for hotel in correction.get("hotels") or []:
        if not isinstance(hotel, dict):
            continue
        hotel_id = str(hotel.get("hotel_id") or hotel.get("trip_hotel_id") or "").strip()
        if not hotel_id:
            continue
        hotels.append({"hotel_id": hotel_id, "trip_hotel_id": str(hotel.get("trip_hotel_id") or hotel_id).strip()})
    return {
        "id": correction.get("id"),
        "status": correction.get("status"),
        "city_name_zh": correction.get("city_name_zh"),
        "suggested_area_name_zh": correction.get("suggested_area_name_zh"),
        "source_areas": correction.get("source_areas") or [],
        "source_area_names": correction.get("source_area_names") or [],
        "hotels": hotels,
        "hotel_count": correction.get("hotel_count") or len(hotels),
        "created_at": correction.get("created_at") or "",
        "updated_at": correction.get("updated_at") or "",
        "reviewed_at": correction.get("reviewed_at") or "",
    }


def public_hotel_area_record(hotel_id: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "hotel_id": hotel_id,
        "area_name": record.get("area_name") or "",
        "review_id": record.get("review_id") or "",
    }


def public_hotel_name_record(hotel_id: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "hotel_id": hotel_id,
        "hotel_name": record.get("hotel_name") or "",
        "hotel_name_original": record.get("hotel_name_original") or "",
        "review_id": record.get("review_id") or "",
    }


def mysql_price_preview_for_search_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not parse_bool(payload.get("use_cache"), default=True):
        return None
    city = normalize_city(payload.get("city")) or (payload.get("city") or "").strip()
    holiday_code = (payload.get("holiday_code") or "").strip()
    if not city or not holiday_code:
        return None
    holiday = holiday_meta(holiday_code)
    if not holiday.get("check_in") or not holiday.get("check_out"):
        return None
    try:
        min_price_int, max_price_int = request_price_filters(payload)
        feature_filters = finder._normalize_feature_filters(
            payload.get("advanced_filter"),
            payload.get("pool_filter"),
            payload.get("child_facility_filter") or payload.get("children_pool_filter"),
        )
    except ReverseTravelFinderError:
        return None
    choices = get_mysql_store().latest_price_preview(
        city_name=city,
        holiday_code=holiday_code,
        check_in=holiday["check_in"],
        check_out=holiday["check_out"],
        min_price=min_price_int,
        max_price=max_price_int,
        advanced_filter=feature_filters.advanced,
        pool_filter=feature_filters.pool,
        child_facility_filter=feature_filters.child_facility,
    )
    if not choices:
        return None
    finder.prepare_cached_preview_hotel_names(choices, city)
    result = {
        "city": city,
        "holiday": holiday,
        "price_filter": {"min_price": min_price_int, "max_price": max_price_int},
        "feature_filters": feature_filters.to_response(),
        "comparison_windows": [],
        "area_recommendations": finder._build_area_recommendations(choices, city),
        "choices": choices,
        "cache": {
            "hit": True,
            "source": "mysql_price",
            "source_label": "MySQL价格缓存",
            "age_seconds": 0,
            "summary_label": f"MySQL价格缓存预览：先显示 {len(choices)} 家，后台刷新完整搜索",
        },
        "partial": {
            "stage": "mysql_price_preview",
            "message": "先显示 MySQL 价格缓存，后台正在刷新完整搜索和设施核验。",
            "preliminary": True,
            "displayed_choice_count": len(choices),
            "total_choice_count": len(choices),
        },
    }
    return result


def mysql_price_preview_for_nearby_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not parse_bool(payload.get("use_cache"), default=True):
        return None
    holiday_code = (payload.get("holiday_code") or "").strip()
    origin_city = normalize_city(payload.get("origin_city") or payload.get("city"))
    if not origin_city:
        return None
    if not holiday_code:
        return None
    try:
        min_price_int, max_price_int = request_price_filters(payload)
        limit = parse_optional_int(payload.get("nearby_limit"), "附近城市数量") or 4
        feature_filters = finder._normalize_feature_filters(
            payload.get("advanced_filter"),
            payload.get("pool_filter"),
            payload.get("child_facility_filter") or payload.get("children_pool_filter"),
        )
    except ReverseTravelFinderError:
        return None
    target_cities = nearby_cities_for(origin_city, limit=limit)
    if not target_cities:
        return None

    city_results: list[dict[str, Any]] = []
    first_success: dict[str, Any] | None = None
    cache_hits = 0
    for city in target_cities:
        city_payload = mysql_price_preview_for_search_payload(
            {
                **payload,
                "city": city,
                "advanced_filter": feature_filters.advanced,
                "pool_filter": feature_filters.pool,
                "child_facility_filter": feature_filters.child_facility,
            }
        )
        if city_payload is None:
            continue
        if first_success is None:
            first_success = city_payload
        city_result = build_nearby_city_result(city, city_payload)["city_result"]
        city_result["partial"] = True
        city_result["status"] = "mysql_price_preview"
        city_results.append(city_result)
        cache_hits += 1
    if not city_results:
        return None
    result = build_nearby_response(
        origin_city=origin_city,
        target_cities=target_cities,
        holiday_code=holiday_code,
        min_price_int=min_price_int,
        max_price_int=max_price_int,
        feature_filters_response=feature_filters.to_response(),
        first_success=first_success,
        city_results=city_results,
        cache_hits=cache_hits,
        live_count=0,
        error_count=0,
    )
    result["cache"] = {
        "hit": True,
        "source": "mysql_price",
        "source_label": "MySQL价格缓存",
        "age_seconds": 0,
        "summary_label": f"MySQL价格缓存预览：{cache_hits} 个城市先出结果，后台刷新完整搜索",
    }
    result["partial"] = {
        "stage": "mysql_price_preview",
        "message": "先显示 MySQL 价格缓存，后台正在刷新周边城市完整搜索。",
        "preliminary": True,
        "displayed_choice_count": len(result.get("choices") or []),
        "total_choice_count": len(result.get("choices") or []),
    }
    return result


def mysql_price_preview_for_job_start(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "search":
        return mysql_price_preview_for_search_payload(payload)
    if kind == "nearby":
        return mysql_price_preview_for_nearby_payload(payload)
    return None


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


def canonical_client_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return "".join(char for char in raw if char.isalnum() or char in {"-", "_"})[:80]


def request_client_id() -> str:
    return canonical_client_id(request.args.get("client_id") or request.headers.get("X-Reverse-Travel-Client"))


def canonical_job_signature(kind: str, payload: dict[str, Any]) -> str | None:
    if kind not in {"search", "nearby"}:
        return None

    holiday_code = str(payload.get("holiday_code") or "").strip()
    client_id = canonical_client_id(payload.get("client_id"))
    child_filter = payload.get("child_facility_filter") or payload.get("children_pool_filter")
    base: dict[str, Any] = {
        "version": 1,
        "kind": kind,
        "holiday_code": holiday_code,
        "client_id": client_id,
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


def job_version(job: dict[str, Any]) -> int:
    try:
        return int(job.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def bump_job_version(job: dict[str, Any]) -> int:
    version = job_version(job) + 1
    job["version"] = version
    return version


def optional_since_version(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def compact_city_results_for_delta(city_results: Any) -> list[dict[str, Any]]:
    if not isinstance(city_results, list):
        return []
    compacted: list[dict[str, Any]] = []
    for item in city_results:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key not in {"choices", "area_recommendations", "result"}
            }
        )
    return compacted


def result_delta_base(result: dict[str, Any]) -> dict[str, Any]:
    meta = copy.deepcopy(result)
    meta.pop("choices", None)
    if isinstance(meta.get("city_results"), list):
        meta["city_results"] = compact_city_results_for_delta(meta.get("city_results"))
    return meta


def stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def build_result_delta(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(current, dict):
        return None
    previous_choices = previous.get("choices") if isinstance(previous, dict) else []
    current_choices = current.get("choices") if isinstance(current.get("choices"), list) else []
    previous_by_key: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(previous_choices or []):
        if isinstance(item, dict):
            previous_by_key[choice_identity(item, index)] = item

    current_keys: list[str] = []
    current_key_set: set[str] = set()
    upserts: list[dict[str, Any]] = []
    for index, item in enumerate(current_choices):
        if not isinstance(item, dict):
            continue
        key = choice_identity(item, index)
        current_keys.append(key)
        current_key_set.add(key)
        previous_item = previous_by_key.get(key)
        if previous_item is None or stable_json(previous_item) != stable_json(item):
            changed = copy.deepcopy(item)
            changed["_choice_key"] = key
            upserts.append(changed)

    removed = [key for key in previous_by_key if key not in current_key_set]
    return {
        "meta": result_delta_base(current),
        "choices_upsert": upserts,
        "choices_removed": removed,
        "choice_order": current_keys,
        "choice_count": len(current_keys),
    }


def append_job_change(job: dict[str, Any], version: int, field_name: str, previous: Any, current: Any) -> None:
    delta = build_result_delta(previous if isinstance(previous, dict) else None, current if isinstance(current, dict) else None)
    if not delta:
        return
    changes = list(job.get("changes") or [])
    changes.append({"version": version, field_name: delta})
    job["changes"] = changes[-60:]


def aggregate_result_delta(job: dict[str, Any], since_version: int, *, prefer_result: bool) -> dict[str, Any] | None:
    changes = [
        change
        for change in (job.get("changes") or [])
        if int(change.get("version") or 0) > since_version
    ]
    if not changes:
        return None

    meta: dict[str, Any] = {}
    upsert_by_key: dict[str, dict[str, Any]] = {}
    removed: set[str] = set()
    choice_order: list[str] = []
    for change in changes:
        delta = None
        if prefer_result:
            delta = change.get("result_delta") or change.get("partial_result_delta")
        else:
            delta = change.get("partial_result_delta")
        if not isinstance(delta, dict):
            continue
        meta.update(copy.deepcopy(delta.get("meta") or {}))
        for key in delta.get("choices_removed") or []:
            text_key = str(key)
            removed.add(text_key)
            upsert_by_key.pop(text_key, None)
        for item in delta.get("choices_upsert") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("_choice_key") or choice_identity(item))
            upsert_by_key[key] = copy.deepcopy(item)
            removed.discard(key)
        if isinstance(delta.get("choice_order"), list):
            choice_order = [str(key) for key in delta["choice_order"]]

    if not meta and not upsert_by_key and not removed and not choice_order:
        return None
    return {
        "meta": meta,
        "choices_upsert": list(upsert_by_key.values()),
        "choices_removed": sorted(removed),
        "choice_order": choice_order,
        "choice_count": len(choice_order),
    }


def public_job(job: dict[str, Any], *, since_version: int | None = None) -> dict[str, Any]:
    version = job_version(job)
    unchanged = since_version is not None and version <= since_version
    data = {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "version": version,
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if unchanged:
        data["unchanged"] = True
        return data
    if job.get("result") is not None:
        result_delta = aggregate_result_delta(job, since_version, prefer_result=True) if since_version is not None else None
        if result_delta is not None:
            data["result_delta"] = result_delta
        else:
            data["result"] = job["result"]
    if job.get("error"):
        data["error"] = job["error"]
    if job.get("status_code"):
        data["status_code"] = job["status_code"]
    if job.get("progress"):
        data["progress"] = job["progress"]
    if job.get("progress_events"):
        data["progress_events"] = job["progress_events"]
    if job.get("stage_timings"):
        data["stage_timings"] = job["stage_timings"]
    if job.get("partial_result") and (job.get("result") is None or since_version is None):
        partial_delta = aggregate_result_delta(job, since_version, prefer_result=False) if since_version is not None else None
        if partial_delta is not None:
            data["partial_result_delta"] = partial_delta
        else:
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


def record_job_timing(job: dict[str, Any], progress: dict[str, Any]) -> None:
    stage = str(progress.get("stage") or "").strip()
    if not stage:
        return
    started = float(job.get("created_ts") or time.time())
    elapsed = max(0, round(time.time() - started, 1))
    timings = list(job.get("stage_timings") or [])
    item = {
        "stage": stage,
        "message": progress.get("message") or "",
        "elapsed_seconds": elapsed,
    }
    if progress.get("percent") not in ("", None):
        item["percent"] = progress["percent"]
    if timings and timings[-1].get("stage") == stage:
        timings[-1] = item
    else:
        timings.append(item)
    job["stage_timings"] = timings[-24:]


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
    record_job_timing(job, progress)


def is_local_request() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


def is_loopback_host(host: str) -> bool:
    raw_host = (host or "").strip()
    if raw_host.startswith("["):
        host_name = raw_host[1:].split("]", 1)[0].lower()
    else:
        host_name = raw_host.split(":", 1)[0].lower()
    return host_name in {"127.0.0.1", "::1", "localhost"} or host_name.startswith("127.")


def is_admin_request() -> bool:
    if not ADMIN_TOKEN:
        return True
    token = str(request.args.get("token") or request.headers.get("X-Admin-Token") or "").strip()
    return hmac.compare_digest(token, ADMIN_TOKEN)


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
        persist_prewarm_state(prewarm_state)


def prewarm_holiday_name(holiday_code: str, result: dict[str, Any] | None = None) -> str:
    holiday = (result or {}).get("holiday") or {}
    name = str(holiday.get("name") or "").strip()
    if name:
        return name
    if "::" in holiday_code:
        return holiday_code.split("::", 1)[1]
    return holiday_code


def prewarm_target_record(
    *,
    index: int,
    total: int,
    city: str,
    holiday_code: str,
    profile_name: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    profile = PREWARM_FILTER_PROFILES.get(profile_name) or PREWARM_FILTER_PROFILES["default"]
    choices = (result or {}).get("choices") if isinstance(result, dict) else []
    cache = (result or {}).get("cache") if isinstance(result, dict) else {}
    return {
        "index": index,
        "total": total,
        "city": city,
        "holiday_code": holiday_code,
        "holiday_name": prewarm_holiday_name(holiday_code, result),
        "profile": profile_name,
        "profile_label": profile["label"],
        "status": status,
        "cache_source": (cache or {}).get("source") or "",
        "cache_hit": bool((cache or {}).get("hit")),
        "choice_count": len(choices or []),
        "error": error,
        "completed_at": utc_timestamp(),
    }


def append_prewarm_target_result(record: dict[str, Any]) -> None:
    with prewarm_lock:
        records = list(prewarm_state.get("target_results") or [])
        records.append(record)
        prewarm_state["target_results"] = records[-300:]
        prewarm_state["target_result_count"] = len(prewarm_state["target_results"])
        persist_prewarm_state(prewarm_state)


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
                "run_date": time.strftime("%Y-%m-%d", time.localtime()),
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
                "target_results": [],
                "target_result_count": 0,
            }
        )
        append_prewarm_event(prewarm_state, "缓存预热已开始", total=total)
        persist_prewarm_state(prewarm_state)

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
            append_prewarm_target_result(
                prewarm_target_record(
                    index=index,
                    total=total,
                    city=city,
                    holiday_code=holiday_code,
                    profile_name=profile_name,
                    status="failed",
                    error=str(exc),
                )
            )
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
            target_status = "succeeded"
            if cache.get("hit"):
                cache_hits += 1
                target_status = "cache_hit"
            elif cache.get("source") == "live":
                live_count += 1
                target_status = "live"
            append_prewarm_target_result(
                prewarm_target_record(
                    index=index,
                    total=total,
                    city=city,
                    holiday_code=holiday_code,
                    profile_name=profile_name,
                    status=target_status,
                    result=result,
                )
            )
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
        for skipped_index, (city, holiday_code, profile_name) in enumerate(targets[completed_count:], start=completed_count + 1):
            append_prewarm_target_result(
                prewarm_target_record(
                    index=skipped_index,
                    total=total,
                    city=city,
                    holiday_code=holiday_code,
                    profile_name=profile_name,
                    status="skipped",
                )
            )
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
                "target_results": [],
                "target_result_count": 0,
            }
        )
        persist_prewarm_state(prewarm_state)
    prewarm_executor.submit(run_cache_prewarm, copy.deepcopy(config))
    return public_prewarm_state(), 202


def daily_prewarm_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(config or {})
    holiday_limit = parse_optional_int(config.get("holiday_limit"), "每日预热假期数量")
    if holiday_limit is None:
        holiday_limit = 2
    holidays = finder.list_holidays()[: max(1, holiday_limit)]
    city_limit = parse_optional_int(config.get("city_limit"), "每日预热城市数量")
    if city_limit is None:
        city_limit = DAILY_PREWARM_CITY_LIMIT
    return {
        "cities": prewarm_city_list(limit=max(1, city_limit)),
        "holiday_codes": [item["code"] for item in holidays],
        "profiles": normalize_prewarm_profiles(config.get("profiles") or ["quality"]),
        "delay_seconds": str(config.get("delay_seconds", "1")),
        "max_runtime_seconds": str(config.get("max_runtime_seconds", "")),
        "preset": "daily",
    }


def start_daily_cache_prewarm(config: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    return start_cache_prewarm(daily_prewarm_config(config))


def cached_search_records(limit: int = DAILY_RECOMMENDATION_SCAN_LIMIT) -> list[dict[str, Any]]:
    search_dir = finder.cache_dir / "search"
    try:
        paths = sorted(
            (entry for entry in search_dir.iterdir() if entry.is_file() and entry.suffix == ".json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for path in paths[:limit]:
        record = finder._read_json_file(path)
        if not isinstance(record, dict):
            continue
        result = record.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
            continue
        records.append(record)
    return records


def _clean_daily_image_url(value: Any) -> str:
    text = html.unescape(str(value or "").strip()).replace("\\/", "/").rstrip("\\")
    if not text or len(text) > 2048 or text.startswith("data:"):
        return ""
    if text.startswith("//"):
        text = f"https:{text}"
    if not re.match(r"https?://", text, flags=re.IGNORECASE):
        return ""
    return text


def _upgrade_trip_daily_image_url(value: str) -> str:
    return re.sub(
        r"_R_\d{2,4}_\d{2,4}_R5_D\.jpg_\.webp(?=($|[?#]))",
        "_R_960_660_R5_D.jpg_.webp",
        value,
        flags=re.IGNORECASE,
    )


def _daily_image_quality_score(value: str) -> int:
    text = _clean_daily_image_url(value)
    if not text:
        return 0
    lowered = text.lower()
    blocked_tokens = (
        "sprite",
        "icon",
        "iconfont",
        "logo",
        "avatar",
        "placeholder",
        "default",
        "loading",
        "blank",
        "map",
        "marker",
        "badge",
        "banner",
        "qrcode",
        "qr-code",
        "panda",
        "animal",
    )
    if any(token in lowered for token in blocked_tokens):
        return 0
    if not re.search(r"\.(?:jpg|jpeg|webp)(?:[?#].*)?$", lowered):
        return 0
    if re.search(r"(?<!\d)(?:[1-3]?\d{2})_(?:[1-3]?\d{2})(?!\d)", lowered):
        return 0
    score = 45
    host = urlparse(text).netloc.lower()
    if any(token in host for token in ("tripcdn.com", "c-ctrip.com", "ctrip.com")):
        score += 25
    if any(token in lowered for token in ("hotel", "photo", "cover", "1mc", "real-hotel")):
        score += 10
    if re.search(r"(?<!\d)(?:[6-9]\d{2}|1\d{3,})_(?:[4-9]\d{2}|1\d{3,})(?!\d)", lowered):
        score += 15
    return score


def normalize_daily_image_url(value: Any) -> str:
    text = _clean_daily_image_url(value)
    if not text:
        return ""
    text = _upgrade_trip_daily_image_url(text)
    if _daily_image_quality_score(text) <= 0:
        return ""
    return text


def best_daily_image_url(candidates: list[Any]) -> str:
    best_url = ""
    best_score = 0
    for candidate in candidates:
        image_url = normalize_daily_image_url(candidate)
        if not image_url:
            continue
        score = _daily_image_quality_score(image_url)
        if score > best_score:
            best_url = image_url
            best_score = score
    return best_url


def choice_image_url(choice: dict[str, Any]) -> str:
    for key in (
        "hotel_image_url",
        "cover_image_url",
        "cover_url",
        "photo_url",
        "picture_url",
        "image_url",
        "thumbnail_url",
    ):
        image_url = normalize_daily_image_url(choice.get(key))
        if image_url:
            return image_url
    return ""


def truthy_feature_value(value: Any) -> bool:
    return value is True or value in (1, "1", "true", "True", "yes", "YES", "是")


def is_daily_quality_choice(choice: dict[str, Any]) -> bool:
    return (
        truthy_feature_value(choice.get("is_advanced"))
        and truthy_feature_value(choice.get("has_pool"))
        and truthy_feature_value(choice.get("has_child_facility"))
    )


def fetch_daily_hotel_image_url(detail_url: str) -> str:
    url = str(detail_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host != "trip.com" and not host.endswith(".trip.com"):
        return ""

    cache_key = url.split("#", 1)[0]
    now = time.time()
    with daily_image_lock:
        cached = daily_image_cache.get(cache_key)
        if cached and now - cached[0] < DAILY_IMAGE_CACHE_TTL_SECONDS:
            return cached[1]

    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    image_url = ""
    try:
        with urlopen(request, timeout=1.5) as response:
            page = response.read(1_500_000).decode("utf-8", errors="ignore")
    except Exception:
        page = ""
    if page:
        candidates: list[str] = []
        patterns = (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'"(?:image|imageUrl|coverImage|coverUrl|picUrl)"\s*:\s*"([^"]+)"',
        )
        for pattern in patterns:
            candidates.extend(match.group(1) for match in re.finditer(pattern, page, flags=re.IGNORECASE))
        candidates.extend(re.findall(r'(?:https?:)?//[^"\'<>\s]+(?:tripcdn|dimg)[^"\'<>\s]+', page))
        image_url = best_daily_image_url(candidates)

    with daily_image_lock:
        daily_image_cache[cache_key] = (now, image_url)
    return image_url


def daily_hotel_display_image_url(hotel: dict[str, Any]) -> str:
    detail_image_url = normalize_daily_image_url(fetch_daily_hotel_image_url(str(hotel.get("detail_url") or "")))
    return detail_image_url or choice_image_url(hotel)


def daily_recommendation_payload() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    now = time.time()
    for record in cached_search_records():
        result = record.get("result") or {}
        cache_key = record.get("cache_key") if isinstance(record.get("cache_key"), list) else []
        try:
            created_at = float(record.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        for choice in result.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if not choice.get("hotel_name") or not choice.get("detail_url"):
                continue
            if not is_daily_quality_choice(choice):
                continue
            try:
                diff = int(choice.get("price_diff_nightly") or 0)
                holiday_value = int(choice.get("holiday_avg_nightly_tax_total_value") or 0)
            except (TypeError, ValueError):
                continue
            display_name = finder._to_simplified_chinese(str(choice.get("hotel_name") or ""))
            has_chinese_name = finder._contains_chinese_text(display_name)
            image_url = choice_image_url(choice)
            candidates.append(
                {
                    "city": finder._to_simplified_chinese(str(result.get("city") or "")),
                    "holiday": result.get("holiday") or {},
                    "feature_filters": result.get("feature_filters") or {},
                    "cache_key": cache_key,
                    "cache_created_at": finder._format_timestamp(created_at) if created_at else "",
                    "cache_age_seconds": max(0, round(now - created_at)) if created_at else None,
                    "hotel": choice,
                    "has_chinese_name": has_chinese_name,
                    "image_url": image_url,
                    "score": (diff, holiday_value, str(choice.get("hotel_id") or choice.get("hotel_name") or "")),
                }
            )

    if not candidates:
        return {
            "available": False,
            "message": "暂无高级、有泳池、有儿童设施的预热缓存。半夜预热完成后会自动显示每日推荐。",
        }

    candidates.sort(key=lambda item: item["score"])
    chinese_candidates = [item for item in candidates if item.get("has_chinese_name")]
    quality_pool = (chinese_candidates or candidates)[: min(20, len(chinese_candidates or candidates))]
    pool = [item for item in quality_pool if item.get("image_url")] or quality_pool
    day_key = time.strftime("%Y-%m-%d", time.localtime())
    index = int(hashlib.sha256(day_key.encode("utf-8")).hexdigest(), 16) % len(pool)
    selected = pool[index]
    hotel = copy.deepcopy(selected["hotel"])
    finder._apply_cached_hotel_names_to_choices([hotel], str(selected.get("city") or ""))
    finder._refresh_choice_area_names([hotel], str(selected.get("city") or ""))
    display_name = finder._to_simplified_chinese(str(hotel.get("hotel_name") or ""))
    original_name = finder._to_simplified_chinese(str(hotel.get("hotel_original_name") or ""))
    image_url = daily_hotel_display_image_url(hotel)
    return {
        "available": True,
        "date": day_key,
        "city": selected["city"],
        "holiday": selected["holiday"],
        "feature_filters": selected["feature_filters"],
        "cache": {
            "created_at": selected["cache_created_at"],
            "age_seconds": selected["cache_age_seconds"],
            "source_label": "预热缓存",
        },
        "hotel": {
            "hotel_id": hotel.get("hotel_id") or "",
            "hotel_name": display_name or hotel.get("hotel_name") or "",
            "hotel_original_name": original_name or hotel.get("hotel_original_name") or "",
            "hotel_name_source": hotel.get("hotel_name_source") or "",
            "detail_url": hotel.get("detail_url") or "",
            "image_url": image_url,
            "area_name": finder._to_simplified_chinese(str(hotel.get("area_name") or "")),
            "is_advanced": hotel.get("is_advanced"),
            "has_pool": hotel.get("has_pool"),
            "has_child_facility": hotel.get("has_child_facility"),
            "room_type_label": hotel.get("room_type_label") or "",
            "holiday_avg_nightly_tax_total_price": hotel.get("holiday_avg_nightly_tax_total_price") or "",
            "holiday_tax_total_price": hotel.get("holiday_tax_total_price") or "",
            "comparison_average_nightly_tax_total_price": hotel.get("comparison_average_nightly_tax_total_price") or "",
            "comparison_lowest_nightly_tax_total_price": hotel.get("comparison_lowest_nightly_tax_total_price") or "",
            "comparison_lowest_check_in": hotel.get("comparison_lowest_check_in") or "",
            "comparison_lowest_check_out": hotel.get("comparison_lowest_check_out") or "",
            "comparison_sample_count": hotel.get("comparison_sample_count") or 0,
            "price_diff_nightly": hotel.get("price_diff_nightly") or 0,
            "price_diff_nightly_text": hotel.get("price_diff_nightly_text") or "",
        },
    }


def bytes_to_mb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / 1024 / 1024, 1)


def process_memory_info() -> dict[str, Any]:
    rss_bytes: int | None = None
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        raw = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if raw:
            rss_bytes = int(raw) * 1024
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        rss_bytes = None

    peak_bytes: int | None = None
    try:
        peak_raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        peak_bytes = peak_raw if sys.platform == "darwin" else peak_raw * 1024
    except (OSError, ValueError):
        peak_bytes = None

    return {
        "rss_bytes": rss_bytes,
        "rss_mb": bytes_to_mb(rss_bytes),
        "peak_rss_bytes": peak_bytes,
        "peak_rss_mb": bytes_to_mb(peak_bytes),
    }


def summarize_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "city",
        "origin_city",
        "holiday_code",
        "nearby_limit",
        "min_price",
        "max_price",
        "advanced_filter",
        "pool_filter",
        "child_facility_filter",
        "use_cache",
        "cache_only",
    ):
        value = payload.get(key)
        if value not in ("", None):
            summary[key] = value
    choices = payload.get("choices")
    if isinstance(choices, list):
        summary["choices_count"] = len(choices)
    areas = payload.get("area_recommendations")
    if isinstance(areas, list):
        summary["area_count"] = len(areas)
    return summary


def summarize_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    summary: dict[str, Any] = {}
    choices = result.get("choices")
    if isinstance(choices, list):
        summary["choices_count"] = len(choices)
    city_results = result.get("city_results")
    if isinstance(city_results, list):
        summary["city_count"] = len(city_results)
    areas = result.get("area_recommendations")
    if isinstance(areas, list):
        summary["area_count"] = len(areas)
    cache = result.get("cache")
    if isinstance(cache, dict):
        summary["cache"] = {
            key: cache.get(key)
            for key in ("source", "hit", "stale", "summary_label")
            if cache.get(key) not in ("", None)
        }
    return summary


def job_export_result(job: dict[str, Any]) -> tuple[dict[str, Any], str]:
    source = "result" if isinstance(job.get("result"), dict) else "partial_result"
    raw_result = job.get(source)
    result = copy.deepcopy(raw_result) if isinstance(raw_result, dict) else {}
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    city_name = str(
        result.get("city")
        or payload.get("city")
        or payload.get("origin_city")
        or ""
    ).strip()
    choices = result.get("choices") if isinstance(result.get("choices"), list) else []
    if choices:
        try:
            finder._apply_cached_hotel_names_to_choices(choices, city_name)
            finder._refresh_choice_area_names(choices, city_name)
            result["area_recommendations"] = finder._build_area_recommendations(choices, city_name)
        except Exception:  # noqa: BLE001
            pass
        result["choices"] = choices
    return result, source


def job_has_exportable_result(job: dict[str, Any]) -> bool:
    for key in ("result", "partial_result"):
        result = job.get(key)
        if isinstance(result, dict) and isinstance(result.get("choices"), list) and result.get("choices"):
            return True
    return job.get("kind") in {"search", "nearby", "coverage"}


def job_pdf_url(job: dict[str, Any]) -> str:
    job_id = str(job.get("job_id") or "").strip()
    return f"/api/admin/jobs/{job_id}/pdf" if job_id and job_has_exportable_result(job) else ""


def admin_job_summary(job: dict[str, Any], now: float) -> dict[str, Any]:
    partial = job.get("partial_result")
    result = job.get("result")
    client_id = canonical_client_id(job.get("client_id"))
    data = {
        "job_id": job.get("job_id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "client_id": client_id[:12],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "age_seconds": max(0, round(now - float(job.get("created_ts") or now))),
        "idle_seconds": max(0, round(now - float(job.get("updated_ts") or now))),
        "progress": job.get("progress") or {},
        "payload": summarize_job_payload(job.get("payload") or {}),
        "partial": summarize_result(partial),
        "result": summarize_result(result),
        "stage_timings": list(job.get("stage_timings") or [])[-8:],
    }
    if job.get("error"):
        data["error"] = job.get("error")
    if job.get("status_code"):
        data["status_code"] = job.get("status_code")
    pdf_url = job_pdf_url(job)
    data["pdf_available"] = bool(pdf_url)
    if pdf_url:
        data["pdf_url"] = pdf_url
    return data


def admin_status_payload() -> dict[str, Any]:
    cleanup_jobs()
    now = time.time()
    with job_lock:
        job_items = [copy.deepcopy(job) for job in jobs.values()]
    job_items.sort(key=lambda item: float(item.get("updated_ts") or 0), reverse=True)
    counts: dict[str, int] = {}
    for job in job_items:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    active_jobs = [job for job in job_items if job.get("status") in {"queued", "running"}]
    return {
        "generated_at": utc_timestamp(),
        "process": {
            "pid": os.getpid(),
            "job_workers": JOB_WORKERS,
            "nearby_city_workers": NEARBY_CITY_WORKERS,
            "tracked_jobs": len(job_items),
        },
        "memory": process_memory_info(),
        "summary": {
            "active": len(active_jobs),
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "total": len(job_items),
        },
        "jobs": {
            "active": [admin_job_summary(job, now) for job in active_jobs[:20]],
            "recent": [admin_job_summary(job, now) for job in job_items[:40]],
        },
        "prewarm": public_prewarm_state(),
        "hotel_name_corrections": hotel_name_correction_admin_payload(),
        "hotel_area_corrections": hotel_area_correction_admin_payload(),
        "area_merge_corrections": area_merge_correction_admin_payload(),
    }


def html_escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def job_status_label(status: Any) -> str:
    return {
        "queued": "排队中",
        "running": "运行中",
        "succeeded": "成功",
        "failed": "失败",
    }.get(str(status or "").lower(), str(status or "-"))


def job_kind_label(kind: Any) -> str:
    return {
        "search": "城市搜索",
        "nearby": "周边搜索",
        "coverage": "行政区补充",
        "hotel_names": "酒店名刷新",
        "areas": "片区刷新",
    }.get(str(kind or ""), str(kind or "-"))


def job_export_holiday(job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    holiday = result.get("holiday")
    if isinstance(holiday, dict) and holiday:
        return holiday
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    holiday_code = str(payload.get("holiday_code") or "").strip()
    if not holiday_code:
        return {}
    try:
        return holiday_meta(holiday_code)
    except Exception:  # noqa: BLE001
        return {"code": holiday_code}


def job_report_title(job: dict[str, Any], result: dict[str, Any]) -> str:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    city = (
        result.get("city")
        or payload.get("city")
        or payload.get("origin_city")
        or ""
    )
    holiday = job_export_holiday(job, result)
    parts = ["反向旅游搜索任务"]
    if city:
        parts.append(str(city))
    if holiday.get("name"):
        parts.append(str(holiday.get("name")))
    elif holiday.get("code"):
        parts.append(str(holiday.get("code")))
    return " - ".join(parts)


def job_report_filter_text(job: dict[str, Any], result: dict[str, Any]) -> str:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    feature_filters = result.get("feature_filters") if isinstance(result.get("feature_filters"), dict) else {}
    price_filter = result.get("price_filter") if isinstance(result.get("price_filter"), dict) else {}
    parts: list[str] = []
    for key in ("advanced", "pool", "child_facility"):
        item = feature_filters.get(key)
        if isinstance(item, dict) and item.get("label"):
            parts.append(f"{item.get('name') or key}：{item.get('label')}")
    min_price = payload.get("min_price") or price_filter.get("min_price")
    max_price = payload.get("max_price") or price_filter.get("max_price")
    if min_price not in ("", None) or max_price not in ("", None):
        parts.append(f"价格：CNY {min_price or '不限'} - {max_price or '不限'}")
    if payload.get("nearby_limit"):
        parts.append(f"附近城市：{payload.get('nearby_limit')} 个")
    return "，".join(parts) or "全部结果"


def job_report_filename(job: dict[str, Any], result: dict[str, Any]) -> str:
    raw = f"{job_report_title(job, result)}-{str(job.get('job_id') or '')[:8]}.pdf"
    normalized = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", raw, flags=re.UNICODE).strip("-")
    return normalized or "reverse-travel-job.pdf"


def job_area_rows_html(areas: list[dict[str, Any]]) -> str:
    if not areas:
        return '<tr><td colspan="6" class="empty">暂无推荐片区</td></tr>'
    return "\n".join(
        f"""
        <tr>
          <td>{html_escape(item.get("area_name") or "-")}</td>
          <td>{html_escape(item.get("recommend_city") or item.get("city_label") or "-")}</td>
          <td class="num">{html_escape(item.get("hotel_count") or 0)}</td>
          <td class="num">{html_escape(item.get("lower_price_hotel_count") or 0)}</td>
          <td class="num">{html_escape(item.get("average_holiday_nightly_tax_total_price") or "-")}</td>
          <td class="num diff">{html_escape(item.get("average_price_diff_nightly_text") or "-")}</td>
        </tr>
        """
        for item in areas
    )


def job_choice_rows_html(choices: list[dict[str, Any]]) -> str:
    if not choices:
        return '<tr><td colspan="7" class="empty">暂无酒店结果。任务还在排队或尚未产出可展示结果时，会先显示这条记录。</td></tr>'
    rows = []
    for index, item in enumerate(choices, start=1):
        hotel_name = item.get("hotel_name_simplified") or item.get("hotel_name") or item.get("hotel_original_name") or "-"
        area = " · ".join(
            str(value)
            for value in (item.get("recommend_city"), item.get("area_name"))
            if value
        ) or "-"
        detail_url = str(item.get("detail_url") or "").strip()
        detail_html = (
            f'<div class="muted"><a href="{html_escape(detail_url)}">{html_escape(detail_url)}</a></div>'
            if detail_url
            else ""
        )
        rows.append(
            f"""
            <tr>
              <td class="num">{index}</td>
              <td><strong>{html_escape(hotel_name)}</strong>{detail_html}</td>
              <td>{html_escape(area)}</td>
              <td>{html_escape(item.get("room_type_label") or "-")}</td>
              <td class="num">{html_escape(item.get("holiday_avg_nightly_tax_total_price") or "-")}</td>
              <td class="num">{html_escape(item.get("comparison_average_nightly_tax_total_price") or "-")}</td>
              <td class="num diff">{html_escape(item.get("price_diff_nightly_text") or "-")}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def build_job_pdf_html(job: dict[str, Any]) -> str:
    result, source = job_export_result(job)
    choices = result.get("choices") if isinstance(result.get("choices"), list) else []
    areas = result.get("area_recommendations") if isinstance(result.get("area_recommendations"), list) else []
    holiday = job_export_holiday(job, result)
    title = job_report_title(job, result)
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    data_source = "最终结果" if source == "result" else "当前已显示结果"
    comparison_count = len(result.get("comparison_windows") or []) if isinstance(result.get("comparison_windows"), list) else 0
    events = job.get("progress_events") if isinstance(job.get("progress_events"), list) else []
    event_rows = "\n".join(
        f"<tr><td>{html_escape(event.get('time') or '-')}</td><td>{html_escape(event.get('message') or '-')}</td></tr>"
        for event in events[-8:]
        if isinstance(event, dict)
    )
    event_section = f"""
      <h2>任务进度</h2>
      <table>
        <thead><tr><th style="width: 180px;">时间</th><th>进度</th></tr></thead>
        <tbody>{event_rows or '<tr><td colspan="2" class="empty">暂无进度记录</td></tr>'}</tbody>
      </table>
    """
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{html_escape(title)}</title>
  <style>
    @page {{ size: A4; margin: 14mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #152336;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif;
      font-size: 12px;
      line-height: 1.5;
    }}
    h1 {{ margin: 0 0 8px; font-size: 22px; letter-spacing: 0; }}
    h2 {{ margin: 20px 0 8px; font-size: 15px; letter-spacing: 0; }}
    a {{ color: #1657d8; text-decoration: none; word-break: break-all; }}
    .summary {{ color: #405066; margin: 8px 0 14px; }}
    .meta {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 14px 0; }}
    .stat {{ border: 1px solid #dce6ef; border-radius: 6px; padding: 8px; min-height: 56px; }}
    .k {{ color: #66758a; font-size: 10px; font-weight: 700; }}
    .v {{ margin-top: 3px; font-size: 13px; font-weight: 800; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; page-break-inside: auto; }}
    tr {{ page-break-inside: avoid; page-break-after: auto; }}
    th, td {{ border: 1px solid #dce6ef; padding: 7px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f6fb; color: #405066; font-size: 11px; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .diff {{ font-weight: 800; }}
    .muted {{ color: #66758a; font-size: 10px; margin-top: 3px; overflow-wrap: anywhere; }}
    .empty {{ color: #66758a; }}
    @media print {{ body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }} }}
  </style>
</head>
<body>
  <h1>{html_escape(title)}</h1>
  <div class="summary">
    {html_escape(data_source)} · {html_escape(job_kind_label(job.get("kind")))} · {html_escape(job_report_filter_text(job, result))}
  </div>
  <div class="meta">
    <div class="stat"><div class="k">导出时间</div><div class="v">{html_escape(generated_at)}</div></div>
    <div class="stat"><div class="k">任务状态</div><div class="v">{html_escape(job_status_label(job.get("status")))}</div></div>
    <div class="stat"><div class="k">酒店数量</div><div class="v">{html_escape(len(choices))} 家</div></div>
    <div class="stat"><div class="k">推荐片区</div><div class="v">{html_escape(len(areas))} 个</div></div>
    <div class="stat"><div class="k">任务 ID</div><div class="v">{html_escape(job.get("job_id") or "-")}</div></div>
    <div class="stat"><div class="k">假期</div><div class="v">{html_escape(holiday.get("name") or holiday.get("code") or "-")}</div></div>
    <div class="stat"><div class="k">更新时间</div><div class="v">{html_escape(job.get("updated_at") or "-")}</div></div>
    <div class="stat"><div class="k">代表时段</div><div class="v">{html_escape(comparison_count)} 个</div></div>
  </div>
  <div class="summary">{html_escape(progress.get("message") or "")}</div>

  <h2>推荐旅游区域</h2>
  <table>
    <thead>
      <tr>
        <th>片区</th>
        <th>城市</th>
        <th>酒店数</th>
        <th>更低</th>
        <th>假期均价</th>
        <th>每晚差额</th>
      </tr>
    </thead>
    <tbody>{job_area_rows_html(areas)}</tbody>
  </table>

  <h2>推荐酒店</h2>
  <table>
    <thead>
      <tr>
        <th style="width: 36px;">#</th>
        <th>酒店</th>
        <th>片区</th>
        <th>房型</th>
        <th>假期每晚含税</th>
        <th>代表时段每晚含税</th>
        <th>每晚差额</th>
      </tr>
    </thead>
    <tbody>{job_choice_rows_html(choices)}</tbody>
  </table>
  {event_section}
</body>
</html>"""


def render_pdf_bytes(html_text: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1600})
            page.set_content(html_text, wait_until="load")
            return page.pdf(format="A4", print_background=True)
        finally:
            browser.close()


def batch_review_pending_corrections(kind: str, action: str, reviewer_note: str = "") -> tuple[dict[str, Any], int]:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "approved", "reject", "rejected"}:
        return {"error": "批量审核操作无效"}, 400

    store = get_mysql_store()
    configs: dict[str, dict[str, Any]] = {}
    if kind == "hotel_name":
        configs[kind] = {
            "list": store.hotel_name_corrections,
            "review": store.review_hotel_name_correction,
            "cache": cache_approved_hotel_name,
        }
    elif kind == "hotel_area":
        configs[kind] = {
            "list": store.hotel_area_corrections,
            "review": store.review_hotel_area_correction,
            "cache": cache_approved_hotel_area,
        }
    elif kind == "area_merge":
        configs[kind] = {
            "list": store.area_merge_corrections,
            "review": store.review_area_merge_correction,
            "cache": cache_approved_area_merge,
        }
    config = configs.get(kind)
    if not config:
        return {"error": "批量审核类型无效"}, 400

    reviewed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    max_batches = 20
    for _ in range(max_batches):
        pending = config["list"]("pending", 100)
        new_items = [item for item in pending if int(item.get("id") or 0) and int(item.get("id") or 0) not in seen_ids]
        if not new_items:
            break
        for item in new_items:
            correction_id = int(item.get("id") or 0)
            seen_ids.add(correction_id)
            result = config["review"](correction_id, normalized_action, reviewer_note)
            if not result.get("ok"):
                errors.append({"id": correction_id, "error": result.get("error") or "unknown"})
                continue
            correction = result.get("correction") or {}
            if result.get("status") == "approved":
                config["cache"](correction)
            reviewed.append(
                {
                    "id": correction_id,
                    "status": result.get("status") or "",
                    "approved_count": result.get("approved_count"),
                }
            )

    approved_count = sum(1 for item in reviewed if item.get("status") == "approved")
    rejected_count = sum(1 for item in reviewed if item.get("status") == "rejected")
    return {
        "ok": True,
        "action": normalized_action,
        "reviewed_count": len(reviewed),
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "failed_count": len(errors),
        "reviewed": reviewed,
        "errors": errors,
    }, 200


def update_job_progress(job_id: str, progress: dict[str, Any]) -> None:
    progress_data = copy.deepcopy(progress)
    partial_result = progress_data.pop("partial_result", None)
    persist_result: dict[str, Any] | None = None
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["progress"] = progress_data
        append_job_progress_event(job, progress_data)
        if partial_result is not None:
            previous_partial = copy.deepcopy(job.get("partial_result"))
            job["partial_result"] = partial_result
        version = bump_job_version(job)
        if partial_result is not None:
            append_job_change(job, version, "partial_result_delta", previous_partial, partial_result)
            fingerprint = result_price_fingerprint(partial_result)
            persisted = set(job.get("persisted_price_fingerprints") or [])
            if fingerprint and fingerprint not in persisted:
                persisted.add(fingerprint)
                job["persisted_price_fingerprints"] = list(persisted)[-40:]
                persist_result = copy.deepcopy(partial_result)
        job["updated_at"] = utc_timestamp()
        job["updated_ts"] = time.time()
    if persist_result is not None:
        persist_result_prices(persist_result, job_id=job_id, source="api")


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
            -int(item.get("hotel_count") or 0),
            -int(item.get("lower_price_hotel_count") or 0),
            -float(item.get("lower_price_ratio") or 0),
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

    city_results_by_city: dict[str, dict[str, Any]] = {}
    completed_cities: set[str] = set()
    state_lock = threading.Lock()
    first_success = None
    cache_hits = 0
    live_count = 0
    error_count = 0
    done_count = 0

    def emit_nearby_progress(message: str, stage: str = "nearby", **extra: Any) -> None:
        if progress_callback is None:
            return
        progress_callback({"stage": stage, "message": message, **extra})

    def nearby_partial_response() -> dict[str, Any]:
        return build_nearby_response(
            origin_city=origin_city,
            target_cities=target_cities,
            holiday_code=holiday_code,
            min_price_int=min_price_int,
            max_price_int=max_price_int,
            feature_filters_response=feature_filters_response,
            first_success=first_success,
            city_results=list(city_results_by_city.values()),
            cache_hits=cache_hits,
            live_count=live_count,
            error_count=error_count,
        )

    def publish_city_partial(city: str, progress: dict[str, Any]) -> None:
        nonlocal first_success
        partial_result = progress.get("partial_result")
        if not isinstance(partial_result, dict) or not partial_result.get("choices"):
            return

        city_payload = build_nearby_city_result(city, partial_result)
        city_result = city_payload["city_result"]
        city_result["partial"] = True
        city_result["status"] = "running"
        with state_lock:
            if city in completed_cities:
                return
            existing = city_results_by_city.get(city)
            existing_count = len(existing.get("choices") or []) if isinstance(existing, dict) else 0
            if existing_count <= len(city_result.get("choices") or []):
                city_results_by_city[city] = city_result
            if first_success is None:
                first_success = partial_result
            partial = nearby_partial_response()
            completed_snapshot = done_count

        choice_count = len(city_result.get("choices") or [])
        message = progress.get("message") or f"{city} 已先展示 {choice_count} 家酒店。"
        emit_nearby_progress(
            f"{city}：{message}",
            "nearby_city_preview",
            city=city,
            completed=completed_snapshot,
            total=len(target_cities),
            choice_count=choice_count,
            partial_result=partial,
        )

    def search_city(city: str) -> dict[str, Any]:
        def city_progress(progress: dict[str, Any]) -> None:
            message = progress.get("message") or "正在查询..."
            emit_nearby_progress(f"{city}：{message}", "nearby_city", city=city, inner=progress)
            publish_city_partial(city, progress)

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
                with state_lock:
                    error_count += 1
                    city_results_by_city[city] = {"city": city, "error": str(exc), "choices": [], "area_recommendations": []}
                    completed_cities.add(city)
                    done_count += 1
                    partial = nearby_partial_response()
                    completed_snapshot = done_count
                emit_nearby_progress(
                    f"已完成 {completed_snapshot}/{len(target_cities)} 个城市，{city} 无结果：{exc}",
                    "nearby_progress",
                    completed=completed_snapshot,
                    total=len(target_cities),
                    partial_result=partial,
                )
                continue

            result = city_payload["result"]
            cache = city_payload["cache"]
            city_result = city_payload["city_result"]
            city_result["status"] = "succeeded"
            with state_lock:
                if first_success is None:
                    first_success = result
                if cache.get("hit"):
                    cache_hits += 1
                elif cache.get("source") == "live":
                    live_count += 1
                city_results_by_city[city] = city_result
                completed_cities.add(city)
                done_count += 1
                partial = nearby_partial_response()
                completed_snapshot = done_count

            emit_nearby_progress(
                f"已完成 {completed_snapshot}/{len(target_cities)} 个城市：{city} 命中 {city_payload['city_result']['choice_count']} 家酒店。",
                "nearby_progress",
                completed=completed_snapshot,
                total=len(target_cities),
                partial_result=partial,
            )

    with state_lock:
        response = nearby_partial_response()
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


def persist_completed_coverage_cache(payload: dict[str, Any], result: dict[str, Any]) -> bool:
    supplement = result.get("coverage_supplement") if isinstance(result, dict) else {}
    if not isinstance(supplement, dict) or supplement.get("status") not in {"succeeded", "skipped"}:
        return False
    city = (payload.get("city") or result.get("city") or "").strip()
    holiday_code = (payload.get("holiday_code") or (result.get("holiday") or {}).get("code") or "").strip()
    if not city or not holiday_code:
        return False
    try:
        min_price_int, max_price_int = request_price_filters(payload)
        return finder.store_completed_coverage_result(
            city=city,
            holiday_code=holiday_code,
            min_price=min_price_int,
            max_price=max_price_int,
            advanced_filter=payload.get("advanced_filter"),
            pool_filter=payload.get("pool_filter"),
            child_facility_filter=payload.get("child_facility_filter") or payload.get("children_pool_filter"),
            result=result,
        )
    except (HolidayCalendarError, ReverseTravelFinderError, ValueError):
        return False


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
    client_id = canonical_client_id(job.get("client_id"))
    poll_url = f"/api/jobs/{job['job_id']}"
    if client_id:
        poll_url = f"{poll_url}?{urlencode({'client_id': client_id})}"
    payload = {
        "job_id": job["job_id"],
        "status": job["status"],
        "version": job_version(job),
        "poll_url": poll_url,
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
    client_id = canonical_client_id(payload.get("client_id"))
    job = {
        "job_id": job_id,
        "kind": kind,
        "signature": signature,
        "client_id": client_id,
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
        "version": 1,
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
        bump_job_version(job)
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

    persist_final_result: dict[str, Any] | None = None
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["updated_at"] = utc_timestamp()
        job["updated_ts"] = time.time()
        job["status_code"] = status_code
        if status_code == 200:
            previous_partial = copy.deepcopy(job.get("partial_result"))
            job["status"] = "succeeded"
            job["result"] = result
            job["partial_result"] = result
            job["progress"] = {"stage": "succeeded", "message": "查询完成。", "percent": 100}
            append_job_progress_event(job, job["progress"])
            version = bump_job_version(job)
            append_job_change(job, version, "result_delta", previous_partial, result)
            persist_final_result = copy.deepcopy(result)
        else:
            job["status"] = "failed"
            job["error"] = result.get("error") or "查询失败"
            job["progress"] = {"stage": "failed", "message": job["error"]}
            append_job_progress_event(job, job["progress"])
            bump_job_version(job)
            signature = job.get("signature")
            if signature and job_signature_index.get(signature) == job_id:
                job_signature_index.pop(signature, None)
    if persist_final_result is not None:
        persist_result_prices(persist_final_result, job_id=job_id, source="api")
        if kind == "coverage":
            persist_completed_coverage_cache(payload, persist_final_result)


def start_background_job(kind: str, payload: dict[str, Any]):
    cleanup_jobs()
    now = time.time()
    signature = canonical_job_signature(kind, payload)
    client_id = canonical_client_id(payload.get("client_id"))
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
                    bump_job_version(existing_job)
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
    mysql_preview_result = None if stale_result is not None else mysql_price_preview_for_job_start(kind, payload)

    job_id = uuid.uuid4().hex
    initial_progress = (
        {"stage": "stale_cache_preview", "message": "已先显示旧缓存，正在后台刷新最新价格。", "percent": 8}
        if stale_result is not None
        else {"stage": "mysql_price_preview", "message": "已先显示 MySQL 价格缓存，正在后台刷新完整搜索。", "percent": 8}
        if mysql_preview_result is not None
        else {"stage": "queued", "message": "查询任务已创建，正在等待执行。"}
    )
    initial_partial = stale_result or mysql_preview_result
    job = {
        "job_id": job_id,
        "kind": kind,
        "signature": signature,
        "client_id": client_id,
        "status": "queued",
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "created_ts": now,
        "updated_ts": now,
        "payload": copy.deepcopy(payload),
        "result": None,
        "partial_result": initial_partial,
        "progress": initial_progress,
        "progress_events": [{"time": utc_timestamp(), **initial_progress}],
        "version": 1,
        "error": "",
        "status_code": None,
    }
    with job_lock:
        jobs[job_id] = job
        if signature:
            job_signature_index[signature] = job_id
        start_payload = job_start_payload(copy.deepcopy(job))
    executor = refresh_executor if kind in {"areas", "hotel_names"} else job_executor
    executor.submit(run_job, job_id, kind, copy.deepcopy(payload))
    return jsonify(start_payload), 202


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


@app.get("/admin")
def admin_dashboard():
    if not is_admin_request():
        return "后台仅允许本机访问；公网访问需要配置 REVERSE_TRAVEL_ADMIN_TOKEN。", 403
    return render_template("admin.html", admin_token_required=bool(ADMIN_TOKEN))


@app.get("/api/admin/status")
def admin_status():
    if not is_admin_request():
        return jsonify({"error": "后台状态仅允许本机查看；公网访问需要配置后台令牌"}), 403
    return jsonify(admin_status_payload())


@app.get("/api/admin/jobs/<job_id>/pdf")
def admin_job_pdf(job_id: str):
    if not is_admin_request():
        return jsonify({"error": "任务 PDF 仅允许后台下载"}), 403
    cleanup_jobs()
    with job_lock:
        job = copy.deepcopy(jobs.get(job_id))
    if not job:
        return jsonify({"error": "查询任务不存在或已过期"}), 404
    if not job_has_exportable_result(job):
        return jsonify({"error": "这个任务还没有可下载的搜索结果"}), 409

    result, _source = job_export_result(job)
    html_text = build_job_pdf_html(job)
    if str(request.args.get("format") or "").lower() == "html":
        return Response(html_text, content_type="text/html; charset=utf-8")

    try:
        pdf_bytes = render_pdf_bytes(html_text)
    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "error": "PDF 生成失败，请确认服务器已安装 Playwright Chromium。",
                "detail": str(exc),
                "html_preview_url": f"/api/admin/jobs/{job_id}/pdf?format=html",
            }
        ), 503

    filename = job_report_filename(job, result)
    ascii_filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", filename).strip("-") or "reverse-travel-job.pdf"
    response = Response(pdf_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = (
        f"attachment; filename={ascii_filename}; filename*=UTF-8''{quote(filename)}"
    )
    return response


@app.get("/api/admin/hotel-name-corrections")
def admin_hotel_name_corrections():
    if not is_admin_request():
        return jsonify({"error": "酒店名审核仅允许后台查看"}), 403
    return jsonify(hotel_name_correction_admin_payload())


@app.post("/api/admin/hotel-name-corrections/batch-review")
def admin_batch_review_hotel_name_corrections():
    if not is_admin_request():
        return jsonify({"error": "酒店名审核仅允许后台操作"}), 403
    payload = request.get_json(silent=True) or {}
    result, status_code = batch_review_pending_corrections(
        "hotel_name",
        str(payload.get("action") or ""),
        str(payload.get("reviewer_note") or ""),
    )
    return jsonify(result), status_code


@app.post("/api/admin/hotel-name-corrections/<int:correction_id>/review")
def admin_review_hotel_name_correction(correction_id: int):
    if not is_admin_request():
        return jsonify({"error": "酒店名审核仅允许后台操作"}), 403
    payload = request.get_json(silent=True) or {}
    result = get_mysql_store().review_hotel_name_correction(
        correction_id,
        str(payload.get("action") or ""),
        str(payload.get("reviewer_note") or ""),
    )
    if not result.get("ok"):
        status_code = 404 if result.get("error") == "not_found" else 400
        return jsonify({"error": "审核操作失败", "detail": result.get("error")}), status_code
    correction = result.get("correction") or {}
    if result.get("status") == "approved":
        cache_approved_hotel_name(correction)
    return jsonify(result)


@app.get("/api/admin/hotel-area-corrections")
def admin_hotel_area_corrections():
    if not is_admin_request():
        return jsonify({"error": "片区审核仅允许后台查看"}), 403
    return jsonify(hotel_area_correction_admin_payload())


@app.post("/api/admin/hotel-area-corrections/batch-review")
def admin_batch_review_hotel_area_corrections():
    if not is_admin_request():
        return jsonify({"error": "片区审核仅允许后台操作"}), 403
    payload = request.get_json(silent=True) or {}
    result, status_code = batch_review_pending_corrections(
        "hotel_area",
        str(payload.get("action") or ""),
        str(payload.get("reviewer_note") or ""),
    )
    return jsonify(result), status_code


@app.post("/api/admin/hotel-area-corrections/<int:correction_id>/review")
def admin_review_hotel_area_correction(correction_id: int):
    if not is_admin_request():
        return jsonify({"error": "片区审核仅允许后台操作"}), 403
    payload = request.get_json(silent=True) or {}
    result = get_mysql_store().review_hotel_area_correction(
        correction_id,
        str(payload.get("action") or ""),
        str(payload.get("reviewer_note") or ""),
    )
    if not result.get("ok"):
        status_code = 404 if result.get("error") == "not_found" else 400
        return jsonify({"error": "审核操作失败", "detail": result.get("error")}), status_code
    correction = result.get("correction") or {}
    if result.get("status") == "approved":
        cache_approved_hotel_area(correction)
    return jsonify(result)


@app.get("/api/admin/area-merge-corrections")
def admin_area_merge_corrections():
    if not is_admin_request():
        return jsonify({"error": "合并片区审核仅允许后台查看"}), 403
    return jsonify(area_merge_correction_admin_payload())


@app.post("/api/admin/area-merge-corrections/batch-review")
def admin_batch_review_area_merge_corrections():
    if not is_admin_request():
        return jsonify({"error": "合并片区审核仅允许后台操作"}), 403
    payload = request.get_json(silent=True) or {}
    result, status_code = batch_review_pending_corrections(
        "area_merge",
        str(payload.get("action") or ""),
        str(payload.get("reviewer_note") or ""),
    )
    return jsonify(result), status_code


@app.post("/api/admin/area-merge-corrections/<int:correction_id>/review")
def admin_review_area_merge_correction(correction_id: int):
    if not is_admin_request():
        return jsonify({"error": "合并片区审核仅允许后台操作"}), 403
    payload = request.get_json(silent=True) or {}
    result = get_mysql_store().review_area_merge_correction(
        correction_id,
        str(payload.get("action") or ""),
        str(payload.get("reviewer_note") or ""),
    )
    if not result.get("ok"):
        status_code = 404 if result.get("error") == "not_found" else 400
        return jsonify({"error": "审核操作失败", "detail": result.get("error")}), status_code
    correction = result.get("correction") or {}
    if result.get("status") == "approved":
        cache_approved_area_merge(correction)
    return jsonify(result)


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


@app.get("/api/daily-recommendation")
def daily_recommendation():
    return jsonify(daily_recommendation_payload())


@app.get("/api/area-merge-corrections/active")
def active_area_merge_corrections():
    raw_cities = request.args.getlist("city") + request.args.getlist("cities")
    city_names: list[str] = []
    for raw in raw_cities:
        for part in str(raw or "").split(","):
            city = normalize_city(part) or finder._to_simplified_chinese(part.strip())
            if city and city not in city_names:
                city_names.append(city)
    try:
        corrections = get_mysql_store().active_area_merge_corrections(city_names, 120)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"corrections": [], "disabled_reason": str(exc)})
    return jsonify({"corrections": [public_area_merge_correction(item) for item in corrections]})


@app.post("/api/hotel-name-corrections/approved")
def approved_hotel_name_corrections():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("hotel_ids") if isinstance(payload.get("hotel_ids"), list) else []
    raw_choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    hotel_ids: list[str] = []
    for value in raw_ids:
        hotel_id = str(value or "").strip()
        if hotel_id and hotel_id not in hotel_ids:
            hotel_ids.append(hotel_id)
    for choice in raw_choices:
        if not isinstance(choice, dict):
            continue
        hotel_id = str(choice.get("hotel_id") or choice.get("trip_hotel_id") or "").strip()
        if hotel_id and hotel_id not in hotel_ids:
            hotel_ids.append(hotel_id)
    hotel_ids = hotel_ids[:300]
    try:
        records = get_mysql_store().approved_hotel_name_records(hotel_ids)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"records": [], "disabled_reason": str(exc)})
    return jsonify({"records": [public_hotel_name_record(hotel_id, record) for hotel_id, record in records.items()]})


@app.post("/api/hotel-area-corrections/approved")
def approved_hotel_area_corrections():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("hotel_ids") if isinstance(payload.get("hotel_ids"), list) else []
    raw_choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    hotel_ids: list[str] = []
    for value in raw_ids:
        hotel_id = str(value or "").strip()
        if hotel_id and hotel_id not in hotel_ids:
            hotel_ids.append(hotel_id)
    for choice in raw_choices:
        if not isinstance(choice, dict):
            continue
        hotel_id = str(choice.get("hotel_id") or choice.get("trip_hotel_id") or "").strip()
        if hotel_id and hotel_id not in hotel_ids:
            hotel_ids.append(hotel_id)
    hotel_ids = hotel_ids[:300]
    try:
        records = get_mysql_store().approved_hotel_area_records(hotel_ids)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"records": [], "disabled_reason": str(exc)})
    return jsonify({"records": [public_hotel_area_record(hotel_id, record) for hotel_id, record in records.items()]})


@app.post("/api/hotel-name-corrections")
def submit_hotel_name_correction():
    payload = request.get_json(silent=True) or {}
    try:
        correction_payload = normalize_hotel_name_correction_payload(payload)
    except ReverseTravelFinderError as exc:
        return jsonify({"error": str(exc)}), 400
    result = get_mysql_store().submit_hotel_name_correction(correction_payload)
    if not result.get("ok"):
        return jsonify({"error": "酒店名修改暂时无法提交", "detail": result.get("error")}), 503
    return jsonify({"status": "pending", "correction": result}), 202


@app.post("/api/hotel-area-corrections")
def submit_hotel_area_correction():
    payload = request.get_json(silent=True) or {}
    try:
        correction_payload = normalize_hotel_area_correction_payload(payload)
    except ReverseTravelFinderError as exc:
        return jsonify({"error": str(exc)}), 400
    result = get_mysql_store().submit_hotel_area_correction(correction_payload)
    if not result.get("ok"):
        return jsonify({"error": "片区修改暂时无法提交", "detail": result.get("error")}), 503
    return jsonify({"status": "pending", "correction": result}), 202


@app.post("/api/area-merge-corrections")
def submit_area_merge_correction():
    payload = request.get_json(silent=True) or {}
    try:
        correction_payload = normalize_area_merge_correction_payload(payload)
    except ReverseTravelFinderError as exc:
        return jsonify({"error": str(exc)}), 400
    result = get_mysql_store().submit_area_merge_correction(correction_payload)
    if not result.get("ok"):
        return jsonify({"error": "合并片区暂时无法提交", "detail": result.get("error")}), 503
    return jsonify({"status": "pending", "correction": result}), 202


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
    if status_code == 200:
        persist_result_prices(result, source="api")
    return jsonify(result), status_code


@app.post("/api/search/start")
def search_start():
    payload = request.get_json(silent=True) or {}
    return start_background_job("search", payload)


@app.post("/api/nearby-search")
def nearby_search():
    payload = request.get_json(silent=True) or {}
    result, status_code = nearby_search_result_from_payload(payload)
    if status_code == 200:
        persist_result_prices(result, source="api")
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
    job_client_id = canonical_client_id(job.get("client_id"))
    if job_client_id and request_client_id() != job_client_id:
        return jsonify({"error": "查询任务不存在或已过期"}), 404
    since_version = optional_since_version(request.args.get("since_version"))
    status_code = 200 if job.get("status") != "failed" else int(job.get("status_code") or 500)
    return jsonify(public_job(job, since_version=since_version)), status_code


@app.get("/api/admin/prewarm/status")
def cache_prewarm_status():
    if not is_admin_request():
        return jsonify({"error": "缓存预热状态仅允许本机查看"}), 403
    return jsonify(public_prewarm_state())


@app.post("/api/admin/prewarm/start")
def cache_prewarm_start():
    if not is_admin_request():
        return jsonify({"error": "缓存预热仅允许本机启动"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        state, status_code = start_cache_prewarm(payload)
    except ReverseTravelFinderError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(state), status_code


@app.post("/api/admin/prewarm/daily/start")
def cache_daily_prewarm_start():
    if not is_admin_request():
        return jsonify({"error": "每日推荐预热仅允许后台启动"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        state, status_code = start_daily_cache_prewarm(payload)
    except ReverseTravelFinderError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(state), status_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5012, debug=False)
