#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from reverse_travel import T2S_CHAR_MAP, T2S_PHRASE_REPLACEMENTS
except Exception:  # pragma: no cover - this script should still work outside the app runtime.
    T2S_PHRASE_REPLACEMENTS: dict[str, str] = {}
    T2S_CHAR_MAP = str.maketrans({})


DEFAULT_SOURCE = ROOT / "exports/xhs_shenzhen_duanwu_2026/source_data.json"
DEFAULT_CACHE_DIR = ROOT / ".cache/search"
DEFAULT_CITY_INTRO_CACHE = ROOT / "data/city_wikipedia_intro_cache.json"

LIST_KEYS = ("star_no_rise", "family_no_rise", "discount_star")
GENERIC_AREA_WORDS = ("待确认", "热门酒店片区", "片区待补充", "未识别", "其他片区")
CITY_EXCLUSION_PREFIXES = {
    "深圳": ("广州", "东莞", "惠州", "中山", "佛山", "珠海", "香港", "澳门"),
    "广州": ("深圳", "东莞", "惠州", "中山", "佛山", "珠海", "香港", "澳门"),
    "佛山": ("广州", "深圳", "东莞", "惠州", "中山", "珠海", "香港", "澳门"),
}
AREA_ALIASES = {
    "深圳": (
        ("前海华侨城片区", ("前海华侨城", "前海", "jen", "qianhai")),
        ("国际会展中心片区", ("国际会展中心", "会展中心", "海上田园", "国展", "fuhai")),
        ("宝安西乡/固戍片区", ("宝安坪洲", "坪洲", "固戍", "西乡", "恒丰海悦", "舜和国际", "泰华梧桐")),
        ("机场片区", ("机场凯悦", "机场店", "宝利来", "凯悦嘉寓", "hyatt regency shenzhen airport", "hyatt house shenzhen airport")),
        ("光明虹桥片区", ("光明", "虹桥")),
        ("深圳北站/龙华片区", ("深圳北站", "深圳北", "龙华", "雅高铂尔曼", "pullman shenzhen north")),
        ("福田中心片区", ("福田", "中心区", "星河丽思", "好日子", "福朋喜来登", "大中华希尔顿", "朗廷", "futian")),
        ("华强北片区", ("华强北", "hongli road", "huaqiangbei", "回酒店")),
        ("罗湖口岸片区", ("罗湖", "东门", "香格里拉", "君悦", "grand hyatt", "luohu")),
        ("深圳湾片区", ("深圳湾", "shenzhen bay", "南油")),
        ("蛇口海上世界片区", ("蛇口", "海上世界", "shekou")),
        ("南山片区", ("南山", "威斯汀", "博林天瑞", "南山假日", "深铁皇冠", "nanshan")),
        ("观澜片区", ("观澜", "硬石", "mission hills", "hard rock")),
        ("龙岗大运片区", ("龙岗", "大运", "隐秀山居", "castle hotel")),
        ("盐田大梅沙片区", ("盐田", "大梅沙", "小梅沙")),
        ("大鹏海边片区", ("大鹏", "南澳", "麓湾", "晟曼湾", "海边")),
    ),
    "广州": (
        ("广州白云片区", ("白云", "白雲", "白云国际会议中心", "白云国际机场", "人和", "机场")),
        ("广州天河/珠江新城片区", ("天河", "珠江新城", "太古汇", "太古滙", "五羊邨", "正佳", "金融城", "海航威斯汀", "卓美亚", "w酒店", "万菱汇", "萬菱匯")),
        ("广州南沙片区", ("南沙", "天后宫", "客运港")),
        ("广州番禺长隆片区", ("番禺", "长隆", "長隆")),
        ("广州花都片区", ("花都",)),
        ("广州越秀/北京路片区", ("越秀", "北京路", "中华广场", "中華廣場", "农讲所", "農講所", "大佛古寺", "广东亚洲国际")),
        ("广州荔湾/永庆坊片区", ("荔湾", "荔灣", "上下九", "永庆坊", "永慶坊", "十甫")),
        ("广州琶洲会展片区", ("琶洲", "广州塔", "廣州塔")),
        ("广州增城新塘片区", ("增城", "新塘")),
        ("广州从化温泉片区", ("从化", "從化", "温泉", "森悦谷", "森悅谷")),
        ("广州黄埔片区", ("黄埔", "黃埔")),
    ),
    "佛山": (
        ("佛山千灯湖/桂城片区", ("千灯湖", "千燈湖", "桂城", "保利洲际", "保利洲際", "佛山万豪", "佛山萬豪", "南海利泰")),
        ("佛山南海片区", ("南海", "和华希尔顿", "和華希爾頓", "丹灶", "里水", "狮山", "獅山")),
        ("佛山禅城祖庙片区", ("禅城", "禪城", "祖庙", "祖廟", "创意产业园", "創意產業園", "南风古灶", "南風古灶", "皇冠假日")),
        ("佛山顺德北滘片区", ("北滘", "碧桂园", "碧桂園", "雅高铂尔曼", "雅高鉑爾曼", "pullman foshan shunde", "country garden")),
        ("佛山顺德乐从片区", ("乐从", "樂從", "罗浮宫", "羅浮宮")),
        ("佛山顺德龙江片区", ("龙江", "龍江", "联塑", "聯塑")),
        ("佛山西樵山片区", ("西樵", "千古情")),
        ("佛山三水片区", ("三水",)),
        ("佛山沙寮江景片区", ("沙寮", "hajana")),
    ),
}
GENERIC_HOTEL_NAMES = {
    "洲际酒店",
    "万豪酒店",
    "喜来登酒店",
    "希尔顿酒店",
    "希尔顿欢朋酒店",
    "希尔顿花园酒店",
    "皇冠假日酒店",
    "假日酒店",
    "维也纳酒店",
}
SOURCE_AREA_GROUPS = {
    "深圳": {
        "南山片区": {"南山"},
        "国际会展中心片区": {"国际会展中心", "深圳国际会展中心"},
        "宝安西乡/固戍片区": {"宝安坪洲", "宝安固戍", "宝安"},
        "前海华侨城片区": {"前海华侨城"},
        "深圳北站/龙华片区": {"深圳北站", "龙华"},
        "福田中心片区": {"福田/中心区", "福田中心"},
        "机场片区": {"机场"},
        "大鹏海边片区": {"大鹏"},
        "罗湖口岸片区": {"罗湖"},
        "光明虹桥片区": {"光明虹桥"},
    }
}
CITY_INTRO_TEXTS = {
    "深圳": "深圳可重点看世界之窗、锦绣中华民俗村、欢乐谷、东部华侨城、大鹏所城和中英街；滨海度假、主题乐园和历史街区适合按片区组合。",
    "广州": "广州主要看长隆、白云山、广州塔、北京路、中山纪念堂、南越王博物院、越秀公园和陈家祠；老城文化、珠江夜景和亲子度假可分片区安排。",
    "佛山": "佛山可重点看清晖园、西樵山、佛山祖庙、南风古灶和岭南天地；顺德园林美食、禅城老城和西樵山周边适合短途组合。",
    "东莞": "东莞可重点看可园、松山湖、鸦片战争博物馆、虎门炮台和观音山；历史文化、城市湖景和亲子度假适合按片区组合。",
    "惠州": "惠州可重点看惠州西湖、罗浮山、巽寮湾、双月湾和南昆山；滨海度假、山水温泉和古城漫游适合分片区安排。",
    "中山": "中山可重点看孙中山故里、岐江公园、中山詹园、孙文西路和紫马岭公园；人文街区和亲子短途可按城区组合。",
    "珠海": "珠海可重点看长隆海洋王国、情侣路、圆明新园、东澳岛和外伶仃岛；海岛度假、亲子乐园和城市海岸适合连线安排。",
    "江门": "江门可重点看开平碉楼与村落、赤坎华侨古镇、上下川岛、小鸟天堂和古劳水乡；侨乡文化与滨海度假适合组合。",
    "汕尾": "汕尾可重点看红海湾、金町湾、凤山祖庙、莲花山和玄武山；海滨度假、古城人文和山海景观适合周边短途。",
    "河源": "河源可重点看万绿湖、桂山、镜花缘、霍山和河源恐龙博物馆；湖景度假、山地自然和亲子行程适合搭配。",
    "肇庆": "肇庆可重点看七星岩、鼎湖山、宋城墙、阅江楼和砚洲岛；山水景区、老城文化和亲子休闲适合短途组合。",
    "云浮": "云浮可重点看国恩寺、蟠龙洞、天露山和六祖故里旅游度假区；禅文化、山地自然和温泉休闲适合周边短途。",
    "汕头": "汕头可重点看小公园、南澳岛、礐石风景区、陈慈黉故居和老妈宫；海岛度假、骑楼街区和潮汕美食适合组合。",
    "清远": "清远可重点看古龙峡、连州地下河、黄腾峡、英西峰林和湟川三峡；峡谷漂流、喀斯特山水和亲子度假适合短途。",
    "韶关": "韶关可重点看丹霞山、南华寺、乳源大峡谷、珠玑古巷和梅关古道；自然地貌、禅宗文化和古道历史适合组合。",
    "潮州": "潮州可重点看广济桥、牌坊街、开元寺、韩文公祠和潮州西湖；古城漫游、非遗美食和韩江夜景适合慢游。",
    "揭阳": "揭阳可重点看进贤门、揭阳学宫、黄岐山、望天湖和惠来海滨；古城人文、城市山景和滨海休闲适合短途组合。",
    "梅州": "梅州可重点看客天下、雁南飞、叶剑英纪念园、松口古镇和五指石；客家文化、茶田山景和古镇线路适合组合。",
    "阳江": "阳江可重点看海陵岛、闸坡、大角湾、十里银滩和凌霄岩；滨海度假、海鲜美食和喀斯特洞景适合短途安排。",
    "茂名": "茂名可重点看中国第一滩、浪漫海岸、放鸡岛、御水古温泉和信宜天马山；滨海度假、温泉和山地休闲适合组合。",
    "湛江": "湛江可重点看湖光岩、赤坎老街、金沙湾、硇洲岛和特呈岛；海湾城市、火山湖景和海岛度假适合分片区安排。",
}
GENERIC_CITY_INTRO_PATTERNS = (
    "可结合历史街区、城市公园、博物馆和周边自然景区安排短途行程",
    "适合结合城市景点与周边片区安排短途旅行",
    "适合结合城市景点、周边片区和高级酒店分布安排短途旅行",
)
EXTRA_T2S_CHAR_MAP = str.maketrans(
    {
        "僑": "侨",
        "閱": "阅",
        "機": "机",
        "場": "场",
        "鵬": "鹏",
        "來": "来",
        "隱": "隐",
        "澔": "澔",
        "閲": "阅",
        "鳳": "凤",
        "聯": "联",
        "順": "顺",
        "從": "从",
        "運": "运",
        "宮": "宫",
        "楓": "枫",
        "億": "亿",
        "學": "学",
        "動": "动",
        "賓": "宾",
        "設": "设",
        "計": "计",
        "蓮": "莲",
        "溫": "温",
        "寶": "宝",
        "衛": "卫",
        "禮": "礼",
        "優": "优",
        "麗": "丽",
        "悅": "悦",
        "騰": "腾",
        "鐘": "钟",
        "樓": "楼",
        "臺": "台",
        "裏": "里",
        "灣": "湾",
        "態": "态",
        "勵": "励",
        "駿": "骏",
        "鵝": "鹅",
        "閑": "闲",
        "燈": "灯",
        "歡": "欢",
        "傢": "家",
        "漁": "渔",
        "覽": "览",
        "興": "兴",
        "嶺": "岭",
        "廟": "庙",
        "視": "视",
        "遠": "远",
        "關": "关",
        "陽": "阳",
        "慶": "庆",
        "雲": "云",
        "頭": "头",
        "縣": "县",
        "區": "区",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an Xiaohongshu area-region rank card.")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE))
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--from-cache", action="store_true", help="Build the ranking from local search cache.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--city-intro-cache", default=str(DEFAULT_CITY_INTRO_CACHE))
    parser.add_argument("--holiday-contains", default="2026-06-19")
    parser.add_argument("--advanced-only", action="store_true", help="Only use cache files whose advanced filter is yes.")
    parser.add_argument("--max-rise", type=int, default=0, help="Maximum allowed nightly rise. 0 means no rise.")
    return parser.parse_args()


def to_simplified(value: str | None) -> str:
    text = str(value or "")
    if not text:
        return ""
    for traditional, simplified in T2S_PHRASE_REPLACEMENTS.items():
        text = text.replace(traditional, simplified)
    return text.translate(T2S_CHAR_MAP).translate(EXTRA_T2S_CHAR_MAP)


def contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value or ""))


