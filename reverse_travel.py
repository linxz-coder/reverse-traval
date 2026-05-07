from __future__ import annotations

import datetime as dt
import copy
import hashlib
import html
import json
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from holiday_helper import HolidayCalendar, HolidayCalendarError, HolidayRange

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
HOTEL_LIST_LIMIT = 120
QUERY_PROFILE = "tri_state_feature_filters_verified_features_area_cache_v27"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
SEARCH_CACHE_TTL_SECONDS = 24 * 60 * 60
CITY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_LIST_FILTERS = "29~1*29*1~2*2,17~1*17*1*2,80~0~1*80*0*2"
ADVANCED_YES_FILTERS = ("16~4*16*4*4", "16~5*16*5*5")
ADVANCED_NO_FILTERS = ("16~2*16*2*≤2", "16~3*16*3*3")
POOL_YES_FILTERS = ("3~605*3*605*Pool",)
CHILD_FACILITY_YES_FILTERS = ("3~68*3*68*Playground",)
TRI_STATE_VALUES = {"all", "yes", "no"}
MAX_COMPARE_WINDOWS = 8
MAX_SCROLL_ROUNDS = 16
SCROLL_WAIT_MS = 2200
STABLE_SCROLL_ROUNDS = 6
COMPARE_PAGE_BATCH_SIZE = 4
CHINESE_NAME_WORKERS = 8
FEATURE_VERIFY_WORKERS = 8
BROWSER_SESSION_LIMIT = 2
SUPPLEMENT_MIN_CHOICES = 8
SUPPLEMENT_HOTEL_LIST_LIMIT = 40
MAX_SUPPLEMENT_KEYWORD_CANDIDATES = 2
CITY_SUPPLEMENT_KEYWORDS = {
    "广州": (
        "增城凯悦酒店",
        "增城酒店",
        "琶洲酒店",
    ),
    "深圳": (
        "国际会展中心皇冠假日酒店",
        "国际会展中心洲际酒店",
        "光明美爵酒店",
    ),
    "东莞": (
        "松山湖酒店",
        "厚街会展酒店",
    ),
    "惠州": (
        "惠阳酒店",
        "博罗酒店",
        "仲恺酒店",
    ),
    "中山": (
        "石岐酒店",
        "东区酒店",
        "小榄酒店",
    ),
    "江门": (
        "新会酒店",
        "鹤山酒店",
        "台山酒店",
    ),
    "河源": (
        "万绿湖酒店",
        "巴伐利亚庄园酒店",
        "客天下酒店",
    ),
    "肇庆": (
        "七星岩酒店",
        "鼎湖山酒店",
        "高要酒店",
    ),
    "珠海": (
        "横琴长隆酒店",
        "情侣路酒店",
        "金湾酒店",
    ),
    "韶关": (
        "丹霞山酒店",
        "南华寺酒店",
        "乳源酒店",
    ),
    "汕尾": (
        "金町湾酒店",
        "红海湾酒店",
        "海丰酒店",
    ),
}
CITY_DEFAULT_AREA_NAMES = {
    "深圳": ("深圳国际会展中心片区", "光明虹桥公园片区", "深圳观澜片区", "深圳南山片区"),
    "广州": ("广州增城片区", "广州琶洲会展片区", "广州天河片区", "广州黄埔片区"),
    "东莞": ("东莞松山湖片区", "东莞厚街会展片区", "东莞虎门片区", "东莞东城片区"),
    "惠州": ("惠州惠阳片区", "惠州惠东片区", "惠州博罗片区", "惠州仲恺片区"),
    "中山": ("中山石岐片区", "中山东区片区", "中山小榄片区", "中山古镇片区", "中山三乡片区"),
    "江门": ("江门新会片区", "江门鹤山片区", "江门台山片区", "江门开平赤坎片区", "江门恩平温泉片区"),
    "河源": ("河源万绿湖片区", "河源巴伐利亚庄园片区", "河源源城片区", "河源客天下片区", "河源和平温泉片区"),
    "肇庆": ("肇庆七星岩星湖片区", "肇庆鼎湖山片区", "肇庆端州片区", "肇庆高要片区", "肇庆四会片区"),
    "珠海": ("珠海横琴长隆片区", "珠海情侣路香洲片区", "珠海拱北口岸片区", "珠海金湾航空新城片区", "珠海唐家湾片区"),
    "韶关": ("韶关丹霞山片区", "韶关南华寺曹溪片区", "韶关乳源大峡谷片区", "韶关市区片区", "韶关南雄片区"),
    "汕尾": ("汕尾金町湾片区", "汕尾红海湾片区", "汕尾海丰片区", "汕尾陆丰片区", "汕尾市区片区"),
}
CITY_ID_LABELS = {
    "31": "珠海",
    "59": "澳门",
    "251": "佛山",
    "553": "中山",
    "3933": "云浮",
}
CITY_LABEL_KEYWORDS = {
    "深圳": ("shenzhen", "深圳"),
    "广州": ("guangzhou", "广州", "廣州"),
    "东莞": ("dongguan", "东莞", "東莞"),
    "中山": ("zhongshan", "中山"),
    "惠州": ("huizhou", "惠州", "boluo", "博罗", "博羅", "huidong", "惠东", "惠東", "huiyang", "惠阳", "惠陽", "longmen", "龙门", "龍門", "双月湾", "雙月灣", "巽寮", "巽寮湾", "巽寮灣"),
    "江门": ("jiangmen", "江门", "江門", "xinhui", "新会", "新會", "heshan", "鹤山", "鶴山", "taishan", "台山", "kaiping", "开平", "開平", "enping", "恩平"),
    "河源": ("heyuan", "河源", "源城", "东源", "東源", "龙川", "龍川", "紫金", "连平", "連平", "和平", "万绿湖", "萬綠湖"),
    "肇庆": ("zhaoqing", "肇庆", "肇慶", "端州", "鼎湖", "高要", "四会", "四會", "七星岩", "七星巖", "星湖"),
    "珠海": ("zhuhai", "珠海", "香洲", "横琴", "橫琴", "长隆", "長隆", "拱北", "金湾", "金灣", "唐家湾", "唐家灣", "斗门", "斗門"),
    "韶关": ("shaoguan", "韶关", "韶關", "丹霞山", "南华寺", "南華寺", "乳源", "南雄", "乐昌", "樂昌", "曲江", "浈江", "湞江", "武江", "翁源"),
    "汕尾": ("shanwei", "汕尾", "海丰", "海豐", "陆丰", "陸豐", "陆河", "陸河", "红海湾", "紅海灣", "金町湾", "金町灣", "深汕"),
    "佛山": ("foshan", "佛山", "shunde", "顺德", "順德", "jiujiang", "九江"),
    "云浮": ("yunfu", "云浮", "雲浮", "云安", "雲安", "罗定", "羅定", "新兴", "新興", "郁南"),
    "澳门": ("macau", "macao", "澳门", "澳門"),
    "清远": ("qingyuan", "清远", "清遠", "英德", "连州", "連州", "佛冈", "佛岡"),
    "郴州": ("chenzhou", "郴州", "宜章", "汝城"),
    "赣州": ("ganzhou", "赣州", "贛州", "大余", "崇义", "崇義"),
}


class ReverseTravelFinderError(RuntimeError):
    pass


@dataclass
class CityCandidate:
    city_id: int
    city_name: str
    province_id: int
    country_id: int
    lat: float
    lon: float
    filter_id: str
    search_coordinate: str


@dataclass(frozen=True)
class HotelKeywordCandidate:
    hotel_id: str
    title: str
    filter_id: str
    lat: float
    lon: float
    search_coordinate: str


@dataclass(frozen=True)
class FeatureFilters:
    advanced: str = "all"
    pool: str = "all"
    child_facility: str = "all"

    def cache_parts(self) -> tuple[str, str, str]:
        return (self.advanced, self.pool, self.child_facility)

    def to_response(self) -> dict[str, dict[str, str]]:
        return {
            "advanced": {
                "value": self.advanced,
                "label": self._label(self.advanced),
                "name": "高级酒店（四钻/四星级以上）",
            },
            "pool": {
                "value": self.pool,
                "label": self._label(self.pool),
                "name": "游泳池",
            },
            "child_facility": {
                "value": self.child_facility,
                "label": self._label(self.child_facility),
                "name": "儿童设施",
            },
        }

    @staticmethod
    def _label(value: str) -> str:
        return {"all": "全部", "yes": "是", "no": "否"}.get(value, "全部")