def chinese_count(value: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", value or ""))


def clean_hotel_name(item: dict[str, Any]) -> str:
    candidates = [
        item.get("hotel_name_simplified"),
        item.get("hotel_name"),
        item.get("name"),
        item.get("hotel_original_name"),
        item.get("hotelName"),
    ]
    for candidate in candidates:
        name = to_simplified(str(candidate or "")).strip()
        if chinese_count(name) >= 4 and name not in {"深圳", "深圳市"} and name not in GENERIC_HOTEL_NAMES:
            return name
    for candidate in candidates:
        name = to_simplified(str(candidate or "")).strip()
        if name:
            return name
    return ""


def clean_hotel_name_from_records(*items: dict[str, Any] | None) -> str:
    for item in items:
        if not isinstance(item, dict):
            continue
        name = clean_hotel_name(item)
        if name:
            return name
    return ""


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Prefer simplified-Chinese sans fonts. This avoids falling back to Arial Unicode,
    # whose Chinese glyphs look looser and less like a native app/poster.
    candidates: list[tuple[str, int]] = []
    if bold:
        candidates.extend(
            [
                ("/System/Library/Fonts/PingFang.ttc", 5),
                ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
                ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
            ]
        )
    else:
        candidates.extend(
            [
                ("/System/Library/Fonts/PingFang.ttc", 0),
                ("/System/Library/Fonts/STHeiti Light.ttc", 1),
                ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
                ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
            ]
        )
    candidates.extend(
        [
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
        ]
    )
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, value: str, image_font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), value, font=image_font)
    return box[2] - box[0]


def ellipsize(draw: ImageDraw.ImageDraw, value: str, image_font: ImageFont.ImageFont, max_width: int) -> str:
    value = str(value or "")
    if text_width(draw, value, image_font) <= max_width:
        return value
    suffix = "..."
    while value and text_width(draw, value + suffix, image_font) > max_width:
        value = value[:-1]
    return value + suffix if value else suffix


def wrap_text(draw: ImageDraw.ImageDraw, value: str, image_font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    chars = list(str(value or ""))
    lines: list[str] = []
    current = ""
    for char in chars:
        candidate = current + char
        if current and text_width(draw, candidate, image_font) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current)
    if len(lines) == max_lines:
        consumed = sum(len(line) for line in lines)
        if consumed < len(chars):
            lines[-1] = ellipsize(draw, lines[-1], image_font, max_width)
    return lines[:max_lines]


def is_generic_area(area: str) -> bool:
    text = to_simplified(area).strip()
    return not text or any(word in text for word in GENERIC_AREA_WORDS)


def normalize_source_area(city: str, raw_area: str) -> str:
    area = to_simplified(str(raw_area or "")).strip()
    groups = SOURCE_AREA_GROUPS.get(city, {})
    for label, aliases in groups.items():
        if area in aliases:
            return label
    if not area:
        return f"{city}待确认片区"
    if area.endswith("片区"):
        return area.replace("深圳国际会展中心片区", "国际会展中心片区")
    return f"{area}片区"