class ReverseTravelFinder:
    def __init__(
        self,
        calendar: HolidayCalendar,
        cache_dir: str | Path | None = None,
        search_cache_ttl_seconds: int = SEARCH_CACHE_TTL_SECONDS,
    ) -> None:
        self.calendar = calendar
        self.cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        self.search_cache_ttl_seconds = search_cache_ttl_seconds
        self._browser_semaphore = threading.BoundedSemaphore(BROWSER_SESSION_LIMIT)
        self._cache_lock = threading.Lock()
        self._search_cache: dict[tuple[str, ...], dict[str, Any]] = {}
        self._search_cache_meta: dict[tuple[str, ...], dict[str, Any]] = {}
        self._city_cache: dict[str, dict[str, Any]] = self._load_cache_items(self._city_cache_path())
        self._hotel_name_cache: dict[str, dict[str, str]] = self._load_cache_items(self._hotel_name_cache_path())
        self._hotel_feature_cache: dict[str, dict[str, Any]] = self._load_cache_items(self._hotel_feature_cache_path())

    def list_holidays(self) -> list[dict[str, Any]]:
        holidays = self.calendar.get_upcoming_holidays()
        return [
            {
                "code": item.code,
                "name": item.name,
                "start": item.start.isoformat(),
                "end": item.end.isoformat(),
                "days": item.days,
                "label": f"{item.name} {item.start.isoformat()} 至 {item.end.isoformat()} ({item.days}天)",
            }
            for item in holidays
        ]

    def _city_cache_path(self) -> Path:
        return self.cache_dir / "city_cache.json"

    def _hotel_name_cache_path(self) -> Path:
        return self.cache_dir / "hotel_name_cache.json"

    def _hotel_feature_cache_path(self) -> Path:
        return self.cache_dir / "hotel_feature_cache.json"

    def _search_cache_path(self, cache_key: tuple[str, ...]) -> Path:
        raw_key = json.dumps(cache_key, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return self.cache_dir / "search" / f"{digest}.json"

    def _read_json_file(self, path: Path) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json_file(self, path: Path, data: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            tmp_path.replace(path)
        except OSError:
            return

    def _load_cache_items(self, path: Path) -> dict[str, Any]:
        data = self._read_json_file(path)
        if not isinstance(data, dict):
            return {}
        items = data.get("items")
        return items if isinstance(items, dict) else {}

    def _save_city_cache(self) -> None:
        with self._cache_lock:
            items = copy.deepcopy(self._city_cache)
        self._write_json_file(self._city_cache_path(), {"version": 1, "items": items})

    def _save_hotel_name_cache(self) -> None:
        with self._cache_lock:
            items = copy.deepcopy(self._hotel_name_cache)
        self._write_json_file(self._hotel_name_cache_path(), {"version": 1, "items": items})

    def _save_hotel_feature_cache(self) -> None:
        with self._cache_lock:
            items = copy.deepcopy(self._hotel_feature_cache)
        self._write_json_file(self._hotel_feature_cache_path(), {"version": 1, "items": items})

    def _is_cache_meta_fresh(self, meta: dict[str, Any] | None, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0 or not meta:
            return False
        try:
            created_at = float(meta.get("created_at") or 0)
        except (TypeError, ValueError):
            return False
        return created_at > 0 and time.time() - created_at <= ttl_seconds

    def _load_search_cache(self, cache_key: tuple[str, ...]) -> dict[str, Any] | None:
        if self.search_cache_ttl_seconds <= 0:
            return None
        record = self._read_json_file(self._search_cache_path(cache_key))
        if not isinstance(record, dict) or record.get("cache_key") != list(cache_key):
            return None
        try:
            created_at = float(record.get("created_at") or 0)
        except (TypeError, ValueError):
            return None
        if not self._is_cache_meta_fresh({"created_at": created_at}, self.search_cache_ttl_seconds):
            return None
        result = record.get("result")
        if not isinstance(result, dict):
            return None
        return {"created_at": created_at, "result": result}

    def _store_search_cache(self, cache_key: tuple[str, ...], base_result: dict[str, Any], created_at: float) -> None:
        if self.search_cache_ttl_seconds <= 0:
            return
        record = {
            "version": 1,
            "cache_key": list(cache_key),
            "created_at": created_at,
            "created_at_text": self._format_timestamp(created_at),
            "ttl_seconds": self.search_cache_ttl_seconds,
            "result": copy.deepcopy(base_result),
        }
        self._write_json_file(self._search_cache_path(cache_key), record)

    def _build_cache_info(self, source: str, created_at: float, hit: bool) -> dict[str, Any]:
        age_seconds = max(0, round(time.time() - created_at))
        expires_at = created_at + self.search_cache_ttl_seconds
        return {
            "hit": hit,
            "source": source,
            "source_label": {"live": "实时查询", "memory": "内存缓存", "disk": "本地缓存"}.get(source, source),
            "created_at": self._format_timestamp(created_at),
            "age_seconds": age_seconds,
            "ttl_seconds": self.search_cache_ttl_seconds,
            "expires_at": self._format_timestamp(expires_at),
        }

    def _format_timestamp(self, timestamp: float) -> str:
        try:
            return dt.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            return ""

    def _load_cached_city_candidate(self, cache_key: str) -> CityCandidate | None:
        if not cache_key:
            return None
        with self._cache_lock:
            record = copy.deepcopy(self._city_cache.get(cache_key))
        if not isinstance(record, dict):
            return None
        if not self._is_cache_meta_fresh(record, CITY_CACHE_TTL_SECONDS):
            return None
        candidate = record.get("candidate")
        if not isinstance(candidate, dict):
            return None
        try:
            return CityCandidate(**candidate)
        except (TypeError, ValueError):
            return None

    def _store_city_candidate(self, cache_key: str, candidate: CityCandidate) -> None:
        if not cache_key:
            return
        with self._cache_lock:
            self._city_cache[cache_key] = {
                "created_at": time.time(),
                "candidate": candidate.__dict__.copy(),
            }
        self._save_city_cache()

    def find_choices(
        self,
        city: str,
        holiday_code: str,
        min_price: int | None,
        max_price: int | None,
        advanced_filter: str | None = "all",
        pool_filter: str | None = "all",
        child_facility_filter: str | None = "all",
        use_cache: bool = True,
        cache_only: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        feature_filters = self._normalize_feature_filters(
            advanced_filter=advanced_filter,
            pool_filter=pool_filter,
            child_facility_filter=child_facility_filter,
        )
        cache_key = (QUERY_PROFILE, city.strip().lower(), holiday_code, *feature_filters.cache_parts())
        cache_info: dict[str, Any] | None = None
        with self._cache_lock:
            cached = self._search_cache.get(cache_key)
            cached_meta = self._search_cache_meta.get(cache_key)
            if cached is not None and not self._is_cache_meta_fresh(cached_meta, self.search_cache_ttl_seconds):
                self._search_cache.pop(cache_key, None)
                self._search_cache_meta.pop(cache_key, None)
                cached = None
                cached_meta = None
        if not use_cache:
            base_result = self._call_find_choices_base(
                city=city,
                holiday_code=holiday_code,
                feature_filters=feature_filters,
                progress_callback=progress_callback,
            )
            created_at = time.time()
            with self._cache_lock:
                self._search_cache[cache_key] = copy.deepcopy(base_result)
                self._search_cache_meta[cache_key] = {"created_at": created_at}
            self._store_search_cache(cache_key, base_result, created_at)
            cache_info = self._build_cache_info(source="live", created_at=created_at, hit=False)
        elif cached is None:
            disk_record = self._load_search_cache(cache_key)
            if disk_record is not None:
                base_result = copy.deepcopy(disk_record["result"])
                cache_info = self._build_cache_info(
                    source="disk",
                    created_at=float(disk_record["created_at"]),
                    hit=True,
                )
                with self._cache_lock:
                    self._search_cache[cache_key] = copy.deepcopy(base_result)
                    self._search_cache_meta[cache_key] = {
                        "created_at": float(disk_record["created_at"]),
                    }
            else:
                base_result = self._call_find_choices_base(
                    city=city,
                    holiday_code=holiday_code,
                    feature_filters=feature_filters,
                    progress_callback=progress_callback,
                )
                created_at = time.time()
                with self._cache_lock:
                    self._search_cache[cache_key] = copy.deepcopy(base_result)
                    self._search_cache_meta[cache_key] = {"created_at": created_at}
                self._store_search_cache(cache_key, base_result, created_at)
                cache_info = self._build_cache_info(source="live", created_at=created_at, hit=False)
        else:
            base_result = copy.deepcopy(cached)
            cache_info = self._build_cache_info(
                source="memory",
                created_at=float((cached_meta or {}).get("created_at") or time.time()),
                hit=True,
            )

        filtered_choices: list[dict[str, Any]] = []
        for hotel in base_result["choices"]:
            if min_price is not None and hotel["holiday_avg_nightly_tax_total_value"] < min_price:
                continue
            if max_price is not None and hotel["holiday_avg_nightly_tax_total_value"] > max_price:
                continue
            filtered_choices.append(hotel)

        base_result["price_filter"] = {"min_price": min_price, "max_price": max_price}
        base_result["feature_filters"] = feature_filters.to_response()
        base_result["choices"] = filtered_choices
        base_result["area_recommendations"] = self._build_area_recommendations(filtered_choices, base_result["city"])
        base_result["cache"] = cache_info
        return base_result

    def _call_find_choices_base(
        self,
        city: str,
        holiday_code: str,
        feature_filters: FeatureFilters,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "city": city,
            "holiday_code": holiday_code,
            "feature_filters": feature_filters,
        }
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        return self._find_choices_base(**kwargs)

    def _emit_progress(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        message: str,
        stage: str,
        **extra: Any,
    ) -> None:
        if progress_callback is None:
            return
        payload = {"stage": stage, "message": message}
        payload.update(extra)
        progress_callback(payload)

    def _find_choices_base(
        self,
        city: str,
        holiday_code: str,
        feature_filters: FeatureFilters,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        self._emit_progress(progress_callback, "正在准备节假日和对比日期...", "prepare", percent=5)
        holiday = self._get_holiday(holiday_code)
        compare_windows = self._build_compare_windows(holiday)
        if not compare_windows:
            raise ReverseTravelFinderError("未来一个月内没有可比较的非法定假期时间段。")

        self._emit_progress(progress_callback, "正在识别城市和 Trip.com 搜索范围...", "resolve_city", percent=10)
        city_candidate = self._resolve_city(city)

        with self._browser_semaphore:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=UA,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1440, "height": 1400},
                    service_workers="block",
                )
                context.route("**/*", self._route_lightweight_resources)
                context.add_init_script(
                    """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                    """
                )
                page = context.new_page()
                try:
                    page.goto("https://www.trip.com/hotels", wait_until="commit", timeout=15000)
                    page.wait_for_timeout(1200)
                except PlaywrightTimeoutError:
                    pass

                self._emit_progress(
                    progress_callback,
                    f"正在抓取{city_candidate.city_name}假期酒店列表...",
                    "holiday_hotels",
                    percent=18,
                )
                holiday_hotels = self._fetch_hotel_list(
                    city_candidate=city_candidate,
                    check_in=holiday.start,
                    check_out=holiday.check_out,
                    limit=HOTEL_LIST_LIMIT,
                    page=page,
                    feature_filters=feature_filters,
                )

                if not holiday_hotels:
                    browser.close()
                    raise ReverseTravelFinderError("没有抓到该城市在假期时段的酒店列表。")

                self._emit_progress(
                    progress_callback,
                    f"已抓到假期酒店 {len(holiday_hotels)} 家，正在抓取 {len(compare_windows)} 个平日代表时段...",
                    "comparison_hotels",
                    percent=36,
                    hotel_count=len(holiday_hotels),
                    comparison_total=len(compare_windows),
                )
                page.close()
                comparison_hotels = self._fetch_hotel_lists_parallel(
                    city_candidate=city_candidate,
                    windows=compare_windows,
                    limit=HOTEL_LIST_LIMIT,
                    context=context,
                    feature_filters=feature_filters,
                )

                comparison_map = self._build_comparison_map(comparison_hotels, compare_windows, holiday.days)
                choices = self._build_choices_from_hotels(city_candidate, holiday, holiday_hotels, comparison_map)
                if len(choices) < SUPPLEMENT_MIN_CHOICES:
                    self._emit_progress(
                        progress_callback,
                        "正在补充重点片区酒店，避免漏掉符合条件的酒店...",
                        "supplemental_hotels",
                        percent=62,
                        choice_count=len(choices),
                    )
                    supplemental_holiday_hotels, supplemental_comparison_hotels = self._fetch_supplemental_hotel_lists(
                        city_query=city,
                        city_candidate=city_candidate,
                        holiday=holiday,
                        compare_windows=compare_windows,
                        context=context,
                        feature_filters=feature_filters,
                    )
                    if supplemental_holiday_hotels:
                        holiday_hotels = self._merge_hotel_lists(holiday_hotels, supplemental_holiday_hotels)
                        for key, hotels in supplemental_comparison_hotels.items():
                            comparison_hotels[key] = self._merge_hotel_lists(
                                comparison_hotels.get(key, []),
                                hotels,
                            )
                        comparison_map = self._build_comparison_map(comparison_hotels, compare_windows, holiday.days)
                        choices = self._build_choices_from_hotels(
                            city_candidate,
                            holiday,
                            holiday_hotels,
                            comparison_map,
                        )
                browser.close()

        choices.sort(key=lambda item: (item["price_diff_nightly"], item["holiday_avg_nightly_tax_total_value"]))
        self._emit_progress(
            progress_callback,
            f"已完成价格对比，正在核验 {len(choices)} 家候选酒店设施...",
            "verify_features",
            percent=78,
            choice_count=len(choices),
        )
        choices = self._filter_choices_by_verified_features(choices, feature_filters)
        self._emit_progress(
            progress_callback,
            f"设施核验后保留 {len(choices)} 家，正在补全中文酒店名...",
            "chinese_names",
            percent=88,
            choice_count=len(choices),
        )
        self._enrich_choices_with_chinese_hotel_names(choices)
        self._emit_progress(progress_callback, "正在整理推荐区域和最终结果...", "finalize", percent=96)
        self._refresh_choice_area_names(choices, city_candidate.city_name)
        area_recommendations = self._build_area_recommendations(choices, city_candidate.city_name)

        return {
            "city": city_candidate.city_name,
            "holiday": {
                "code": holiday.code,
                "name": holiday.name,
                "check_in": holiday.start.isoformat(),
                "check_out": holiday.check_out.isoformat(),
                "days": holiday.days,
            },
            "price_filter": {"min_price": None, "max_price": None},
            "feature_filters": feature_filters.to_response(),
            "comparison_windows": [
                {
                    "check_in": item["check_in"].isoformat(),
                    "check_out": item["check_out"].isoformat(),
                }
                for item in compare_windows
            ],
            "area_recommendations": area_recommendations,
            "choices": choices,
        }

    def _build_comparison_map(
        self,
        comparison_hotels: dict[str, list[dict[str, Any]]],
        compare_windows: list[dict[str, dt.date]],
        nights: int,
    ) -> dict[str, dict[str, Any]]:
        comparison_map: dict[str, dict[str, Any]] = {}
        for window in compare_windows:
            hotels = comparison_hotels.get(window["check_in"].isoformat(), [])
            for hotel in hotels:
                room_type = self._classify_room_type(hotel["room_name"])
                if room_type == "unknown":
                    continue
                key = f"{hotel['hotel_id']}::{room_type}"
                nightly_value = self._nightly_value(hotel["tax_total_value"], nights)
                current = comparison_map.get(key)
                if not current:
                    comparison_map[key] = {
                        "hotel_id": hotel["hotel_id"],
                        "hotel_name": hotel["hotel_name"],
                        "room_type": room_type,
                        "nightly_values": [nightly_value],
                        "sample_count": 1,
                        "lowest_sample": {
                            **hotel,
                            "nightly_tax_total_value": nightly_value,
                            "window_check_in": window["check_in"].isoformat(),
                            "window_check_out": window["check_out"].isoformat(),
                        },
                    }
                    continue
                current["nightly_values"].append(nightly_value)
                current["sample_count"] += 1
                if nightly_value < current["lowest_sample"]["nightly_tax_total_value"]:
                    current["lowest_sample"] = {
                        **hotel,
                        "nightly_tax_total_value": nightly_value,
                        "window_check_in": window["check_in"].isoformat(),
                        "window_check_out": window["check_out"].isoformat(),
                    }
        return comparison_map

    def _build_choices_from_hotels(
        self,
        city_candidate: CityCandidate,
        holiday: HolidayRange,
        holiday_hotels: list[dict[str, Any]],
        comparison_map: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        choices: list[dict[str, Any]] = []
        for hotel in holiday_hotels:
            room_type = self._classify_room_type(hotel["room_name"])
            if room_type == "unknown":
                continue
            holiday_nightly_value = self._nightly_value(hotel["tax_total_value"], holiday.days)
            comparison = comparison_map.get(f"{hotel['hotel_id']}::{room_type}")
            if not comparison:
                continue
            average_nightly_value = round(sum(comparison["nightly_values"]) / comparison["sample_count"])
            diff = holiday_nightly_value - average_nightly_value
            if diff > 100:
                continue
            lowest_sample = comparison["lowest_sample"]
            detail_url = self._to_zh_detail_url(hotel["detail_url"])
            if not detail_url:
                detail_url = self._to_zh_detail_url(
                    self._build_detail_url_from_ids(
                        city_id=city_candidate.city_id,
                        hotel_id=hotel["hotel_id"],
                        check_in=holiday.start,
                        check_out=holiday.check_out,
                    )
                )

            choices.append(
                {
                    "hotel_id": hotel["hotel_id"],
                    "hotel_name": hotel["hotel_name"],
                    "hotel_original_name": hotel["hotel_name"],
                    "hotel_name_source": "",
                    "area_name": hotel.get("area_name") or self._infer_area_name(
                        city_name=city_candidate.city_name,
                        hotel_name=hotel["hotel_name"],
                        area_text=hotel.get("area_hint") or "",
                    ),
                    "area_hint": hotel.get("area_hint") or "",
                    "area_source": hotel.get("area_source") or "酒店位置",
                    "is_advanced": hotel.get("is_advanced"),
                    "has_pool": hotel.get("has_pool"),
                    "has_child_facility": hotel.get("has_child_facility"),
                    "room_type": room_type,
                    "room_type_label": self._room_type_label(room_type),
                    "holiday_room_name": self._localize_room_name(hotel["room_name"]),
                    "holiday_room_price": hotel["room_price_text"],
                    "holiday_tax_total_price": hotel["tax_total_text"],
                    "holiday_tax_total_value": hotel["tax_total_value"],
                    "holiday_avg_nightly_tax_total_price": self._format_cny(holiday_nightly_value),
                    "holiday_avg_nightly_tax_total_value": holiday_nightly_value,
                    "comparison_average_nightly_tax_total_price": self._format_cny(average_nightly_value),
                    "comparison_average_nightly_tax_total_value": average_nightly_value,
                    "comparison_sample_count": comparison["sample_count"],
                    "comparison_lowest_room_name": self._localize_room_name(lowest_sample["room_name"]),
                    "comparison_lowest_room_price": lowest_sample["room_price_text"],
                    "comparison_lowest_tax_total_price": lowest_sample["tax_total_text"],
                    "comparison_lowest_tax_total_value": lowest_sample["tax_total_value"],
                    "comparison_lowest_nightly_tax_total_price": self._format_cny(
                        lowest_sample["nightly_tax_total_value"]
                    ),
                    "comparison_lowest_nightly_tax_total_value": lowest_sample["nightly_tax_total_value"],
                    "comparison_lowest_check_in": lowest_sample["window_check_in"],
                    "comparison_lowest_check_out": lowest_sample["window_check_out"],
                    "price_diff_nightly": diff,
                    "price_diff_nightly_text": self._format_cny_diff(diff),
                    "detail_url": detail_url,
                }
            )
        return choices

    def _normalize_feature_filters(
        self,
        advanced_filter: str | None,
        pool_filter: str | None,
        child_facility_filter: str | None,
    ) -> FeatureFilters:
        return FeatureFilters(
            advanced=self._normalize_tri_state(advanced_filter, "高级酒店"),
            pool=self._normalize_tri_state(pool_filter, "游泳池"),
            child_facility=self._normalize_tri_state(child_facility_filter, "儿童设施"),
        )

    def _normalize_tri_state(self, value: str | None, field_name: str) -> str:
        normalized = (value or "all").strip().lower()
        if normalized in {"全部", "不限"}:
            normalized = "all"
        elif normalized == "是":
            normalized = "yes"
        elif normalized == "否":
            normalized = "no"
        if normalized not in TRI_STATE_VALUES:
            raise ReverseTravelFinderError(f"{field_name}筛选项只能是“是”“否”或“全部”。")
        return normalized

    def _get_holiday(self, holiday_code: str) -> HolidayRange:
        for item in self.calendar.get_upcoming_holidays():
            if item.code == holiday_code:
                return item
        raise ReverseTravelFinderError("没有找到对应的法定假期。")

    def _build_compare_windows(self, holiday: HolidayRange) -> list[dict[str, dt.date]]:
        return self._sample_compare_windows(self._build_all_compare_windows(holiday))

    def _build_all_compare_windows(self, holiday: HolidayRange) -> list[dict[str, dt.date]]:
        nights = holiday.days
        start = holiday.end + dt.timedelta(days=1)
        last_start = holiday.end + dt.timedelta(days=30)
        windows: list[dict[str, dt.date]] = []
        current = start
        while current <= last_start:
            check_out = current + dt.timedelta(days=nights)
            valid = True
            for offset in range(nights):
                if self.calendar.is_statutory_holiday(current + dt.timedelta(days=offset)):
                    valid = False
                    break
            if valid:
                windows.append({"check_in": current, "check_out": check_out})
            current += dt.timedelta(days=1)
        return windows

    def _sample_compare_windows(self, windows: list[dict[str, dt.date]]) -> list[dict[str, dt.date]]:
        if len(windows) <= MAX_COMPARE_WINDOWS:
            return windows
        weekdays = [item for item in windows if item["check_in"].weekday() < 5]
        weekends = [item for item in windows if item["check_in"].weekday() >= 5]
        sampled = self._pick_evenly(weekdays, MAX_COMPARE_WINDOWS // 2)
        sampled.extend(self._pick_evenly(weekends, MAX_COMPARE_WINDOWS - len(sampled)))
        if len(sampled) < MAX_COMPARE_WINDOWS:
            selected_dates = {item["check_in"] for item in sampled}
            remainder = [item for item in windows if item["check_in"] not in selected_dates]
            sampled.extend(self._pick_evenly(remainder, MAX_COMPARE_WINDOWS - len(sampled)))
        return sorted(sampled, key=lambda item: item["check_in"])

    def _pick_evenly(self, items: list[dict[str, dt.date]], limit: int) -> list[dict[str, dt.date]]:
        if limit <= 0 or not items:
            return []
        if len(items) <= limit:
            return items[:]
        if limit == 1:
            return [items[len(items) // 2]]
        indexes = {
            round(idx * (len(items) - 1) / (limit - 1))
            for idx in range(limit)
        }
        return [items[idx] for idx in sorted(indexes)]

    def _route_lightweight_resources(self, route) -> None:
        if route.request.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()

    def _resolve_city(self, city: str) -> CityCandidate:
        city_cache_key = city.strip().lower()
        cached_city = self._load_cached_city_candidate(city_cache_key)
        if cached_city is not None:
            return cached_city

        trace_id = self._trace_id()
        client_id = trace_id.split("-")[0]
        pid = str(uuid.uuid4())
        payload = {
            "code": 0,
            "codeType": "",
            "keyWord": city,
            "searchType": "D",
            "scenicCode": 0,
            "cityCodeOfUser": 0,
            "searchConditions": [
                {"type": "D_PROVINCE", "value": "T"},
                {"type": "SupportNormalSearch", "value": "T"},
                {"type": "DisplayTagIcon", "value": "F"},
            ],
            "head": {
                "platform": "PC",
                "clientId": client_id,
                "bu": "ibu",
                "group": "TRIP",
                "aid": "",
                "sid": "",
                "ouid": "",
                "caid": "",
                "csid": "",
                "couid": "",
                "region": "XX",
                "locale": "en-XX",
                "timeZone": "8",
                "currency": "CNY",
                "p": str(random.randint(10_000_000_000, 19_999_999_999)),
                "pageID": "10320668150",
                "deviceID": "PC",
                "clientVersion": "0",
                "frontend": {"vid": client_id, "sessionID": "1", "pvid": "1"},
                "extension": [
                    {"name": "cityId", "value": ""},
                    {"name": "checkIn", "value": ""},
                    {"name": "checkOut", "value": ""},
                    {"name": "region", "value": "XX"},
                ],
                "tripSub1": "",
                "qid": "",
                "pid": pid,
                "hotelExtension": {},
                "cid": client_id,
                "traceLogID": uuid.uuid4().hex[:13],
                "ticket": "",
                "href": "https://www.trip.com/hotels",
            },
        }
        url = "https://www.trip.com/htls/getKeyWordSearch?" + urlencode(
            {"htl_customtraceid": uuid.uuid4().hex, "x-traceID": trace_id}
        )
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "currency": "CNY",
                "locale": "en-XX",
                "p": payload["head"]["p"],
                "pid": pid,
                "referer": "https://www.trip.com/hotels",
                "trip-trace-id": trace_id,
                "user-agent": UA,
                "x-traceid": trace_id,
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        for item in data.get("keyWordSearchResults") or []:
            if item.get("resultType") != "CT":
                continue
            coords = item.get("coordinateInfos") or []
            preferred = next((x for x in coords if x.get("coordinateType") == "NORMAL"), None) or coords[0]
            candidate = CityCandidate(
                city_id=int(item["city"]["geoCode"]),
                city_name=item["city"]["currentLocaleName"],
                province_id=int(item["province"]["geoCode"]),
                country_id=int(item["country"]["geoCode"]),
                lat=float(preferred["latitude"]),
                lon=float(preferred["longitude"]),
                filter_id=((item.get("item") or {}).get("data") or {}).get("filterID") or f"19|{item['code']}",
                search_coordinate="|".join(
                    f"{x['coordinateType']}_{x['latitude']}_{x['longitude']}_{x.get('accuracy', 0)}"
                    for x in coords
                ),
            )
            self._store_city_candidate(city_cache_key, candidate)
            return candidate
        raise ReverseTravelFinderError("没有识别到这个城市。")

    def _fetch_supplemental_hotel_lists(
        self,
        city_query: str,
        city_candidate: CityCandidate,
        holiday: HolidayRange,
        compare_windows: list[dict[str, dt.date]],
        context,
        feature_filters: FeatureFilters,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        candidates = self._resolve_hotel_keyword_candidates(city_query, city_candidate)
        holiday_hotels: list[dict[str, Any]] = []
        comparison_hotels: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            page = context.new_page()
            try:
                candidate_holiday_hotels = self._fetch_hotel_list(
                    city_candidate=city_candidate,
                    check_in=holiday.start,
                    check_out=holiday.check_out,
                    limit=SUPPLEMENT_HOTEL_LIST_LIMIT,
                    page=page,
                    feature_filters=feature_filters,
                    keyword_candidate=candidate,
                )
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            if not candidate_holiday_hotels:
                continue
            holiday_hotels = self._merge_hotel_lists(holiday_hotels, candidate_holiday_hotels)
            candidate_comparison_hotels = self._fetch_hotel_lists_parallel(
                city_candidate=city_candidate,
                windows=compare_windows,
                limit=SUPPLEMENT_HOTEL_LIST_LIMIT,
                context=context,
                feature_filters=feature_filters,
                keyword_candidate=candidate,
            )
            for key, hotels in candidate_comparison_hotels.items():
                comparison_hotels[key] = self._merge_hotel_lists(comparison_hotels.get(key, []), hotels)
        return holiday_hotels, comparison_hotels

    def _resolve_hotel_keyword_candidates(
        self,
        city_query: str,
        city_candidate: CityCandidate,
    ) -> list[HotelKeywordCandidate]:
        candidates: list[HotelKeywordCandidate] = []
        seen_ids: set[str] = set()
        for keyword in self._supplement_keywords(city_query, city_candidate):
            try:
                results = self._keyword_search_results(keyword)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                continue
            for item in results:
                candidate = self._hotel_keyword_candidate_from_result(item, city_candidate)
                if candidate is None or candidate.hotel_id in seen_ids:
                    continue
                seen_ids.add(candidate.hotel_id)
                candidates.append(candidate)
                break
            if len(candidates) >= MAX_SUPPLEMENT_KEYWORD_CANDIDATES:
                break
        return candidates

    def _supplement_keywords(self, city_query: str, city_candidate: CityCandidate) -> list[str]:
        city_label = self._normalize_city_label(city_candidate.city_name or city_query)
        seeds = CITY_SUPPLEMENT_KEYWORDS.get(city_label, ())
        if not seeds:
            return []
        prefixes = [item for item in (city_query.strip(), city_label) if item]
        keywords: list[str] = []
        seen: set[str] = set()
        for seed in seeds:
            seed = seed.strip()
            if not seed:
                continue
            keyword = seed if any(seed.startswith(prefix) for prefix in prefixes) else f"{prefixes[0]}{seed}"
            if keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
        return keywords

    def _keyword_search_results(self, keyword: str) -> list[dict[str, Any]]:
        trace_id = self._trace_id()
        client_id = trace_id.split("-")[0]
        pid = str(uuid.uuid4())
        payload = {
            "code": 0,
            "codeType": "",
            "keyWord": keyword,
            "searchType": "D",
            "scenicCode": 0,
            "cityCodeOfUser": 0,
            "searchConditions": [
                {"type": "D_PROVINCE", "value": "T"},
                {"type": "SupportNormalSearch", "value": "T"},
                {"type": "DisplayTagIcon", "value": "F"},
            ],
            "head": {
                "platform": "PC",
                "clientId": client_id,
                "bu": "ibu",
                "group": "TRIP",
                "aid": "",
                "sid": "",
                "ouid": "",
                "caid": "",
                "csid": "",
                "couid": "",
                "region": "XX",
                "locale": "en-XX",
                "timeZone": "8",
                "currency": "CNY",
                "p": str(random.randint(10_000_000_000, 19_999_999_999)),
                "pageID": "10320668150",
                "deviceID": "PC",
                "clientVersion": "0",
                "frontend": {"vid": client_id, "sessionID": "1", "pvid": "1"},
                "extension": [
                    {"name": "cityId", "value": ""},
                    {"name": "checkIn", "value": ""},
                    {"name": "checkOut", "value": ""},
                    {"name": "region", "value": "XX"},
                ],
                "tripSub1": "",
                "qid": "",
                "pid": pid,
                "hotelExtension": {},
                "cid": client_id,
                "traceLogID": uuid.uuid4().hex[:13],
                "ticket": "",
                "href": "https://www.trip.com/hotels",
            },
        }
        url = "https://www.trip.com/htls/getKeyWordSearch?" + urlencode(
            {"htl_customtraceid": uuid.uuid4().hex, "x-traceID": trace_id}
        )
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "currency": "CNY",
                "locale": "en-XX",
                "p": payload["head"]["p"],
                "pid": pid,
                "referer": "https://www.trip.com/hotels",
                "trip-trace-id": trace_id,
                "user-agent": UA,
                "x-traceid": trace_id,
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("keyWordSearchResults") if isinstance(data, dict) else []
        return results if isinstance(results, list) else []

    def _hotel_keyword_candidate_from_result(
        self,
        item: dict[str, Any],
        city_candidate: CityCandidate,
    ) -> HotelKeywordCandidate | None:
        if item.get("resultType") != "H":
            return None
        result_city = item.get("city") or {}
        try:
            result_city_id = int(result_city.get("geoCode") or 0)
        except (TypeError, ValueError):
            result_city_id = 0
        if result_city_id and result_city_id != city_candidate.city_id:
            return None
        result_city_name = " ".join(
            str(result_city.get(key) or "")
            for key in ("currentLocaleName", "enusName")
        )
        if result_city_name and self._normalize_city_label(result_city_name) != self._normalize_city_label(city_candidate.city_name):
            return None

        item_data = ((item.get("item") or {}).get("data") or {})
        title = str(item_data.get("title") or item.get("word") or item.get("name") or "").strip()
        hotel_id = str(item_data.get("value") or item.get("code") or "").strip()
        if not title or not hotel_id:
            return None
        lat, lon, search_coordinate = self._hotel_keyword_coordinate(item, city_candidate)
        return HotelKeywordCandidate(
            hotel_id=hotel_id,
            title=title,
            filter_id=str(item_data.get("filterID") or f"31|{hotel_id}"),
            lat=lat,
            lon=lon,
            search_coordinate=search_coordinate,
        )

    def _hotel_keyword_coordinate(
        self,
        item: dict[str, Any],
        city_candidate: CityCandidate,
    ) -> tuple[float, float, str]:
        extra = ((item.get("item") or {}).get("extra") or {})
        formatted = str(extra.get("formattedCoordinateInfo") or "").strip()
        parts = formatted.split("|")
        if len(parts) >= 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                accuracy = parts[2] if len(parts) >= 3 else "0"
                return lat, lon, f"NORMAL_{lat}_{lon}_{accuracy}"
            except (TypeError, ValueError):
                pass
        return city_candidate.lat, city_candidate.lon, city_candidate.search_coordinate

    def _merge_hotel_lists(
        self,
        primary: list[dict[str, Any]],
        supplemental: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in [*primary, *supplemental]:
            key = self._hotel_merge_key(item)
            if not key:
                continue
            current = merged.get(key)
            if current is None or self._prefer_hotel_item(item, current):
                merged[key] = item
        return list(merged.values())

    def _hotel_merge_key(self, item: dict[str, Any]) -> str:
        hotel_id = str(item.get("hotel_id") or "").strip()
        room_type = self._classify_room_type(str(item.get("room_name") or ""))
        if hotel_id and room_type != "unknown":
            return f"{hotel_id}::{room_type}"
        return hotel_id or str(item.get("detail_href") or item.get("hotel_name") or "").strip()

    def _prefer_hotel_item(self, item: dict[str, Any], current: dict[str, Any]) -> bool:
        item_value = int(item.get("tax_total_value") or 0)
        current_value = int(current.get("tax_total_value") or 0)
        if item_value and current_value and item_value != current_value:
            return item_value < current_value
        return self._hotel_item_score(item) > self._hotel_item_score(current)

    def _fetch_hotel_list(
        self,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        limit: int,
        page,
        feature_filters: FeatureFilters,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> list[dict[str, Any]]:
        url = self._build_hotel_list_url(city_candidate, check_in, check_out, feature_filters, keyword_candidate)
        response_items: list[dict[str, Any]] = []
        response_lock = threading.Lock()
        nights = max(1, (check_out - check_in).days)

        def collect_response_items(response) -> None:
            items = self._extract_hotel_list_response_items(response, nights=nights)
            if not items:
                return
            with response_lock:
                response_items.extend(items)

        page.on("response", collect_response_items)
        try:
            for wait_ms in (2500, 4500):
                try:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(wait_ms)
                    except PlaywrightTimeoutError:
                        continue

                    items = self._collect_scrolled_hotel_list(
                        page=page,
                        limit=limit,
                        city_candidate=city_candidate,
                        check_in=check_in,
                        check_out=check_out,
                        response_items=response_items,
                        response_lock=response_lock,
                        feature_filters=feature_filters,
                    )
                    if items:
                        return items
                finally:
                    with response_lock:
                        response_items.clear()
        finally:
            try:
                page.remove_listener("response", collect_response_items)
            except (AttributeError, ValueError):
                pass
        return []

    def _fetch_hotel_lists_parallel(
        self,
        city_candidate: CityCandidate,
        windows: list[dict[str, dt.date]],
        limit: int,
        context,
        feature_filters: FeatureFilters,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        results: dict[str, list[dict[str, Any]]] = {}
        for batch in self._chunked(windows, COMPARE_PAGE_BATCH_SIZE):
            states: list[dict[str, Any]] = []
            try:
                for window in batch:
                    page = context.new_page()
                    state = self._create_hotel_list_state(
                        page=page,
                        city_candidate=city_candidate,
                        check_in=window["check_in"],
                        check_out=window["check_out"],
                        limit=limit,
                        feature_filters=feature_filters,
                        keyword_candidate=keyword_candidate,
                    )
                    page.on("response", state["handler"])
                    states.append(state)

                for state in states:
                    try:
                        state["page"].goto(state["url"], wait_until="domcontentloaded", timeout=60000)
                    except PlaywrightTimeoutError:
                        state["load_failed"] = True

                active = [state for state in states if not state.get("load_failed")]
                if active:
                    active[0]["page"].wait_for_timeout(4500)

                for round_index in range(MAX_SCROLL_ROUNDS + 1):
                    active = [state for state in states if not state["done"] and not state.get("load_failed")]
                    if not active:
                        break

                    for state in active:
                        self._collect_hotel_list_state_snapshot(state)
                    for state in active:
                        self._update_hotel_list_state_progress(state, round_index)

                    active = [state for state in states if not state["done"] and not state.get("load_failed")]
                    if not active:
                        break

                    for state in active:
                        self._advance_hotel_list_scroll(state["page"], wait_ms=0)
                    active[0]["page"].wait_for_timeout(SCROLL_WAIT_MS)

                for state in states:
                    self._drain_hotel_list_state_response_items(state)
                    results[state["key"]] = self._finalize_hotel_items(
                        list(state["collected"].values())[:limit],
                        city_candidate=city_candidate,
                        check_in=state["check_in"],
                        check_out=state["check_out"],
                        feature_filters=state["feature_filters"],
                    )
            finally:
                for state in states:
                    try:
                        state["page"].remove_listener("response", state["handler"])
                    except (AttributeError, ValueError):
                        pass
                    try:
                        state["page"].close()
                    except Exception:
                        pass

        return results

    def _create_hotel_list_state(
        self,
        page,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        limit: int,
        feature_filters: FeatureFilters,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> dict[str, Any]:
        response_items: list[dict[str, Any]] = []
        response_lock = threading.Lock()
        nights = max(1, (check_out - check_in).days)

        def collect_response_items(response) -> None:
            items = self._extract_hotel_list_response_items(response, nights=nights)
            if not items:
                return
            with response_lock:
                response_items.extend(items)

        return {
            "key": check_in.isoformat(),
            "page": page,
            "handler": collect_response_items,
            "url": self._build_hotel_list_url(
                city_candidate,
                check_in,
                check_out,
                feature_filters,
                keyword_candidate,
            ),
            "check_in": check_in,
            "check_out": check_out,
            "feature_filters": feature_filters,
            "limit": limit,
            "response_items": response_items,
            "response_lock": response_lock,
            "collected": {},
            "last_count": 0,
            "stable_rounds": 0,
            "done": False,
            "load_failed": False,
        }

    def _collect_hotel_list_state_snapshot(self, state: dict[str, Any]) -> None:
        self._drain_hotel_list_state_response_items(state)
        self._add_hotel_list_state_items(
            state,
            self._extract_hotel_list_snapshot(state["page"], state["limit"]),
        )
        self._drain_hotel_list_state_response_items(state)

    def _drain_hotel_list_state_response_items(self, state: dict[str, Any]) -> None:
        with state["response_lock"]:
            items = state["response_items"][:]
            state["response_items"].clear()
        self._add_hotel_list_state_items(state, items)

    def _add_hotel_list_state_items(self, state: dict[str, Any], items: list[dict[str, Any]]) -> None:
        collected = state["collected"]
        for item in items:
            key = item["hotel_id"] or item["detail_href"] or item["hotel_name"]
            if not key:
                continue
            if key not in collected:
                collected[key] = item
            elif self._hotel_item_score(item) > self._hotel_item_score(collected[key]):
                collected[key] = item
            if len(collected) >= state["limit"]:
                state["done"] = True
                break

    def _update_hotel_list_state_progress(self, state: dict[str, Any], round_index: int) -> None:
        current_count = len(state["collected"])
        if current_count >= state["limit"]:
            state["done"] = True
            return
        if round_index >= 1 and current_count == state["last_count"]:
            state["stable_rounds"] += 1
        else:
            state["stable_rounds"] = 0
        if state["stable_rounds"] >= STABLE_SCROLL_ROUNDS:
            state["done"] = True
            return
        state["last_count"] = current_count

    def _chunked(self, items: list[Any], size: int) -> list[list[Any]]:
        return [items[index : index + size] for index in range(0, len(items), max(1, size))]

    def _collect_scrolled_hotel_list(
        self,
        page,
        limit: int,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        response_items: list[dict[str, Any]],
        response_lock: threading.Lock,
        feature_filters: FeatureFilters,
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        last_count = 0
        stable_rounds = 0

        def add_items(items: list[dict[str, Any]]) -> None:
            for item in items:
                key = item["hotel_id"] or item["detail_href"] or item["hotel_name"]
                if not key:
                    continue
                if key not in collected:
                    collected[key] = item
                elif self._hotel_item_score(item) > self._hotel_item_score(collected[key]):
                    collected[key] = item
                if len(collected) >= limit:
                    break

        def drain_response_items() -> None:
            with response_lock:
                items = response_items[:]
                response_items.clear()
            add_items(items)

        for round_index in range(MAX_SCROLL_ROUNDS + 1):
            drain_response_items()
            add_items(self._extract_hotel_list_snapshot(page, limit))
            drain_response_items()

            current_count = len(collected)
            if current_count >= limit:
                break
            if round_index >= 1 and current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= STABLE_SCROLL_ROUNDS:
                break

            last_count = current_count
            self._advance_hotel_list_scroll(page)

        drain_response_items()
        return self._finalize_hotel_items(
            list(collected.values())[:limit],
            city_candidate=city_candidate,
            check_in=check_in,
            check_out=check_out,
            feature_filters=feature_filters,
        )

    def _hotel_item_score(self, item: dict[str, Any]) -> int:
        score = sum(
            1
            for key in ("detail_href", "room_name", "room_price_text", "tax_total_text")
            if item.get(key)
        )
        if item.get("_source") == "api":
            score += 2
        if not self._is_placeholder_hotel_name(item.get("hotel_name") or ""):
            score += 1
        return score

    def _extract_hotel_list_response_items(self, response, nights: int) -> list[dict[str, Any]]:
        if "/htls/getHotelList" not in response.url:
            return []
        try:
            data = response.json()
        except Exception:
            return []
        hotel_list = data.get("hotelList") if isinstance(data, dict) else None
        if not isinstance(hotel_list, list):
            return []
        return self._normalize_hotel_api_items(hotel_list, nights=nights)

    def _normalize_hotel_api_items(self, hotels: list[dict[str, Any]], nights: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in hotels:
            basic = row.get("hotelBasicInfo") or {}
            room = row.get("roomInfo") or {}
            min_room = row.get("minRoomInfo") or {}
            position = row.get("positionInfo") or {}

            hotel_id = str(basic.get("hotelId") or "").strip()
            hotel_name = str(basic.get("hotelName") or basic.get("hotelEnName") or "").strip()
            room_name = str(
                room.get("physicalRoomName")
                or room.get("name")
                or min_room.get("roomName")
                or ""
            ).strip()
            room_price_text = self._format_api_price_text(basic.get("price"))
            tax_total_text = self._extract_api_total_price_text(
                basic.get("priceExplanation"),
                basic.get("onlineAndShopTaxPrice") or basic.get("onlineTaxPrice"),
                nights=nights,
            )
            if not hotel_id or not hotel_name or not tax_total_text:
                continue
            city_name = str(position.get("cityName") or position.get("cityNameEn") or "").strip()
            area_hint = self._hotel_area_hint(row)
            area_name = self._infer_area_name(
                city_name=city_name,
                hotel_name=hotel_name,
                area_text=area_hint,
            )
            feature_flags = self._extract_api_feature_flags(row)
            items.append(
                {
                    "hotel_id": hotel_id,
                    "hotel_name": hotel_name,
                    "detail_href": "",
                    "room_name": room_name,
                    "room_price_text": room_price_text,
                    "tax_total_text": tax_total_text,
                    "area_name": area_name,
                    "area_hint": area_hint,
                    "area_source": "Trip.com 位置",
                    **feature_flags,
                    "_source": "api",
                }
            )
        return items

    def _extract_api_feature_flags(self, row: dict[str, Any]) -> dict[str, bool | None]:
        text = json.dumps(row, ensure_ascii=False).lower()
        return {
            "has_pool": self._text_has_pool_feature(text),
            "has_child_facility": self._text_has_child_feature(text),
            "is_advanced": self._extract_advanced_flag(row),
        }

    def _text_has_pool_feature(self, text: str) -> bool | None:
        if re.search(r"no\s+pool|without\s+pool|无泳池|無泳池|没有泳池|沒有泳池", text, flags=re.IGNORECASE):
            return False
        if re.search(r"\bpool\b|swimming|泳池|游泳", text, flags=re.IGNORECASE):
            return True
        return None

    def _text_has_child_feature(self, text: str) -> bool | None:
        if re.search(
            r"no\s+(children|kids)|without\s+(children|kids)|无儿童|無兒童|没有儿童|沒有兒童|无亲子|無親子",
            text,
            flags=re.IGNORECASE,
        ):
            return False
        if re.search(
            r"playground|family-friendly|parent-child|children|kid|儿童|兒童|亲子|親子",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        return None

    def _feature_values_by_key(self, value: Any, key_tokens: tuple[str, ...]) -> list[Any]:
        found: list[Any] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if any(token in str(key).lower() for token in key_tokens):
                    found.append(child)
                found.extend(self._feature_values_by_key(child, key_tokens))
        elif isinstance(value, list):
            for child in value:
                found.extend(self._feature_values_by_key(child, key_tokens))
        return found

    def _classify_advanced_value(self, value: Any) -> bool | None:
        if value in ("", None):
            return None
        text = str(value).lower()
        match = re.search(r"\d+(?:\.\d+)?", text)
        if match:
            return float(match.group(0)) >= 4
        if re.search(r"五星|五钻|五鑽|四星|四钻|四鑽|luxury|premium|upscale|deluxe", text):
            return True
        if re.search(r"三星|三钻|三鑽|二星|二钻|二鑽|经济|經濟|舒适|舒適|budget|comfort", text):
            return False
        return None

    def _extract_advanced_flag(self, row: dict[str, Any]) -> bool | None:
        basic = row.get("hotelBasicInfo") or {}
        candidates = [
            basic.get("star"),
            basic.get("starRating"),
            basic.get("hotelStar"),
            basic.get("hotelStarLevel"),
            basic.get("diamond"),
            basic.get("diamondLevel"),
        ]
        candidates.extend(self._feature_values_by_key(row, ("star", "diamond")))
        for value in candidates:
            classified = self._classify_advanced_value(value)
            if classified is not None:
                return classified
        return None

    def _hotel_area_hint(self, row: dict[str, Any]) -> str:
        basic = row.get("hotelBasicInfo") or {}
        position = row.get("positionInfo") or {}
        values: list[str] = [
            str(basic.get("hotelName") or ""),
            str(basic.get("hotelEnName") or ""),
            str(basic.get("hotelAddress") or ""),
            str(position.get("positionDesc") or ""),
            str(position.get("positionName") or ""),
            str(position.get("cityName") or ""),
            str(position.get("cityNameEn") or ""),
        ]
        for key in ("zoneNames", "transport"):
            value = position.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif value:
                values.append(str(value))
        return " ".join(item.strip() for item in values if item and str(item).strip())

    def _infer_area_name(self, city_name: str, hotel_name: str, area_text: str) -> str:
        text = " ".join([city_name or "", hotel_name or "", area_text or ""]).lower()
        city = city_name or ""
        normalized_city = self._normalize_city_label(city)
        city_patterns: dict[str, list[tuple[tuple[str, ...], str]]] = {
            "深圳": [
                (("wecc", "world exhibition", "international convention and exhibition", "international exhibition", "国际会展", "國際會展", "會展中心", "会展中心"), "深圳国际会展中心片区"),
                (("guangming", "光明", "hongqiao", "虹桥", "虹橋"), "光明虹桥公园片区"),
                (("guanlan", "mission hills", "觀瀾", "观澜"), "深圳观澜片区"),
                (("nanshan", "南山"), "深圳南山片区"),
                (("shenzhen bay", "深圳湾", "深圳灣"), "深圳湾片区"),
                (("qianhai", "前海"), "深圳前海片区"),
                (("shekou", "蛇口"), "深圳蛇口片区"),
                (("futian", "福田"), "深圳福田中心区"),
                (("bao'an", "baoan", "宝安", "寶安"), "深圳宝安片区"),
                (("longhua", "龙华", "龍華"), "深圳龙华片区"),
                (("longgang", "龙岗", "龍崗"), "深圳龙岗片区"),
                (("yantian", "盐田", "鹽田"), "深圳盐田片区"),
                (("luohu", "罗湖", "羅湖"), "深圳罗湖片区"),
                (("pingshan", "坪山"), "深圳坪山片区"),
            ],
            "广州": [
                (("zengcheng", "增城"), "广州增城片区"),
                (("pazhou", "canton fair", "琶洲", "广交会", "廣交會", "會展中心", "会展中心"), "广州琶洲会展片区"),
                (("tianhe", "天河"), "广州天河片区"),
                (("yuexiu", "越秀"), "广州越秀片区"),
                (("haizhu", "海珠"), "广州海珠片区"),
                (("panyu", "番禺"), "广州番禺片区"),
                (("baiyun", "白云", "白雲"), "广州白云片区"),
                (("huangpu", "黄埔", "黃埔"), "广州黄埔片区"),
                (("huadu", "花都"), "广州花都片区"),
                (("conghua", "从化", "從化"), "广州从化片区"),
                (("nansha", "南沙"), "广州南沙片区"),
            ],
            "东莞": [
                (("songshan lake", "songshanhu", "松山湖"), "东莞松山湖片区"),
                (("houjie", "厚街", "guangdong modern international exhibition", "modern international exhibition", "现代国际展览", "現代國際展覽", "国际会展", "國際會展", "會展中心", "会展中心", "exhibition center"), "东莞厚街会展片区"),
                (("humen", "虎门", "虎門"), "东莞虎门片区"),
                (("chang'an", "changan", "长安", "長安"), "东莞长安片区"),
                (("nancheng", "南城"), "东莞南城片区"),
                (("dongcheng", "东城", "東城"), "东莞东城片区"),
                (("guancheng", "莞城"), "东莞莞城片区"),
                (("wanjiang", "万江", "萬江"), "东莞万江片区"),
                (("changping", "常平"), "东莞常平片区"),
                (("tangxia", "塘厦", "塘廈"), "东莞塘厦片区"),
                (("fenggang", "凤岗", "鳳崗"), "东莞凤岗片区"),
                (("dalang", "大朗"), "东莞大朗片区"),
                (("liaobu", "寮步"), "东莞寮步片区"),
                (("dalingshan", "大岭山", "大嶺山"), "东莞大岭山片区"),
                (("huangjiang", "黄江", "黃江"), "东莞黄江片区"),
                (("machong", "麻涌"), "东莞麻涌片区"),
                (("shilong", "石龙", "石龍"), "东莞石龙片区"),
                (("qingxi", "清溪"), "东莞清溪片区"),
                (("shatian", "沙田"), "东莞沙田片区"),
                (("daojiao", "dao jiao", "道滘"), "东莞道滘片区"),
                (("binhaiwan", "滨海湾", "濱海灣"), "东莞滨海湾片区"),
                (("qiaotou", "桥头", "橋頭"), "东莞桥头片区"),
                (("shipai", "石排"), "东莞石排片区"),
                (("gaobu", "高埗"), "东莞高埗片区"),
            ],
            "惠州": [
                (("xunliao", "巽寮", "巽寮湾", "巽寮灣"), "惠州巽寮湾片区"),
                (("double moon bay", "shuangyue", "双月湾", "雙月灣"), "惠州双月湾片区"),
                (("daya bay", "dayawan", "大亚湾", "大亞灣"), "惠州大亚湾片区"),
                (("huidong", "惠东", "惠東"), "惠州惠东片区"),
                (("huiyang", "惠阳", "惠陽", "danshui", "淡水"), "惠州惠阳片区"),
                (("boluo", "博罗", "博羅", "shiwan", "石湾", "石灣"), "惠州博罗片区"),
                (("zhongkai", "仲恺", "仲愷", "chenjiang", "陈江", "陳江", "tcl"), "惠州仲恺片区"),
                (("huicheng", "惠城", "jiangbei", "江北", "west lake", "西湖", "51新天地"), "惠州惠城片区"),
                (("longmen", "龙门", "龍門", "nankunshan", "南昆山"), "惠州龙门片区"),
                (("luofu", "罗浮山", "羅浮山"), "惠州罗浮山片区"),
            ],
            "中山": [
                (("shiqi", "石岐", "兴中", "興中"), "中山石岐片区"),
                (("east district", "dongqu", "东区", "東區", "利和", "lihe"), "中山东区片区"),
                (("west district", "xiqu", "西区", "西區"), "中山西区片区"),
                (("south district", "nanqu", "南区", "南區"), "中山南区片区"),
                (("torch", "huoju", "火炬", "开发区", "開發區", "zhongshan port", "中山港"), "中山火炬开发区片区"),
                (("xiaolan", "小榄", "小欖"), "中山小榄片区"),
                (("guzhen", "古镇", "古鎮", "灯都", "燈都"), "中山古镇片区"),
                (("sanxiang", "三乡", "三鄉"), "中山三乡片区"),
                (("tanzhou", "坦洲"), "中山坦洲片区"),
                (("nanlang", "南朗", "翠亨", "cuiheng"), "中山南朗翠亨片区"),
                (("dongfeng", "东凤", "東鳳"), "中山东凤片区"),
                (("shaxi", "沙溪"), "中山沙溪片区"),
                (("dachong", "大涌", "大湧"), "中山大涌片区"),
                (("gangkou", "港口"), "中山港口片区"),
                (("minzhong", "民众", "民眾"), "中山民众片区"),
                (("huangpu", "黄圃", "黃圃"), "中山黄圃片区"),
                (("nantou", "南头", "南頭"), "中山南头片区"),
                (("banfu", "板芙"), "中山板芙片区"),
                (("henglan", "横栏", "橫欄"), "中山横栏片区"),
                (("wuguishan", "五桂山"), "中山五桂山片区"),
            ],
            "江门": [
                (("xinhui", "新会", "新會", "gudou", "古兜"), "江门新会片区"),
                (("heshan", "鹤山", "鶴山", "gulao", "古劳", "古勞"), "江门鹤山片区"),
                (("taishan", "台山", "下川岛", "下川島", "naqin", "那琴", "川岛", "川島"), "江门台山片区"),
                (("kaiping", "开平", "開平", "chikan", "赤坎"), "江门开平赤坎片区"),
                (("enping", "恩平", "泉林", "温泉", "溫泉"), "江门恩平温泉片区"),
                (("pengjiang", "蓬江", "五邑", "万达", "萬達", "利和", "白石"), "江门蓬江片区"),
                (("jianghai", "江海", "高新", "礼乐", "禮樂"), "江门江海片区"),
                (("yinhuwan", "银湖湾", "銀湖灣", "滨海新区", "濱海新區"), "江门银湖湾片区"),
            ],
            "河源": [
                (("wanlv", "wanlu", "万绿湖", "萬綠湖", "新丰江", "新豐江"), "河源万绿湖片区"),
                (("bavaria", "巴伐利亚", "巴伐利亞", "福源寺"), "河源巴伐利亚庄园片区"),
                (("ketianxia", "客天下", "春沐源", "chunmuyuan"), "河源客天下片区"),
                (("yuancheng", "源城", "兴源", "興源", "亚洲第一高喷泉", "亞洲第一高噴泉"), "河源源城片区"),
                (("dongyuan", "东源", "東源", "康禾"), "河源东源片区"),
                (("longchuan", "龙川", "龍川"), "河源龙川片区"),
                (("zijin", "紫金"), "河源紫金片区"),
                (("lianping", "连平", "連平"), "河源连平片区"),
                (("heping", "和平", "温泉", "溫泉"), "河源和平温泉片区"),
            ],
            "肇庆": [
                (("qixingyan", "seven star", "七星岩", "七星巖", "星湖", "牌坊", "星湖湾", "星湖灣"), "肇庆七星岩星湖片区"),
                (("dinghu", "鼎湖", "鼎湖山", "砚洲", "硯洲"), "肇庆鼎湖山片区"),
                (("duanzhou", "端州", "敏捷广场", "敏捷廣場", "宋城墙", "宋城牆"), "肇庆端州片区"),
                (("gaoyao", "高要", "金利"), "肇庆高要片区"),
                (("sihui", "四会", "四會", "大旺", "高新区", "高新區"), "肇庆四会片区"),
                (("guangning", "广宁", "廣寧"), "肇庆广宁片区"),
                (("huaiji", "怀集", "懷集"), "肇庆怀集片区"),
                (("fengkai", "封开", "封開"), "肇庆封开片区"),
                (("deqing", "德庆", "德慶", "盘龙峡", "盤龍峽"), "肇庆德庆片区"),
            ],
            "珠海": [
                (("hengqin", "横琴", "橫琴", "chimelong", "长隆", "長隆", "ocean kingdom", "海洋王国", "海洋王國"), "珠海横琴长隆片区"),
                (("xiangzhou", "香洲", "情侣路", "情侶路", "海滨", "海濱", "日月贝", "日月貝", "野狸岛", "野狸島"), "珠海情侣路香洲片区"),
                (("gongbei", "拱北", "口岸", "港珠澳", "港珠澳大桥", "港珠澳大橋"), "珠海拱北口岸片区"),
                (("jida", "吉大", "免税", "免稅"), "珠海吉大片区"),
                (("wanzai", "湾仔", "灣仔", "会展", "會展", "十字门", "十字門"), "珠海湾仔会展片区"),
                (("jinwan", "金湾", "金灣", "航空新城", "机场", "機場", "sanzao", "三灶"), "珠海金湾航空新城片区"),
                (("tangjia", "唐家", "唐家湾", "唐家灣", "高新区", "高新區", "淇澳"), "珠海唐家湾片区"),
                (("doumen", "斗门", "斗門", "井岸", "御温泉", "御溫泉"), "珠海斗门片区"),
            ],
            "韶关": [
                (("danxia", "丹霞", "丹霞山", "仁化"), "韶关丹霞山片区"),
                (("nanhua", "南华寺", "南華寺", "caoxi", "曹溪", "马坝", "馬壩"), "韶关南华寺曹溪片区"),
                (("ruyuan", "乳源", "大峡谷", "大峽谷", "云门山", "雲門山", "南岭", "南嶺"), "韶关乳源大峡谷片区"),
                (("zhenjiang", "浈江", "湞江", "wujiang", "武江", "韶关站", "韶關站", "高铁站", "高鐵站", "摩尔城", "摩爾城", "百年东街", "百年東街"), "韶关市区片区"),
                (("nanxiong", "南雄", "珠玑", "珠璣", "帽子峰"), "韶关南雄片区"),
                (("lechang", "乐昌", "樂昌", "坪石"), "韶关乐昌片区"),
                (("wengyuan", "翁源", "翁山源", "始兴", "始興"), "韶关翁源始兴片区"),
                (("qujiang", "曲江", "经律论", "經律論", "小坑", "汤泉谷", "湯泉谷"), "韶关曲江片区"),
                (("xinfeng", "新丰", "新豐"), "韶关新丰片区"),
            ],
            "汕尾": [
                (("jinding", "jinding bay", "金町", "金町湾", "金町灣", "保利金町湾", "保利金町灣"), "汕尾金町湾片区"),
                (("red bay", "honghai", "红海湾", "紅海灣", "遮浪", "zhelang"), "汕尾红海湾片区"),
                (("haifeng", "海丰", "海豐", "莲花山", "蓮花山"), "汕尾海丰片区"),
                (("lufeng", "陆丰", "陸豐", "碣石", "甲子", "金厢", "金廂"), "汕尾陆丰片区"),
                (("luhe", "陆河", "陸河"), "汕尾陆河片区"),
                (("chengqu", "城区", "城區", "香洲", "品清湖", "凤山", "鳳山", "汕尾站", "善美"), "汕尾市区片区"),
                (("shenshan", "深汕", "鹅埠", "鵝埠", "鲘门", "鮜門"), "汕尾深汕合作区片区"),
            ],
        }
        patterns = city_patterns.get(normalized_city, [])
        if not patterns and not normalized_city:
            if "shenzhen" in text or "深圳" in text:
                patterns = city_patterns["深圳"]
            elif "guangzhou" in text or "广州" in text or "廣州" in text:
                patterns = city_patterns["广州"]
            elif "dongguan" in text or "东莞" in text or "東莞" in text:
                patterns = city_patterns["东莞"]
            elif "huizhou" in text or "惠州" in text:
                patterns = city_patterns["惠州"]
            elif "jiangmen" in text or "江门" in text or "江門" in text:
                patterns = city_patterns["江门"]
            elif "heyuan" in text or "河源" in text:
                patterns = city_patterns["河源"]
            elif "zhaoqing" in text or "肇庆" in text or "肇慶" in text:
                patterns = city_patterns["肇庆"]
            elif "zhuhai" in text or "珠海" in text:
                patterns = city_patterns["珠海"]
            elif "shaoguan" in text or "韶关" in text or "韶關" in text:
                patterns = city_patterns["韶关"]
            elif "shanwei" in text or "汕尾" in text:
                patterns = city_patterns["汕尾"]
        for keywords, area_name in patterns:
            if any(keyword.lower() in text for keyword in keywords):
                return area_name

        return f"{normalized_city}区域待确认" if normalized_city else "区域待确认"

    def _normalize_city_label(self, city_name: str) -> str:
        text = (city_name or "").strip().lower()
        if "shenzhen" in text or "深圳" in city_name:
            return "深圳"
        if "guangzhou" in text or "广州" in city_name or "廣州" in city_name:
            return "广州"
        if "dongguan" in text or "东莞" in city_name or "東莞" in city_name:
            return "东莞"
        if "zhongshan" in text or "中山" in city_name:
            return "中山"
        if (
            "jiangmen" in text
            or "江门" in city_name
            or "江門" in city_name
            or "xinhui" in text
            or "新会" in city_name
            or "新會" in city_name
            or "heshan" in text
            or "鹤山" in city_name
            or "鶴山" in city_name
            or "taishan" in text
            or "台山" in city_name
            or "kaiping" in text
            or "开平" in city_name
            or "開平" in city_name
            or "enping" in text
            or "恩平" in city_name
        ):
            return "江门"
        if (
            "heyuan" in text
            or "河源" in city_name
            or "yuancheng" in text
            or "源城" in city_name
            or "dongyuan" in text
            or "东源" in city_name
            or "東源" in city_name
            or "longchuan" in text
            or "龙川" in city_name
            or "龍川" in city_name
            or "zijin" in text
            or "紫金" in city_name
            or "lianping" in text
            or "连平" in city_name
            or "連平" in city_name
        ):
            return "河源"
        if (
            "zhaoqing" in text
            or "肇庆" in city_name
            or "肇慶" in city_name
            or "duanzhou" in text
            or "端州" in city_name
            or "dinghu" in text
            or "鼎湖" in city_name
            or "gaoyao" in text
            or "高要" in city_name
            or "sihui" in text
            or "四会" in city_name
            or "四會" in city_name
        ):
            return "肇庆"
        if (
            "zhuhai" in text
            or "珠海" in city_name
            or "xiangzhou" in text
            or "香洲" in city_name
            or "hengqin" in text
            or "横琴" in city_name
            or "橫琴" in city_name
            or "gongbei" in text
            or "拱北" in city_name
            or "jinwan" in text
            or "金湾" in city_name
            or "金灣" in city_name
            or "doumen" in text
            or "斗门" in city_name
            or "斗門" in city_name
        ):
            return "珠海"
        if (
            "macau" in text
            or "macao" in text
            or "澳门" in city_name
            or "澳門" in city_name
        ):
            return "澳门"
        if (
            "shaoguan" in text
            or "韶关" in city_name
            or "韶關" in city_name
            or "danxia" in text
            or "丹霞" in city_name
            or "renhua" in text
            or "仁化" in city_name
            or "ruyuan" in text
            or "乳源" in city_name
            or "nanxiong" in text
            or "南雄" in city_name
            or "lechang" in text
            or "乐昌" in city_name
            or "樂昌" in city_name
            or "qujiang" in text
            or "曲江" in city_name
        ):
            return "韶关"
        if (
            "shanwei" in text
            or "汕尾" in city_name
            or "haifeng" in text
            or "海丰" in city_name
            or "海豐" in city_name
            or "lufeng" in text
            or "陆丰" in city_name
            or "陸豐" in city_name
            or "luhe" in text
            or "陆河" in city_name
            or "陸河" in city_name
            or "honghai" in text
            or "红海湾" in city_name
            or "紅海灣" in city_name
            or "jinding" in text
            or "金町" in city_name
            or "shenshan" in text
            or "深汕" in city_name
        ):
            return "汕尾"
        if (
            "qingyuan" in text
            or "清远" in city_name
            or "清遠" in city_name
            or "yingde" in text
            or "英德" in city_name
        ):
            return "清远"
        if (
            "chenzhou" in text
            or "郴州" in city_name
            or "yizhang" in text
            or "宜章" in city_name
        ):
            return "郴州"
        if (
            "ganzhou" in text
            or "赣州" in city_name
            or "贛州" in city_name
        ):
            return "赣州"
        if (
            "yunfu" in text
            or "云浮" in city_name
            or "雲浮" in city_name
            or "yunan" in text
            or "云安" in city_name
            or "雲安" in city_name
            or "luoding" in text
            or "罗定" in city_name
            or "羅定" in city_name
        ):
            return "云浮"
        if (
            "foshan" in text
            or "佛山" in city_name
            or "shunde" in text
            or "顺德" in city_name
            or "順德" in city_name
            or "jiujiang" in text
            or "九江" in city_name
        ):
            return "佛山"
        if (
            "huizhou" in text
            or "惠州" in city_name
            or "boluo" in text
            or "博罗" in city_name
            or "博羅" in city_name
            or "huidong" in text
            or "惠东" in city_name
            or "惠東" in city_name
            or "huiyang" in text
            or "惠阳" in city_name
            or "惠陽" in city_name
            or "longmen" in text
            or "龙门" in city_name
            or "龍門" in city_name
        ):
            return "惠州"
        return (city_name or "").strip()

    def _is_outside_search_city(
        self,
        item: dict[str, Any],
        city_candidate: CityCandidate,
        detail_url: str,
    ) -> bool:
        expected = self._normalize_city_label(city_candidate.city_name)
        if not expected:
            return False
        detail_city = self._detect_city_label_from_detail_url(detail_url)
        if detail_city:
            return detail_city != expected

        text = " ".join(
            str(value or "")
            for value in (
                item.get("hotel_name"),
                item.get("area_name"),
                item.get("area_hint"),
                item.get("detail_href"),
            )
        )
        if self._text_mentions_city_label(text, expected):
            return False
        detected = self._detect_city_label_from_text(text)
        return bool(detected and detected != expected)

    def _detect_city_label_from_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        query = parse_qs(urlparse(detail_url).query)
        for key in ("cityName", "cityEnName"):
            value = (query.get(key) or [""])[0]
            city_label = self._normalize_city_label(value)
            if city_label and city_label != value.strip():
                return city_label
        city_id = (query.get("cityId") or [""])[0]
        return CITY_ID_LABELS.get(str(city_id).strip(), "")

    def _detect_city_label_from_text(self, text: str) -> str:
        for city_label, keywords in CITY_LABEL_KEYWORDS.items():
            if self._text_has_any_city_keyword(text, keywords):
                return city_label
        return ""

    def _text_mentions_city_label(self, text: str, city_label: str) -> bool:
        return self._text_has_any_city_keyword(text, CITY_LABEL_KEYWORDS.get(city_label, ()))

    def _text_has_any_city_keyword(self, text: str, keywords: tuple[str, ...]) -> bool:
        lowered = (text or "").lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    def _build_area_recommendations(self, choices: list[dict[str, Any]], city_name: str) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for item in choices:
            area_name = self._choice_area_name(item, city_name)
            if self._is_generic_area_name(area_name):
                continue
            group = groups.setdefault(
                area_name,
                {
                    "area_name": area_name,
                    "hotel_count": 0,
                    "lower_price_hotel_count": 0,
                    "slightly_higher_hotel_count": 0,
                    "holiday_values": [],
                    "diff_values": [],
                    "room_types": set(),
                    "representative_hotels": [],
                },
            )
            diff = int(item.get("price_diff_nightly") or 0)
            group["hotel_count"] += 1
            if diff <= 0:
                group["lower_price_hotel_count"] += 1
            else:
                group["slightly_higher_hotel_count"] += 1
            group["holiday_values"].append(int(item.get("holiday_avg_nightly_tax_total_value") or 0))
            group["diff_values"].append(diff)
            if item.get("room_type_label"):
                group["room_types"].add(item["room_type_label"])
            if len(group["representative_hotels"]) < 4:
                group["representative_hotels"].append(item.get("hotel_name") or item.get("hotel_original_name") or "")

        recommendations: list[dict[str, Any]] = []
        for group in groups.values():
            holiday_values = [value for value in group["holiday_values"] if value > 0]
            diff_values = group["diff_values"] or [0]
            avg_holiday = round(sum(holiday_values) / len(holiday_values)) if holiday_values else 0
            avg_diff = round(sum(diff_values) / len(diff_values))
            lower_ratio = group["lower_price_hotel_count"] / max(1, group["hotel_count"])
            recommendations.append(
                {
                    "area_name": group["area_name"],
                    "hotel_count": group["hotel_count"],
                    "lower_price_hotel_count": group["lower_price_hotel_count"],
                    "slightly_higher_hotel_count": group["slightly_higher_hotel_count"],
                    "lower_price_ratio": lower_ratio,
                    "average_holiday_nightly_tax_total_value": avg_holiday,
                    "average_holiday_nightly_tax_total_price": self._format_cny(avg_holiday),
                    "average_price_diff_nightly": avg_diff,
                    "average_price_diff_nightly_text": self._format_cny_diff(avg_diff),
                    "room_type_labels": sorted(group["room_types"]),
                    "representative_hotels": [name for name in group["representative_hotels"] if name],
                    "reason": self._area_recommendation_reason(group["hotel_count"], group["lower_price_hotel_count"], avg_diff),
                }
            )

        recommendations.sort(
            key=lambda item: (
                -item["lower_price_ratio"],
                -item["lower_price_hotel_count"],
                -item["hotel_count"],
                item["average_price_diff_nightly"],
                item["average_holiday_nightly_tax_total_value"],
            )
        )
        recommendations = self._add_default_area_recommendations(recommendations, city_name, choices)
        return recommendations[:8]

    def _is_generic_area_name(self, area_name: str) -> bool:
        text = area_name or ""
        return "热门酒店片区" in text or "区域待确认" in text

    def _choice_area_name(self, item: dict[str, Any], city_name: str) -> str:
        raw_area = str(item.get("area_name") or "").strip()
        if raw_area and not self._is_generic_area_name(raw_area):
            return raw_area
        return self._infer_area_name(
            city_name=city_name,
            hotel_name=" ".join(
                str(value or "")
                for value in (
                    item.get("hotel_original_name"),
                    item.get("hotel_name"),
                )
            ),
            area_text=str(item.get("area_hint") or ""),
        )

    def _refresh_choice_area_names(self, choices: list[dict[str, Any]], city_name: str) -> None:
        for item in choices:
            area_name = self._choice_area_name(item, city_name)
            item["area_name"] = "" if self._is_generic_area_name(area_name) else area_name

    def _add_default_area_recommendations(
        self,
        recommendations: list[dict[str, Any]],
        city_name: str,
        choices: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not choices or len(recommendations) >= 3:
            return recommendations
        city_label = self._normalize_city_label(city_name)
        defaults = CITY_DEFAULT_AREA_NAMES.get(city_label, ())
        if not defaults:
            return recommendations
        existing = {item["area_name"] for item in recommendations}
        filled = list(recommendations)
        for area_name in defaults:
            if area_name in existing:
                continue
            filled.append(
                {
                    "area_name": area_name,
                    "hotel_count": 0,
                    "lower_price_hotel_count": 0,
                    "slightly_higher_hotel_count": 0,
                    "lower_price_ratio": 0,
                    "average_holiday_nightly_tax_total_value": 0,
                    "average_holiday_nightly_tax_total_price": self._format_cny(0),
                    "average_price_diff_nightly": 0,
                    "average_price_diff_nightly_text": self._format_cny_diff(0),
                    "room_type_labels": [],
                    "representative_hotels": [],
                    "reason": "当前命中酒店不足 3 个具体片区，补充展示该城市常见旅游区域。",
                }
            )
            existing.add(area_name)
            if len(filled) >= 3:
                break
        return filled

    def _area_recommendation_reason(self, hotel_count: int, lower_count: int, avg_diff: int) -> str:
        if lower_count == hotel_count:
            return f"{hotel_count} 家命中酒店假期价格不高于平日代表均价。"
        if lower_count:
            return f"{lower_count} 家酒店假期更低，整体涨幅可控。"
        return f"{hotel_count} 家酒店假期涨幅不超过 100 元/晚。"

    def _format_api_price_text(self, value: Any) -> str:
        price = self._coerce_api_price(value)
        return f"CNY {price:,}" if price is not None else ""

    def _extract_api_total_price_text(self, price_explanation: Any, nightly_tax_price: Any, nights: int) -> str:
        text = str(price_explanation or "")
        match = re.search(r"Total price:\s*CNY\s*[\d,]+", text)
        if match:
            return match.group(0)
        nightly_value = self._coerce_api_price(nightly_tax_price)
        if nightly_value is None:
            return ""
        return f"Total price: CNY {nightly_value * max(1, nights):,}"

    def _coerce_api_price(self, value: Any) -> int | None:
        if value in ("", None):
            return None
        if isinstance(value, (int, float)):
            return round(float(value))
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        return round(float(match.group(0))) if match else None

    def _is_placeholder_hotel_name(self, value: str) -> bool:
        return value.strip().lower() in {"hi china", "hi china!"}

    def _extract_hotel_list_snapshot(self, page, limit: int) -> list[dict[str, Any]]:
        extracted = page.evaluate(
            """(limit) => {
                const cards = Array.from(document.querySelectorAll('.hotel-card')).slice(0, limit);
                return cards.map((card) => {
                    const detailNode = card.querySelector(
                        'a.hotelName, .hotel-title a, .list-card-title a, a[href*="/hotels/detail/"]'
                    );
                    const roomPriceNode =
                        card.querySelector('.room-price .sale') ||
                        card.querySelector('.room-price .price-line span:last-child');
                    const totalNode = card.querySelector('.room-price .price-explain');
                    return {
                        hotel_id: card.getAttribute('id') || '',
                        hotel_name: (detailNode?.textContent || '').trim(),
                        detail_href: detailNode?.getAttribute('href') || '',
                        room_name: (card.querySelector('.room-name')?.textContent || '').trim(),
                        room_price_text: (roomPriceNode?.textContent || '').trim(),
                        tax_total_text: (totalNode?.innerText || totalNode?.textContent || '').trim(),
                        raw_text: (card.innerText || '').trim(),
                    };
                });
            }""",
            limit,
        )

        items = self._normalize_hotel_cards(extracted)
        if items:
            return items

        fallback = page.evaluate(
            """(limit) => {
                const anchors = Array.from(document.querySelectorAll('a[href*="/hotels/detail/"]'));
                const seen = new Set();
                const rows = [];
                for (const anchor of anchors) {
                    const href = anchor.getAttribute('href') || '';
                    if (!href || seen.has(href)) {
                        continue;
                    }
                    seen.add(href);
                    let node = anchor.parentElement;
                    let container = null;
                    while (node && node !== document.body) {
                        const text = (node.innerText || '').trim();
                        const cnyCount = (text.match(/CNY/g) || []).length;
                        if (text.includes('Total price') || cnyCount >= 2) {
                            container = node;
                            break;
                        }
                        node = node.parentElement;
                    }
                    rows.push({
                        hotel_id: '',
                        hotel_name: (anchor.textContent || '').trim(),
                        detail_href: href,
                        room_name: '',
                        room_price_text: '',
                        tax_total_text: '',
                        raw_text: (container?.innerText || anchor.innerText || '').trim(),
                    });
                    if (rows.length >= limit) {
                        break;
                    }
                }
                return rows;
            }""",
            limit,
        )
        return self._normalize_hotel_cards(fallback)

    def _advance_hotel_list_scroll(self, page, wait_ms: int = SCROLL_WAIT_MS) -> None:
        page.evaluate(
            """() => {
                const scrollers = Array.from(document.querySelectorAll('div, main, section'))
                    .filter((node) => node.scrollHeight > node.clientHeight + 300)
                    .sort((a, b) => b.scrollHeight - a.scrollHeight);
                const target = scrollers[0];
                if (target) {
                    target.scrollTop = target.scrollHeight;
                }
                window.scrollTo(0, document.body.scrollHeight);
            }"""
        )
        page.mouse.wheel(0, 7000)
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)

    def _finalize_hotel_items(
        self,
        items: list[dict[str, Any]],
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters,
    ) -> list[dict[str, Any]]:
        finalized: list[dict[str, Any]] = []
        for item in items:
            detail_url = urljoin("https://www.trip.com", item["detail_href"])
            if not self._to_zh_detail_url(detail_url):
                detail_url = self._build_detail_url_from_ids(
                    city_id=city_candidate.city_id,
                    hotel_id=item["hotel_id"],
                    check_in=check_in,
                    check_out=check_out,
                )
            item["detail_url"] = detail_url
            if self._is_outside_search_city(item, city_candidate, detail_url):
                continue
            item["room_price_value"] = self._extract_price_value(item["room_price_text"])
            item["tax_total_value"] = self._extract_price_value(item["tax_total_text"])
            if not self._apply_feature_filter_context(item, feature_filters):
                continue
            finalized.append(item)
        return finalized

    def _apply_feature_filter_context(self, item: dict[str, Any], feature_filters: FeatureFilters) -> bool:
        if feature_filters.advanced == "yes":
            if item.get("is_advanced") is False:
                return False
        elif feature_filters.advanced == "no" and item.get("is_advanced") is True:
            return False

        if feature_filters.pool == "yes":
            if item.get("has_pool") is False:
                return False
        elif feature_filters.pool == "no" and item.get("has_pool") is True:
            return False

        if feature_filters.child_facility == "yes":
            if item.get("has_child_facility") is False:
                return False
        elif feature_filters.child_facility == "no" and item.get("has_child_facility") is True:
            return False

        return True

    def _excluded_by_negative_feature_filter(self, item: dict[str, Any], feature_filters: FeatureFilters) -> bool:
        if feature_filters.pool == "no" and item.get("has_pool") is True:
            return True
        if feature_filters.child_facility == "no" and item.get("has_child_facility") is True:
            return True
        if feature_filters.advanced == "no" and item.get("is_advanced") is True:
            return True
        return False

    def _normalize_hotel_cards(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for card in cards:
            raw_text = str(card.get("raw_text") or "").strip()
            hotel_name = str(card.get("hotel_name") or "").strip()
            room_name = str(card.get("room_name") or "").strip()
            room_price_text = str(card.get("room_price_text") or "").strip()
            tax_total_text = str(card.get("tax_total_text") or "").strip()
            detail_href = str(card.get("detail_href") or "").strip()

            if not hotel_name and raw_text:
                hotel_name = raw_text.splitlines()[0].strip()

            cny_matches = re.findall(r"CNY\s*[\d,]+", raw_text)
            if not room_price_text and cny_matches:
                room_price_text = cny_matches[0]

            if tax_total_text:
                tax_total_text = tax_total_text.splitlines()[0].strip()
            else:
                total_match = re.search(r"Total price:\s*CNY\s*[\d,]+", raw_text)
                if total_match:
                    tax_total_text = total_match.group(0)
                elif len(cny_matches) >= 2:
                    tax_total_text = f"Total price: {cny_matches[-1]}"

            if not room_name and raw_text:
                lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
                price_lines = set(cny_matches)
                for line in lines:
                    if line == hotel_name or line in price_lines or line.startswith("Total price:"):
                        continue
                    if any(keyword in line for keyword in ("bed", "Bed", "Twin", "King", "Queen", "Room", "房")):
                        room_name = line
                        break

            hotel_id = str(card.get("hotel_id") or "").strip() or self._extract_hotel_id(detail_href)
            if not hotel_id or not hotel_name or not tax_total_text:
                continue

            items.append(
                {
                    "hotel_id": hotel_id,
                    "hotel_name": hotel_name,
                    "detail_href": detail_href,
                    "room_name": room_name,
                    "room_price_text": room_price_text,
                    "tax_total_text": tax_total_text,
                    "has_pool": self._text_has_pool_feature(raw_text.lower()),
                    "has_child_facility": self._text_has_child_feature(raw_text.lower()),
                    "is_advanced": None,
                }
            )
        return items

    def _extract_hotel_id(self, detail_href: str) -> str:
        if not detail_href:
            return ""
        query = parse_qs(urlparse(detail_href).query)
        hotel_ids = query.get("hotelId") or []
        return hotel_ids[0] if hotel_ids else ""

    def _enrich_choices_with_chinese_hotel_names(self, choices: list[dict[str, Any]]) -> None:
        missing: dict[str, str] = {}
        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if not hotel_id:
                continue
            with self._cache_lock:
                cached = self._hotel_name_cache.get(hotel_id)
            if cached is None and hotel_id not in missing:
                missing[hotel_id] = item.get("detail_url") or ""

        if missing:
            cache_changed = False
            with ThreadPoolExecutor(max_workers=CHINESE_NAME_WORKERS) as executor:
                future_map = {
                    executor.submit(self._fetch_trip_hk_chinese_hotel_name, detail_url): hotel_id
                    for hotel_id, detail_url in missing.items()
                }
                for future in as_completed(future_map):
                    hotel_id = future_map[future]
                    try:
                        value = future.result()
                    except Exception:
                        value = {}
                    with self._cache_lock:
                        self._hotel_name_cache[hotel_id] = value
                    cache_changed = True
            if cache_changed:
                self._save_hotel_name_cache()

        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if not hotel_id:
                continue
            with self._cache_lock:
                cached = self._hotel_name_cache.get(hotel_id) or {}
            name = cached.get("hotel_name") or ""
            if name:
                item["hotel_name"] = name
                item["hotel_name_source"] = cached.get("source") or "Trip.com HK"

    def _filter_choices_by_verified_features(
        self,
        choices: list[dict[str, Any]],
        feature_filters: FeatureFilters,
    ) -> list[dict[str, Any]]:
        required = self._required_feature_keys(feature_filters)
        if not required:
            return choices

        missing: dict[str, str] = {}
        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if not hotel_id:
                continue
            cached = self._cached_hotel_feature_flags(hotel_id)
            if cached:
                self._apply_feature_flags(item, cached)
            if any(item.get(key) is not True for key in required):
                missing[hotel_id] = item.get("detail_url") or ""

        if missing:
            cache_changed = False
            with ThreadPoolExecutor(max_workers=FEATURE_VERIFY_WORKERS) as executor:
                future_map = {
                    executor.submit(self._fetch_hotel_detail_feature_flags, detail_url): hotel_id
                    for hotel_id, detail_url in missing.items()
                    if detail_url
                }
                for future in as_completed(future_map):
                    hotel_id = future_map[future]
                    try:
                        flags = future.result()
                    except Exception:
                        flags = {}
                    if not flags:
                        continue
                    with self._cache_lock:
                        self._hotel_feature_cache[hotel_id] = flags
                    cache_changed = True
            if cache_changed:
                self._save_hotel_feature_cache()

        filtered: list[dict[str, Any]] = []
        for item in choices:
            hotel_id = str(item.get("hotel_id") or "")
            if hotel_id:
                cached = self._cached_hotel_feature_flags(hotel_id)
                if cached:
                    self._apply_feature_flags(item, cached)
            if all(item.get(key) is True for key in required):
                filtered.append(item)
        return filtered

    def _required_feature_keys(self, feature_filters: FeatureFilters) -> tuple[str, ...]:
        keys: list[str] = []
        if feature_filters.advanced == "yes":
            keys.append("is_advanced")
        if feature_filters.pool == "yes":
            keys.append("has_pool")
        if feature_filters.child_facility == "yes":
            keys.append("has_child_facility")
        return tuple(keys)

    def _cached_hotel_feature_flags(self, hotel_id: str) -> dict[str, bool]:
        with self._cache_lock:
            cached = copy.deepcopy(self._hotel_feature_cache.get(hotel_id) or {})
        return {
            key: value
            for key, value in cached.items()
            if key in {"is_advanced", "has_pool", "has_child_facility"} and isinstance(value, bool)
        }

    def _apply_feature_flags(self, item: dict[str, Any], flags: dict[str, bool]) -> None:
        for key in ("is_advanced", "has_pool", "has_child_facility"):
            if key in flags:
                item[key] = flags[key]

    def _fetch_hotel_detail_feature_flags(self, detail_url: str) -> dict[str, bool]:
        verify_url = self._to_feature_verify_detail_url(detail_url)
        if not verify_url:
            return {}
        req = Request(
            verify_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.5",
                "user-agent": UA,
            },
        )
        try:
            with urlopen(req, timeout=18) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError, OSError):
            return {}
        return self._extract_detail_feature_flags(body)

    def _to_feature_verify_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        parsed = urlparse(detail_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        hotel_ids = query.get("hotelId") or []
        if not hotel_ids:
            return ""
        city_ids = query.get("cityId") or query.get("city") or []
        params = {
            "hotelId": hotel_ids[0],
            "adult": 2,
            "children": 0,
            "crn": 1,
            "curr": "CNY",
            "locale": "zh-CN",
        }
        if city_ids:
            params["cityId"] = city_ids[0]
        for key in ("checkIn", "checkOut"):
            values = query.get(key)
            if values:
                params[key] = values[0]
        return "https://www.trip.com/hotels/detail/?" + urlencode(params)

    def _extract_detail_feature_flags(self, body: str) -> dict[str, bool]:
        text = self._detail_feature_text(body)
        flags: dict[str, bool] = {}

        pool = self._detail_has_pool_feature(text)
        if pool is not None:
            flags["has_pool"] = pool
        child = self._detail_has_child_feature(text)
        if child is not None:
            flags["has_child_facility"] = child
        advanced = self._detail_is_advanced_feature(text)
        if advanced is not None:
            flags["is_advanced"] = advanced
        return flags

    def _detail_feature_text(self, body: str) -> str:
        decoded = html.unescape(body or "")
        decoded = re.sub(r"https?://\S+", " ", decoded)
        decoded = re.sub(r"detailFilters=[^\"'&\s]+", " ", decoded, flags=re.IGNORECASE)
        decoded = re.sub(r"hoteluniquekey=[^\"'&\s]+", " ", decoded, flags=re.IGNORECASE)
        return decoded.lower()

    def _detail_has_pool_feature(self, text: str) -> bool | None:
        if re.search(r"no\s+pool|without\s+pool|无泳池|無泳池|没有泳池|沒有泳池", text, flags=re.IGNORECASE):
            return False
        if re.search(
            r"游泳池|泳池|室内泳池|室外泳池|swimming\s+pool|indoor\s+pool|outdoor\s+pool|heated\s+pool",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        return None

    def _detail_has_child_feature(self, text: str) -> bool | None:
        if re.search(
            r"儿童乐园|兒童樂園|儿童泳池|兒童泳池|亲子|親子|children'?s\s+playground|kids?\s+club|children'?s\s+pool|family-friendly",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        return None

    def _detail_is_advanced_feature(self, text: str) -> bool | None:
        if re.search(r"5-star|4-star|5\s+star|4\s+star|五星|四星|五钻|四钻|五鑽|四鑽|豪华型|豪華型|高档型|高檔型", text):
            return True
        if re.search(r"3-star|2-star|3\s+star|2\s+star|三星|二星|三钻|二钻|三鑽|二鑽|经济型|經濟型", text):
            return False
        return None

    def _fetch_trip_hk_chinese_hotel_name(self, detail_url: str) -> dict[str, str]:
        hk_url = self._to_trip_hk_detail_url(detail_url)
        if not hk_url:
            return {}
        req = Request(
            hk_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "zh-HK,zh;q=0.9,en;q=0.5",
                "user-agent": UA,
            },
        )
        try:
            with urlopen(req, timeout=18) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError, OSError):
            return {}
        name = self._extract_trip_hk_chinese_hotel_name(body)
        if not name:
            return {}
        return {"hotel_name": name, "source": "Trip.com HK"}

    def _to_trip_hk_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        parsed = urlparse(detail_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        hotel_ids = query.get("hotelId") or []
        if not hotel_ids:
            return ""
        query["locale"] = ["zh_hk"]
        query["curr"] = ["CNY"]
        if "crn" not in query:
            query["crn"] = ["1"]
        path = parsed.path if "/hotels/detail/" in parsed.path else "/hotels/detail/"
        return parsed._replace(scheme="https", netloc="hk.trip.com", path=path, query=urlencode(query, doseq=True)).geturl()

    def _build_detail_url_from_ids(
        self,
        city_id: int,
        hotel_id: str,
        check_in: dt.date,
        check_out: dt.date,
    ) -> str:
        params = {
            "cityId": city_id,
            "hotelId": hotel_id,
            "checkIn": check_in.isoformat(),
            "checkOut": check_out.isoformat(),
            "adult": 2,
            "children": 0,
            "crn": 1,
            "curr": "CNY",
            "locale": "zh-CN",
        }
        return "https://www.trip.com/hotels/detail/?" + urlencode(params)

    def _extract_trip_hk_chinese_hotel_name(self, body: str) -> str:
        decoded = html.unescape(body)
        patterns = [
            (r'\\"nameLocale\\":\\"([^"\\]+)\\"', "json nameLocale"),
            (r'"nameLocale":"([^"]+)"', "json nameLocale"),
            (r'\\"keywords\\":\\"([^"\\]+)\\"', "seo keywords"),
            (r'"keywords":"([^"]+)"', "seo keywords"),
            (r'\\"title\\":\\"([^"\\]+?)\\s+-\\s+\\d{4}\\s+', "seo title"),
            (r'"title":"([^"]+?)\\s+-\\s+\\d{4}\\s+', "seo title"),
            (r'class="crumbSEO_crumb_content__[^"]*">([^<]+)</span>', "breadcrumb"),
            (r'\\"name\\":\\"([^"\\(]+)(?:\\([^"\\)]*\\))?\\"', "structured data"),
        ]
        for pattern, _source in patterns:
            match = re.search(pattern, decoded)
            if not match:
                continue
            name = self._clean_chinese_hotel_name(match.group(1))
            if self._is_reliable_chinese_hotel_name(name):
                return name
        return ""

    def _clean_chinese_hotel_name(self, value: str) -> str:
        value = html.unescape(value or "")
        if "\\u" in value:
            value = value.encode("utf-8", errors="ignore").decode("unicode_escape", errors="ignore")
        value = re.sub(r"\\/", "/", value)
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(r"\s*[-｜|]\s*20\d{2}.*$", "", value).strip()
        value = re.sub(r"\([^)]*[A-Za-z][^)]*\)$", "", value).strip()
        return value

    def _is_reliable_chinese_hotel_name(self, value: str) -> bool:
        if not value or len(value) > 80:
            return False
        if any(token in value for token in ("Trip.com", "訂房", "優惠", "住客評論", "酒店推薦")):
            return False
        return bool(re.search(r"[\u3400-\u9fff]", value))

    def _to_zh_detail_url(self, detail_url: str) -> str:
        if not detail_url:
            return ""
        parsed = urlparse(detail_url)
        if not parsed.path or "/hotels/detail/" not in parsed.path:
            return ""
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["locale"] = ["zh-CN"]
        return parsed._replace(query=urlencode(query, doseq=True)).geturl()

    def _nightly_value(self, total_value: int, nights: int) -> int:
        if nights <= 0:
            return total_value
        return round(total_value / nights)

    def _classify_room_type(self, room_name: str) -> str:
        text = (room_name or "").strip().lower()
        if not text:
            return "unknown"
        twin_patterns = [
            r"\btwin\b",
            r"\b2\s*beds?\b",
            r"\btwo\s*beds?\b",
            r"双床",
            r"两张床",
            r"2张床",
        ]
        king_patterns = [
            r"\bking\b",
            r"\bqueen\b",
            r"\bdouble\s*bed\b",
            r"\bdouble\s*room\b",
            r"\bdouble\b",
            r"大床",
            r"一张床",
            r"1张床",
        ]
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in twin_patterns):
            return "twin"
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in king_patterns):
            return "king"
        return "unknown"

    def _room_type_label(self, room_type: str) -> str:
        if room_type == "king":
            return "大床房"
        if room_type == "twin":
            return "双床房"
        return "未识别房型"

    def _localize_room_name(self, room_name: str) -> str:
        text = (room_name or "").strip()
        if not text:
            return text
        replacements = [
            (r"\bDeluxe\b", "豪华"),
            (r"\bPremier\b", "尊贵"),
            (r"\bSuperior\b", "高级"),
            (r"\bExecutive\b", "行政"),
            (r"\bClassic\b", "经典"),
            (r"\bStandard\b", "标准"),
            (r"\bGuestroom\b", "客房"),
            (r"\bRoom\b", "房"),
            (r"\bSuite\b", "套房"),
            (r"\bKing\b", "大床"),
            (r"\bQueen\b", "大床"),
            (r"\bTwin\b", "双床"),
            (r"\bDouble bed\b", "大床"),
            (r"\bDouble\b", "双人"),
            (r"\bBed\b", "床"),
            (r"\bBund\b", "外滩"),
            (r"\bRiver Wing\b", "江景楼"),
            (r"\bCity View\b", "城景"),
            (r"\bRiver View\b", "江景"),
            (r"\bNo window\b", "无窗"),
            (r"\bSpecial promotion\b", "特惠"),
        ]
        localized = text
        for pattern, replacement in replacements:
            localized = re.sub(pattern, replacement, localized, flags=re.IGNORECASE)
        localized = re.sub(r"\s+", " ", localized).strip(" -(),")
        return localized

    def _build_list_url(
        self,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters | None = None,
    ) -> str:
        feature_filters = feature_filters or FeatureFilters()
        params = {
            "city": city_candidate.city_id,
            "cityName": city_candidate.city_name,
            "provinceId": city_candidate.province_id,
            "countryId": city_candidate.country_id,
            "districtId": 0,
            "checkin": check_in.strftime("%Y/%m/%d"),
            "checkout": check_out.strftime("%Y/%m/%d"),
            "lat": city_candidate.lat,
            "lon": city_candidate.lon,
            "searchType": "CT",
            "searchWord": city_candidate.city_name,
            "searchValue": f"{city_candidate.filter_id}*19*{city_candidate.city_id}*1".replace("|", "~"),
            "searchCoordinate": city_candidate.search_coordinate.replace("|", "~"),
            "crn": 1,
            "adult": 2,
            "children": 0,
            "searchBoxArg": "t",
            "travelPurpose": 0,
            "ctm_ref": "ix_sb_dl",
            "domestic": "true",
            "listFilters": self._build_list_filters(feature_filters),
            "locale": "zh-CN",
            "curr": "CNY",
        }
        return "https://www.trip.com/hotels/list?" + urlencode(params)

    def _build_hotel_list_url(
        self,
        city_candidate: CityCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters | None = None,
        keyword_candidate: HotelKeywordCandidate | None = None,
    ) -> str:
        if keyword_candidate is None:
            return self._build_list_url(city_candidate, check_in, check_out, feature_filters)
        return self._build_keyword_list_url(city_candidate, keyword_candidate, check_in, check_out, feature_filters)

    def _build_keyword_list_url(
        self,
        city_candidate: CityCandidate,
        keyword_candidate: HotelKeywordCandidate,
        check_in: dt.date,
        check_out: dt.date,
        feature_filters: FeatureFilters | None = None,
    ) -> str:
        feature_filters = feature_filters or FeatureFilters()
        filter_type = keyword_candidate.filter_id.split("|", 1)[0] or "31"
        params = {
            "city": city_candidate.city_id,
            "cityName": city_candidate.city_name,
            "provinceId": city_candidate.province_id,
            "countryId": city_candidate.country_id,
            "districtId": 0,
            "checkin": check_in.strftime("%Y/%m/%d"),
            "checkout": check_out.strftime("%Y/%m/%d"),
            "lat": keyword_candidate.lat,
            "lon": keyword_candidate.lon,
            "searchType": "H",
            "searchWord": keyword_candidate.title,
            "searchValue": (
                f"{keyword_candidate.filter_id}*{filter_type}*{keyword_candidate.hotel_id}*1".replace("|", "~")
            ),
            "searchCoordinate": keyword_candidate.search_coordinate.replace("|", "~"),
            "crn": 1,
            "adult": 2,
            "children": 0,
            "searchBoxArg": "t",
            "travelPurpose": 0,
            "ctm_ref": "ix_sb_dl",
            "domestic": "true",
            "listFilters": self._build_list_filters(feature_filters),
            "locale": "zh-CN",
            "curr": "CNY",
        }
        return "https://www.trip.com/hotels/list?" + urlencode(params)

    def _build_list_filters(self, feature_filters: FeatureFilters) -> str:
        filters = [item for item in DEFAULT_LIST_FILTERS.split(",") if item]
        if feature_filters.advanced == "yes":
            filters.extend(ADVANCED_YES_FILTERS)
        elif feature_filters.advanced == "no":
            filters.extend(ADVANCED_NO_FILTERS)

        if feature_filters.pool == "yes":
            filters.extend(POOL_YES_FILTERS)
        if feature_filters.child_facility == "yes":
            filters.extend(CHILD_FACILITY_YES_FILTERS)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in filters:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return ",".join(deduped)

    def _extract_price_value(self, text: str) -> int:
        match = re.search(r"CNY\s*([\d,]+)", text)
        if not match:
            match = re.search(r"(\d[\d,]*)", text.replace(".00", ""))
        if not match:
            return 0
        return int(match.group(1).replace(",", ""))

    def _format_cny(self, value: int) -> str:
        return f"CNY {value}"

    def _format_cny_diff(self, value: int) -> str:
        sign = "+" if value > 0 else ""
        return f"{sign}CNY {value}"

    def _trace_id(self) -> str:
        prefix = str(random.randint(1_000_000_000, 1_999_999_999))
        millis = int(dt.datetime.now().timestamp() * 1000)
        suffix = random.randint(1_000_000_000, 1_999_999_999)
        return f"{prefix}-{millis}-{suffix}"