def cache_result(raw: dict[str, Any]) -> dict[str, Any]:
    return raw.get("result") if isinstance(raw.get("result"), dict) else raw


def cache_hotel_key(choice: dict[str, Any]) -> str:
    hotel_id = str(choice.get("hotel_id") or choice.get("hotelId") or "").strip()
    if hotel_id:
        return hotel_id
    return clean_hotel_name(choice)


def useful_area_context(choice: dict[str, Any]) -> int:
    score = 0
    for key in ("area_name", "area", "area_hint", "area_source"):
        value = to_simplified(str(choice.get(key) or "")).strip()
        if not value:
            continue
        score += 1
        if key == "area_hint":
            score += 2
        if not is_generic_area(value):
            score += 3
    return score


def normalize_cache_area(city: str, choice: dict[str, Any], supplemental: dict[str, Any] | None = None) -> str:
    supplemental = supplemental or {}
    raw_area = to_simplified(str(choice.get("area_name") or choice.get("area") or "")).strip()
    parts = [
        clean_hotel_name(choice),
        choice.get("hotel_name"),
        choice.get("hotel_original_name"),
        raw_area,
        choice.get("area_hint"),
        supplemental.get("area_name"),
        supplemental.get("area"),
        supplemental.get("area_hint"),
        supplemental.get("hotel_original_name"),
    ]
    haystack = " ".join(to_simplified(str(part or "")).lower() for part in parts if part)
    for label, aliases in AREA_ALIASES.get(city, ()):
        if any(alias.lower() in haystack for alias in aliases):
            return label
    if not is_generic_area(raw_area):
        if raw_area == "深圳国际会展中心片区":
            return "国际会展中心片区"
        return raw_area if raw_area.endswith("片区") else f"{raw_area}片区"
    return f"{city}待确认片区"


def display_area(city: str, area: str | None) -> str:
    text = to_simplified(str(area or "")).strip()
    if not text or is_generic_area(text):
        return city
    return text


def area_rank_title(city: str) -> str:
    return f"{city}-反向旅游推荐片区榜"


def price_value(item: dict[str, Any]) -> int | None:
    for key in ("holiday_avg_nightly_tax_total_value", "price", "holiday_avg_price", "avg_price"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            continue
    return None


def diff_value(item: dict[str, Any]) -> int | None:
    for key in ("price_diff_nightly", "diff", "diff_per_night", "avg_diff", "price_diff"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            continue
    return None


def collect_source_hotels(data: dict[str, Any], city: str, max_rise: int) -> list[dict[str, Any]]:
    by_hotel: dict[str, dict[str, Any]] = {}
    for list_key in LIST_KEYS:
        for item in data.get(list_key) or []:
            hotel_id = str(item.get("hotel_id") or item.get("name") or "").strip()
            if not hotel_id:
                continue
            diff = diff_value(item)
            price = price_value(item)
            if diff is None or price is None or diff > max_rise:
                continue
            name = to_simplified(str(item.get("name") or "")).strip()
            normalized = {
                "hotel_id": hotel_id,
                "name": name,
                "area": normalize_source_area(city, str(item.get("area") or "")),
                "price": price,
                "diff": diff,
                "note": to_simplified(str(item.get("note") or "")).strip(),
            }
            existing = by_hotel.get(hotel_id)
            if existing is None or normalized["diff"] < existing["diff"]:
                by_hotel[hotel_id] = normalized
    return list(by_hotel.values())


def cache_file_matches(d: dict[str, Any], city: str, holiday_contains: str) -> bool:
    holiday = d.get("holiday") or {}
    return to_simplified(str(d.get("city") or "")) == to_simplified(city) and holiday_contains in str(holiday.get("code") or "")


def load_hotel_name_cache(cache_dir: Path) -> dict[str, dict[str, Any]]:
    path = cache_dir.parent / "hotel_name_cache.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        items = raw if isinstance(raw, dict) else {}
    return {
        str(hotel_id): value
        for hotel_id, value in items.items()
        if isinstance(value, dict)
    }


def load_hotel_feature_cache(cache_dir: Path) -> dict[str, dict[str, Any]]:
    path = cache_dir.parent / "hotel_feature_cache.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        items = raw if isinstance(raw, dict) else {}
    return {
        str(hotel_id): value
        for hotel_id, value in items.items()
        if isinstance(value, dict)
    }


def collect_cache_hotels(
    city: str,
    cache_dir: Path,
    holiday_contains: str,
    advanced_only: bool,
    max_rise: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    supplemental_by_hotel: dict[str, dict[str, Any]] = {}
    matching_files: list[dict[str, Any]] = []
    hotel_name_cache = load_hotel_name_cache(cache_dir)
    hotel_feature_cache = load_hotel_feature_cache(cache_dir)

    for path in cache_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        d = cache_result(raw)
        if not cache_file_matches(d, city, holiday_contains):
            continue
        feature_filters = d.get("feature_filters") or {}
        is_advanced_file = ((feature_filters.get("advanced") or {}).get("value") == "yes")
        choices = d.get("choices") or []
        matching_files.append(
            {
                "file": path.name,
                "mtime": path.stat().st_mtime,
                "choices": len(choices),
                "advanced": is_advanced_file,
                "filters": feature_filters,
            }
        )
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            hotel_key = cache_hotel_key(choice)
            if not hotel_key:
                continue
            previous = supplemental_by_hotel.get(hotel_key)
            if previous is None or useful_area_context(choice) > useful_area_context(previous):
                supplemental_by_hotel[hotel_key] = choice
            cached_features = hotel_feature_cache.get(hotel_key) or {}
            is_cached_advanced = choice.get("is_advanced") is True or cached_features.get("is_advanced") is True
            if advanced_only and not (is_advanced_file or is_cached_advanced):
                continue
            candidates.append((path.stat().st_mtime, path.name, choice))

    by_hotel: dict[str, tuple[float, str, dict[str, Any]]] = {}
    for mtime, file_name, choice in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True):
        hotel_key = cache_hotel_key(choice)
        if not hotel_key:
            continue
        existing = by_hotel.get(hotel_key)
        if existing is None:
            by_hotel[hotel_key] = (mtime, file_name, choice)
            continue
        existing_diff = diff_value(existing[2])
        current_diff = diff_value(choice)
        if mtime == existing[0] and current_diff is not None and (existing_diff is None or current_diff < existing_diff):
            by_hotel[hotel_key] = (mtime, file_name, choice)

    hotels: list[dict[str, Any]] = []
    excluded_prefixes = CITY_EXCLUSION_PREFIXES.get(city, ())
    for hotel_key, (mtime, file_name, choice) in by_hotel.items():
        diff = diff_value(choice)
        price = price_value(choice)
        if diff is None or price is None or diff > max_rise:
            continue
        supplemental = supplemental_by_hotel.get(hotel_key, {})
        name = clean_hotel_name(choice)
        name = clean_hotel_name_from_records(hotel_name_cache.get(hotel_key), choice, supplemental) or name
        if (
            not name
            or chinese_count(name) < 4
            or name in GENERIC_HOTEL_NAMES
            or any(name.startswith(prefix) for prefix in excluded_prefixes)
        ):
            continue
        hotels.append(
            {
                "hotel_id": hotel_key,
                "name": name,
                "area": normalize_cache_area(city, choice, supplemental),
                "price": price,
                "diff": diff,
                "source_file": file_name,
                "source_cached_at": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            }
        )

    meta = {
        "cache_dir": str(cache_dir),
        "holiday_contains": holiday_contains,
        "advanced_only": advanced_only,
        "max_rise": max_rise,
        "matching_file_count": len(matching_files),
        "candidate_count": len(candidates),
        "unique_hotel_count": len(by_hotel),
        "included_hotel_count": len(hotels),
        "matching_files": sorted(matching_files, key=lambda item: item["mtime"], reverse=True),
    }
    return hotels, meta


def price_text(value: int) -> str:
    return f"约¥{value}"


def diff_text(value: int, *, average: bool = False) -> str:
    prefix = "平均" if average else ""
    if value < 0:
        return f"{prefix}降约¥{abs(value)}/晚"
    if value == 0:
        return f"{prefix}持平"
    return f"{prefix}涨约¥{value}/晚"


def build_area_rows(hotels: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for hotel in hotels:
        grouped.setdefault(hotel["area"], []).append(hotel)

    rows: list[dict[str, Any]] = []
    for area, area_hotels in grouped.items():
        area_hotels = sorted(area_hotels, key=lambda item: (item["diff"], item["price"], item["name"]))
        avg_price = round(sum(item["price"] for item in area_hotels) / len(area_hotels))
        avg_diff = round(sum(item["diff"] for item in area_hotels) / len(area_hotels))
        discount_count = sum(1 for item in area_hotels if item["diff"] < 0)
        flat_count = sum(1 for item in area_hotels if item["diff"] == 0)
        rows.append(
            {
                "area": area,
                "hotel_count": len(area_hotels),
                "discount_hotel_count": discount_count,
                "flat_hotel_count": flat_count,
                "average_price": avg_price,
                "average_diff": avg_diff,
                "price_range": [min(item["price"] for item in area_hotels), max(item["price"] for item in area_hotels)],
                "hotels": area_hotels,
                "representative_hotels": [item["name"] for item in area_hotels[:3]],
            }
        )

    rows.sort(
        key=lambda item: (
            "待确认" in item["area"],
            -item["hotel_count"],
            item["average_diff"],
            item["average_price"],
            item["area"],
        )
    )
    for index, item in enumerate(rows[:limit], start=1):
        item["rank"] = index
        item["summary"] = (
            f"{item['hotel_count']}家高级酒店，"
            f"{item['discount_hotel_count']}家降价"
            + (f"，{item['flat_hotel_count']}家持平" if item["flat_hotel_count"] else "")
        )
    return rows[:limit]


def build_area_rank(data: dict[str, Any], city: str, limit: int, max_rise: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hotels = collect_source_hotels(data, city, max_rise)
    return build_area_rows(hotels, limit), {
        "source": "source_data",
        "included_hotel_count": len(hotels),
        "max_rise": max_rise,
    }


def build_area_rank_from_cache(
    city: str,
    cache_dir: Path,
    holiday_contains: str,
    advanced_only: bool,
    limit: int,
    max_rise: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hotels, meta = collect_cache_hotels(city, cache_dir, holiday_contains, advanced_only, max_rise)
    meta["source"] = "search_cache"
    return build_area_rows(hotels, limit), meta


def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json_dict(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_city_intro_text(city: str) -> str:
    return CITY_INTRO_TEXTS.get(city, f"{city}适合结合城市景点、周边片区和高级酒店分布安排短途旅行。")


def user_facing_city_intro_text(city: str, text: str) -> str:
    cleaned = to_simplified(str(text or "")).strip()
    cleaned = re.sub(r"^\s*维基百科资料[:：]\s*", "", cleaned)
    cleaned = cleaned.replace("；后续生成过一次后会写入本地记录。", "。")
    cleaned = cleaned.replace("；后续生成过一次后会写入本地记录", "")
    cleaned = cleaned.replace("后续生成过一次后会写入本地记录。", "")
    cleaned = cleaned.replace("后续生成过一次后会写入本地记录", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[；;，,、\s]+$", "", cleaned)
    if cleaned and not cleaned.endswith(("。", "！", "？")):
        cleaned += "。"
    if any(pattern in cleaned for pattern in GENERIC_CITY_INTRO_PATTERNS):
        return default_city_intro_text(city)
    return cleaned or default_city_intro_text(city)


def normalize_city_intro(city: str, item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = user_facing_city_intro_text(city, str(item.get("text") or ""))
    source = to_simplified(str(item.get("source") or "")).strip() or "维基百科"
    if not text or "维基" not in source:
        return None
    return {
        "city": to_simplified(str(item.get("city") or city)).strip() or city,
        "text": text,
        "source": source,
        "source_url": str(item.get("source_url") or "").strip(),
        "updated_at": str(item.get("updated_at") or "").strip(),
    }


def load_city_intro(city: str, source_path: Path, cache_path: Path) -> dict[str, Any]:
    source_data = read_json_dict(source_path)
    source_intro = normalize_city_intro(city, source_data.get("city_wikipedia_intro"))
    if source_intro is not None:
        return source_intro

    cache_data = read_json_dict(cache_path)
    cache_items = cache_data.get("items") if isinstance(cache_data.get("items"), dict) else cache_data
    cache_intro = normalize_city_intro(city, cache_items.get(city) if isinstance(cache_items, dict) else None)
    if cache_intro is not None:
        return cache_intro

    fallback = {
        "city": city,
        "text": default_city_intro_text(city),
        "source": "维基百科",
        "source_url": f"https://zh.wikipedia.org/wiki/{city}",
        "updated_at": "",
    }
    return fallback


def persist_city_intro(city: str, intro: dict[str, Any], cache_path: Path) -> None:
    cache_data = read_json_dict(cache_path)
    items = cache_data.get("items") if isinstance(cache_data.get("items"), dict) else {}
    items = dict(items)
    normalized = normalize_city_intro(city, intro)
    if normalized is None:
        return
    if not normalized.get("updated_at"):
        normalized["updated_at"] = datetime.now().isoformat(timespec="seconds")
    items[city] = normalized
    write_json_dict(cache_path, {"version": 1, "items": items})


def update_source_data(
    source_path: Path,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    city_intro: dict[str, Any],
) -> None:
    data: dict[str, Any] = {}
    if source_path.exists():
        data = json.loads(source_path.read_text(encoding="utf-8"))
    data["area_region_rank"] = rows
    data["area_region_rank_source"] = meta
    data["city_wikipedia_intro"] = city_intro
    source_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_card(
    output_path: Path,
    city: str,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    city_intro: dict[str, Any],
) -> None:
    canvas = Image.new("RGB", (1080, 1440), "#f6f0e6")
    draw = ImageDraw.Draw(canvas)
    title_font = font(54, True)
    kicker_font = font(23, True)
    subtitle_font = font(22)
    rank_font = font(24, True)
    area_font = font(27, True)
    metric_big_font = font(27, True)
    metric_font = font(19, True)
    body_font = font(18)
    small_font = font(16)
    tiny_font = font(14)

    draw.rectangle((0, 0, 1080, 16), fill="#123f39")
    draw.rounded_rectangle((60, 46, 286, 88), radius=20, fill="#123f39")
    draw.text((82, 54), f"{city} · 2026端午", fill="#ffffff", font=kicker_font)
    draw.text((60, 114), area_rank_title(city), fill="#123f39", font=title_font)
    draw.text((62, 179), "按最新数据生成，同一家酒店取最新记录；适合按片区规划端午反向短途游", fill="#6b675e", font=subtitle_font)
    draw.text((860, 56), "制作人：小中", fill="#7b6b59", font=body_font)

    included = int(meta.get("included_hotel_count") or 0)
    source_note = f"纳入{included}家酒店 · {city}-端午数据"
    draw.rounded_rectangle((60, 218, 1020, 258), radius=18, fill="#ead6bd")
    draw.text((84, 228), source_note, fill="#60422f", font=small_font)
    draw.text((760, 228), "价格为每晚含税均价", fill="#60422f", font=small_font)

    rows_top = 280
    row_h = 82
    gap = 10
    for index, item in enumerate(rows):
        y = rows_top + index * (row_h + gap)
        fill = "#fffaf1" if index % 2 == 0 else "#ffffff"
        outline = "#e0d1bc"
        if index < 3:
            fill = "#fff6e6"
            outline = "#d2a274"
        draw.rounded_rectangle((56, y, 1024, y + row_h), radius=18, fill=fill, outline=outline, width=1)

        badge_fill = "#b24f31" if index < 3 else "#123f39"
        draw.rounded_rectangle((78, y + 17, 128, y + 65), radius=14, fill=badge_fill)
        rank_value = str(item["rank"])
        rank_w = text_width(draw, rank_value, rank_font)
        draw.text((103 - rank_w / 2, y + 27), rank_value, fill="#ffffff", font=rank_font)

        area = ellipsize(draw, display_area(city, item["area"]), area_font, 510)
        draw.text((150, y + 12), area, fill="#17212b", font=area_font)
        status = f"{item['hotel_count']}家高级"
        if item["discount_hotel_count"]:
            status += f" · 降{item['discount_hotel_count']}"
        if item["flat_hotel_count"]:
            status += f" · 平{item['flat_hotel_count']}"
        draw.text((150, y + 46), ellipsize(draw, status, body_font, 250), fill="#6a6258", font=body_font)

        reps_list = item["representative_hotels"]
        reps = reps_list[0] + (" 等" if len(reps_list) > 1 else "") if reps_list else ""
        reps = ellipsize(draw, f"代表：{reps}", small_font, 300)
        draw.text((420, y + 48), reps, fill="#807468", font=small_font)

        draw.line((746, y + 18, 746, y + 64), fill="#ead7c0", width=2)
        draw.text((774, y + 13), f"{price_text(item['average_price'])}/晚", fill="#b24f31", font=metric_big_font)
        draw.text((776, y + 49), diff_text(item["average_diff"], average=True), fill="#1f6a50", font=metric_font)

    info_y = rows_top + len(rows) * (row_h + gap) + 2
    draw.rounded_rectangle((56, info_y, 1024, info_y + 78), radius=18, fill="#123f39")
    info = user_facing_city_intro_text(city, str(city_intro.get("text") or ""))
    for offset, line in enumerate(wrap_text(draw, info, small_font, 900, 2)):
        draw.text((86, info_y + 18 + offset * 26), line, fill="#fff7e8", font=small_font)

    foot = "参考价格仅供参考，实际价格以各大软件订购为准。价格为端午每晚含税均价，对比未来一月非法定假期代表时段均价。"
    foot_lines = wrap_text(draw, foot, tiny_font, 940, 2)
    for offset, line in enumerate(foot_lines):
        draw.text((60, 1360 + offset * 22), line, fill="#7a8079", font=tiny_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def render_markdown_section(
    city: str,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    city_intro: dict[str, Any],
) -> str:
    lines = [f"## {city}-2026端午反向旅游推荐片区榜", ""]
    if meta.get("source") == "search_cache":
        lines.append(
            f"本榜单纳入{meta.get('included_hotel_count', 0)}家符合条件的高级酒店。"
        )
        lines.append("")
    for item in rows:
        hotel_names = "、".join(item["representative_hotels"])
        lines.append(
            f"{item['rank']}. {display_area(city, item['area'])}｜{item['hotel_count']}家高级酒店｜"
            f"端午每晚含税均价{price_text(item['average_price'])}｜{diff_text(item['average_diff'], average=True)}"
        )
        lines.append(f"   代表酒店：{hotel_names}")
    lines.append("")
    lines.append(user_facing_city_intro_text(city, str(city_intro.get("text") or "")))
    return "\n".join(lines)


def upsert_markdown_section(
    post_path: Path,
    city: str,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    city_intro: dict[str, Any],
) -> None:
    old_marker = "## 2026端午反向旅游推荐片区榜"
    previous_city_marker = f"## {city}2026端午反向旅游推荐片区榜"
    marker = f"## {city}-2026端午反向旅游推荐片区榜"
    section = render_markdown_section(city, rows, meta, city_intro)
    text = post_path.read_text(encoding="utf-8")
    if marker in text:
        text = re.sub(rf"\n{re.escape(marker)}\n.*?(?=\n数据来源：|\Z)", "\n" + section + "\n", text, flags=re.S)
    elif previous_city_marker in text:
        text = re.sub(rf"\n{re.escape(previous_city_marker)}\n.*?(?=\n数据来源：|\Z)", "\n" + section + "\n", text, flags=re.S)
    elif old_marker in text:
        text = re.sub(r"\n## 2026端午反向旅游推荐片区榜\n.*?(?=\n数据来源：|\Z)", "\n" + section + "\n", text, flags=re.S)
    else:
        text = text.replace("\n数据来源：", "\n" + section + "\n数据来源：")
    if meta.get("source") == "search_cache":
        text = re.sub(
            r"数据来源：本项目基于 Trip\.com 查询结果生成；.*",
            "数据来源：本项目基于 Trip.com 查询结果生成；价格仅作行程参考。",
            text,
        )
    post_path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    source_path = Path(args.source)
    if not source_path.is_absolute():
        source_path = ROOT / source_path

    if args.from_cache:
        rows, meta = build_area_rank_from_cache(
            args.city,
            Path(args.cache_dir),
            args.holiday_contains,
            args.advanced_only,
            max(1, args.limit),
            args.max_rise,
        )
    else:
        data = json.loads(source_path.read_text(encoding="utf-8"))
        rows, meta = build_area_rank(data, args.city, max(1, args.limit), args.max_rise)

    city_intro = load_city_intro(args.city, source_path, Path(args.city_intro_cache))
    persist_city_intro(args.city, city_intro, Path(args.city_intro_cache))
    update_source_data(source_path, rows, meta, city_intro)
    output_path = source_path.parent / "04_area_region_rank.png"
    render_card(output_path, args.city, rows, meta, city_intro)
    post_path = source_path.parent / "xhs_post.md"
    if post_path.exists():
        upsert_markdown_section(post_path, args.city, rows, meta, city_intro)
    print(json.dumps({"output": str(output_path), "rows": rows, "meta": meta}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
