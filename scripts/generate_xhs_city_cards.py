#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from generate_latest_city_area_rank import run_city_search_refresh  # noqa: E402
from generate_xhs_area_region_rank import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_CITY_INTRO_CACHE,
    build_area_rows,
    collect_cache_hotels,
    diff_text,
    ellipsize,
    font,
    load_city_intro,
    load_hotel_feature_cache,
    persist_city_intro,
    price_text,
    render_card as render_area_card,
    text_width,
    to_simplified,
    wrap_text,
)


CITY_SLUGS = {
    "深圳": "shenzhen",
    "东莞": "dongguan",
    "惠州": "huizhou",
    "广州": "guangzhou",
    "中山": "zhongshan",
    "珠海": "zhuhai",
    "佛山": "foshan",
    "江门": "jiangmen",
    "汕尾": "shanwei",
    "河源": "heyuan",
    "肇庆": "zhaoqing",
    "清远": "qingyuan",
    "云浮": "yunfu",
    "韶关": "shaoguan",
    "汕头": "shantou",
    "揭阳": "jieyang",
    "潮州": "chaozhou",
    "梅州": "meizhou",
    "阳江": "yangjiang",
    "茂名": "maoming",
    "湛江": "zhanjiang",
}

GUANGDONG_SHENZHEN_DISTANCE_ORDER = [
    "佛山",
    "东莞",
    "惠州",
    "广州",
    "中山",
    "珠海",
    "江门",
    "汕尾",
    "河源",
    "肇庆",
    "清远",
    "云浮",
    "韶关",
    "汕头",
    "揭阳",
    "潮州",
    "梅州",
    "阳江",
    "茂名",
    "湛江",
]

LIST_META = {
    "star_no_rise": {
        "index": "01",
        "title": "端午不涨价星级酒店",
        "subtitle": "四星级以上。按端午每晚含税价从低到高排，均为持平或低于平日均价。",
        "empty": "暂无足够星级酒店结果。",
    },
    "family_no_rise": {
        "index": "02",
        "title": "端午不涨价亲子酒店",
        "subtitle": "四星级以上 + 儿童设施 + 泳池。适合带娃短途反向游。",
        "empty": "暂无足够亲子酒店结果。",
    },
    "discount_star": {
        "index": "03",
        "title": "端午降价星级酒店",
        "subtitle": "四星级以上。按“端午每晚含税价 - 平日每晚均价”的降幅排序。",
        "empty": "暂无明显降价星级酒店。",
    },
}

DETAIL_PAGE_SIZE = 20


def list_title(city: str, list_key: str) -> str:
    return f"{city}-{LIST_META[list_key]['title']}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Xiaohongshu city ranking cards from local search cache.")
    parser.add_argument("--city", action="append")
    parser.add_argument("--guangdong-main", action="store_true")
    parser.add_argument("--holiday-contains", default="2026-06-19")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--city-intro-cache", default=str(DEFAULT_CITY_INTRO_CACHE))
    parser.add_argument("--output-root", default=str(ROOT / "exports"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-rise", type=int, default=0)
    parser.add_argument("--min-city-rank-count", type=int, default=8)
    parser.add_argument("--no-rerun-under-count", action="store_true")
    return parser.parse_args()


def output_dir_for_city(output_root: Path, city: str) -> Path:
    slug = CITY_SLUGS.get(city, city.lower())
    return output_root / f"xhs_{slug}_duanwu_2026"


def display_area(city: str, area: str | None) -> str:
    text = to_simplified(str(area or "")).strip()
    if not text or "待确认" in text:
        return city
    if text.endswith("片区"):
        text = text[:-2]
    return text or city


def enrich_with_features(city: str, hotels: list[dict[str, Any]], cache_dir: Path) -> list[dict[str, Any]]:
    feature_cache = load_hotel_feature_cache(cache_dir)
    enriched: list[dict[str, Any]] = []
    for item in hotels:
        hotel = dict(item)
        features = feature_cache.get(str(hotel.get("hotel_id") or "")) or {}
        hotel["child"] = features.get("has_child_facility") is True
        hotel["pool"] = features.get("has_pool") is True
        hotel["advanced"] = features.get("is_advanced") is True
        hotel["name"] = to_simplified(str(hotel.get("name") or "")).strip()
        hotel["area"] = to_simplified(str(hotel.get("area") or "")).strip()
        hotel["display_area"] = display_area(city, hotel["area"])
        hotel["note"] = note_for_hotel(city, hotel)
        enriched.append(hotel)
    return enriched


def note_for_hotel(city: str, hotel: dict[str, Any]) -> str:
    area = str(hotel.get("display_area") or display_area(city, str(hotel.get("area") or "")))
    if hotel.get("child") and hotel.get("pool"):
        return f"{area}，具备儿童设施和泳池，适合亲子短途。"
    if hotel.get("child"):
        return f"{area}，具备儿童设施，适合亲子短途。"
    if hotel.get("pool"):
        return f"{area}，具备泳池，适合短途度假。"
    if int(hotel.get("diff") or 0) < 0:
        return f"{area}，端午每晚含税均价低于平日代表价。"
    return f"{area}，端午价格保持平稳，适合周边短途度假。"


def collect_city_hotels(
    city: str,
    cache_dir: Path,
    holiday_contains: str,
    max_rise: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hotels, meta = collect_cache_hotels(
        city=city,
        cache_dir=cache_dir,
        holiday_contains=holiday_contains,
        advanced_only=True,
        max_rise=max_rise,
    )
    return enrich_with_features(city, hotels, cache_dir), meta


def collect_city_hotels_with_rerun(
    city: str,
    cache_dir: Path,
    holiday_contains: str,
    max_rise: int,
    min_count: int,
    rerun_under_count: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hotels, meta = collect_city_hotels(city, cache_dir, holiday_contains, max_rise)
    if not rerun_under_count or len(hotels) >= min_count:
        return hotels, meta

    meta["low_count_rerun"] = {
        "triggered": True,
        "threshold": min_count,
        "first_included_hotel_count": len(hotels),
        "status": "started",
    }
    print(f"[{city}] 榜单只有 {len(hotels)} 家，低于 {min_count} 家，强制新搜索一次。", file=sys.stderr, flush=True)
    try:
        run_city_search_refresh(city, holiday_contains)
    except Exception as exc:  # noqa: BLE001
        meta["low_count_rerun"].update({"status": "failed", "error": str(exc)})
        print(f"[{city}] 重跑失败，保留首次结果：{exc}", file=sys.stderr, flush=True)
        return hotels, meta

    hotels, refreshed_meta = collect_city_hotels(city, cache_dir, holiday_contains, max_rise)
    refreshed_meta["low_count_rerun"] = {
        "triggered": True,
        "threshold": min_count,
        "first_included_hotel_count": meta["low_count_rerun"]["first_included_hotel_count"],
        "final_included_hotel_count": len(hotels),
        "status": "used_second_result",
    }
    print(f"[{city}] 重跑完成，采用第二次结果：{len(hotels)} 家。", file=sys.stderr, flush=True)
    return hotels, refreshed_meta


def build_lists(hotels: list[dict[str, Any]], limit: int) -> dict[str, list[dict[str, Any]]]:
    star_all = sorted(hotels, key=lambda item: (int(item["price"]), int(item["diff"]), item["name"]))
    star = star_all[:limit]
    family_pool = [item for item in hotels if item.get("child") and item.get("pool")]
    family_all = sorted(family_pool, key=lambda item: (int(item["price"]), int(item["diff"]), item["name"]))
    family = family_all[:limit]
    discount = sorted(
        [item for item in hotels if int(item.get("diff") or 0) < 0],
        key=lambda item: (int(item["diff"]), int(item["price"]), item["name"]),
    )[:limit]
    return {
        "star_no_rise": star,
        "star_no_rise_all": star_all,
        "family_no_rise": family,
        "family_no_rise_all": family_all,
        "discount_star": discount,
    }


def draw_top_bar(draw: ImageDraw.ImageDraw, city: str, title: str, subtitle: str, kicker: str) -> None:
    draw.rectangle((0, 0, 1080, 18), fill="#123f39")
    draw.rounded_rectangle((60, 48, 292, 90), radius=21, fill="#123f39")
    draw.text((82, 56), kicker, fill="#ffffff", font=font(23, True))
    draw.text((60, 116), title, fill="#123f39", font=font(52, True))
    for offset, line in enumerate(wrap_text(draw, subtitle, font(22), 900, 2)):
        draw.text((62, 184 + offset * 30), line, fill="#6b675e", font=font(22))
    draw.text((860, 58), "制作人：小中", fill="#7b6b59", font=font(18, True))


def render_cover(output_path: Path, city: str, lists: dict[str, list[dict[str, Any]]]) -> None:
    canvas = Image.new("RGB", (1080, 1440), "#f6f0e6")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1080, 18), fill="#123f39")
    draw.text((62, 58), "反向旅游好选择", fill="#5f6e65", font=font(24, True))
    draw.rounded_rectangle((60, 150, 414, 200), radius=24, fill="#123f39")
    draw.text((88, 160), f"{city} · 2026端午", fill="#ffffff", font=font(27, True))
    title = f"{city}-端午不涨价\n星级酒店榜单"
    draw.multiline_text((60, 260), title, fill="#123f39", font=font(82, True), spacing=12)
    draw.text((64, 465), "四星级以上｜每晚含税均价｜对比未来一月平日均价", fill="#6b675e", font=font(25))

    boxes = [
        ("星级不涨价", f"{len(lists.get('star_no_rise_all') or lists['star_no_rise'])} 家入选", "#fff7e8"),
        ("亲子推荐", f"{len(lists.get('family_no_rise_all') or lists['family_no_rise'])} 家入选", "#eef7f0"),
        ("降价榜", f"{len(lists['discount_star'])} 家入选", "#fff1ed"),
        ("推荐片区", "按酒店聚集度排序", "#eef3f8"),
    ]
    for index, (heading, body, fill) in enumerate(boxes):
        x = 60 + (index % 2) * 492
        y = 590 + (index // 2) * 210
        draw.rounded_rectangle((x, y, x + 450, y + 168), radius=26, fill=fill, outline="#dfd0b9", width=2)
        draw.text((x + 28, y + 28), heading, fill="#123f39", font=font(34, True))
        draw.text((x + 28, y + 86), body, fill="#b24f31", font=font(31, True))

    draw.rounded_rectangle((60, 1080, 1020, 1200), radius=28, fill="#123f39")
    draw.text((92, 1115), "2026/06/19 - 06/22", fill="#fff7e8", font=font(44, True))
    draw.text((92, 1170), "端午 3 晚｜本地酒店价格对比", fill="#e5d4b9", font=font(23))
    foot = "参考价格仅供参考，实际价格以各大软件订购为准。价格为端午每晚含税均价，对比未来一月非法定假期代表时段均价。"
    for offset, line in enumerate(wrap_text(draw, foot, font(17), 940, 2)):
        draw.text((60, 1340 + offset * 24), line, fill="#7a8079", font=font(17))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def render_list_card(output_path: Path, city: str, list_key: str, rows: list[dict[str, Any]]) -> None:
    meta = LIST_META[list_key]
    canvas = Image.new("RGB", (1080, 1440), "#f6f0e6")
    draw = ImageDraw.Draw(canvas)
    draw_top_bar(draw, city, list_title(city, list_key), meta["subtitle"], f"榜单 {meta['index']}")
    y = 258
    row_h = 88 if len(rows) >= 10 else 96 if len(rows) >= 9 else 106
    gap = 7
    if not rows:
        draw.rounded_rectangle((60, y, 1020, y + 180), radius=24, fill="#fffaf1", outline="#e1d1ba")
        draw.text((100, y + 64), meta["empty"], fill="#657069", font=font(30, True))
    for index, item in enumerate(rows):
        row_y = y + index * (row_h + gap)
        fill = "#fffaf1" if index % 2 == 0 else "#ffffff"
        outline = "#d9c7ad" if index < 3 else "#ead9c0"
        draw.rounded_rectangle((56, row_y, 1024, row_y + row_h), radius=18, fill=fill, outline=outline, width=2 if index < 3 else 1)
        badge_fill = "#b24f31" if index < 3 else "#123f39"
        draw.rounded_rectangle((78, row_y + 19, 128, row_y + 69), radius=14, fill=badge_fill)
        rank = str(index + 1)
        draw.text((103 - text_width(draw, rank, font(24, True)) / 2, row_y + 30), rank, fill="#ffffff", font=font(24, True))

        name_font = font(24, True)
        name = ellipsize(draw, item["name"], name_font, 600)
        draw.text((150, row_y + 9), name, fill="#17212b", font=name_font)
        area = display_area(city, str(item.get("display_area") or item.get("area") or ""))
        meta_line = f"{area}｜端午每晚含税{price_text(item['price'])}"
        draw.text((150, row_y + 38), ellipsize(draw, meta_line, font(18), 430), fill="#6a6258", font=font(18))
        note = ellipsize(draw, str(item.get("note") or ""), font(16), 560)
        draw.text((150, row_y + 62), note, fill="#7e7468", font=font(16))

        draw.text((804, row_y + 12), price_text(item["price"]), fill="#b24f31", font=font(30, True))
        diff = int(item.get("diff") or 0)
        pill_fill = "#edf6ef" if diff < 0 else "#f1f2e8"
        pill_text = diff_text(diff)
        pill_w = text_width(draw, pill_text, font(18, True)) + 24
        draw.rounded_rectangle((800, row_y + 52, 800 + pill_w, row_y + 82), radius=15, fill=pill_fill)
        draw.text((812, row_y + 58), pill_text, fill="#1f6a50" if diff < 0 else "#67622f", font=font(18, True))

    draw.rounded_rectangle((60, 1268, 440, 1312), radius=22, fill="#ead6bd")
    draw.text((84, 1278), f"四星级以上｜{city}｜2026端午", fill="#60422f", font=font(18, True))
    foot = "参考价格仅供参考，实际价格以各大软件订购为准。差额=端午每晚含税均价 - 平日代表时段每晚含税均价。制作人：小中"
    for offset, line in enumerate(wrap_text(draw, foot, font(16), 940, 2)):
        draw.text((60, 1350 + offset * 22), line, fill="#7a8079", font=font(16))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def detail_page_path(base_path: Path, page_index: int) -> Path:
    if page_index == 0:
        return base_path
    return base_path.with_name(f"{base_path.stem}_p{page_index + 1:02d}{base_path.suffix}")


def detail_page_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not rows:
        return [[]]
    return [rows[index : index + DETAIL_PAGE_SIZE] for index in range(0, len(rows), DETAIL_PAGE_SIZE)]


def cleanup_detail_pages(base_path: Path) -> None:
    for stale_path in base_path.parent.glob(f"{base_path.stem}_p*{base_path.suffix}"):
        stale_path.unlink(missing_ok=True)


def render_star_detail_page(
    output_path: Path,
    city: str,
    rows: list[dict[str, Any]],
    *,
    total_count: int,
    page_index: int,
    page_count: int,
) -> None:
    row_count = max(1, len(rows))
    row_h = 94
    gap = 8
    header_h = 286
    foot_h = 150
    height = max(1440, header_h + row_count * (row_h + gap) + foot_h)
    canvas = Image.new("RGB", (1080, height), "#f6f0e6")
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, 1080, 18), fill="#123f39")
    draw.rounded_rectangle((60, 48, 306, 90), radius=21, fill="#123f39")
    draw.text((82, 56), f"星级详细榜 {page_index + 1}/{page_count}", fill="#ffffff", font=font(21, True))
    draw.text((60, 116), f"{city}-端午不涨价星级酒店详细榜", fill="#123f39", font=font(46, True))
    subtitle = "四星级以上。囊括全部入选酒店，按端午每晚含税价从低到高。"
    for offset, line in enumerate(wrap_text(draw, subtitle, font(21), 900, 2)):
        draw.text((62, 182 + offset * 29), line, fill="#6b675e", font=font(21))
    draw.text((860, 58), "制作人：小中", fill="#7b6b59", font=font(18, True))
    draw.rounded_rectangle((60, 232, 1020, 266), radius=17, fill="#ead6bd")
    draw.text(
        (84, 239),
        f"共{total_count}家入选 · 第{page_index + 1}/{page_count}页 · {city}-2026端午 · 价格为每晚含税均价",
        fill="#60422f",
        font=font(16, True),
    )

    y = header_h
    if not rows:
        draw.rounded_rectangle((60, y, 1020, y + 180), radius=24, fill="#fffaf1", outline="#e1d1ba")
        draw.text((100, y + 64), "暂无符合条件的星级酒店结果。", fill="#657069", font=font(30, True))
    start_rank = page_index * DETAIL_PAGE_SIZE
    for index, item in enumerate(rows):
        row_y = y + index * (row_h + gap)
        fill = "#fffaf1" if index % 2 == 0 else "#ffffff"
        outline = "#d9c7ad" if index < 3 else "#ead9c0"
        draw.rounded_rectangle((56, row_y, 1024, row_y + row_h), radius=18, fill=fill, outline=outline, width=2 if index < 3 else 1)

        badge_fill = "#b24f31" if index < 3 else "#123f39"
        draw.rounded_rectangle((78, row_y + 18, 132, row_y + 64), radius=14, fill=badge_fill)
        rank = str(start_rank + index + 1)
        draw.text((105 - text_width(draw, rank, font(22, True)) / 2, row_y + 29), rank, fill="#ffffff", font=font(22, True))

        name_font = font(23, True)
        name = ellipsize(draw, str(item["name"]), name_font, 590)
        draw.text((152, row_y + 8), name, fill="#17212b", font=name_font)

        area = display_area(city, str(item.get("display_area") or item.get("area") or ""))
        meta_line = f"{area}｜四星级以上｜端午不涨价"
        draw.text((152, row_y + 36), ellipsize(draw, meta_line, font(16), 500), fill="#6a6258", font=font(16))
        note = "端午价格保持平稳，适合短途度假。"
        if int(item.get("diff") or 0) < 0:
            note = "端午低于平日代表价，适合短途度假。"
        if row_h >= 86:
            draw.text((152, row_y + 59), ellipsize(draw, note, font(15), 560), fill="#7e7468", font=font(15))

        draw.text((806, row_y + 12), price_text(item["price"]), fill="#b24f31", font=font(28, True))
        diff = int(item.get("diff") or 0)
        pill_text = diff_text(diff)
        pill_fill = "#edf6ef" if diff < 0 else "#f1f2e8"
        pill_w = text_width(draw, pill_text, font(17, True)) + 24
        draw.rounded_rectangle((804, row_y + 50, 804 + pill_w, row_y + 79), radius=15, fill=pill_fill)
        draw.text((816, row_y + 56), pill_text, fill="#1f6a50" if diff < 0 else "#67622f", font=font(17, True))

    footer_y = height - 114
    draw.rounded_rectangle((60, footer_y, 458, footer_y + 44), radius=22, fill="#ead6bd")
    draw.text((84, footer_y + 10), f"四星级以上｜{city}｜2026端午", fill="#60422f", font=font(17, True))
    foot = "参考价格仅供参考，实际价格以各大软件订购为准。差额=端午每晚含税均价 - 平日代表时段每晚含税均价。制作人：小中"
    for offset, line in enumerate(wrap_text(draw, foot, font(15), 940, 2)):
        draw.text((60, footer_y + 66 + offset * 22), line, fill="#7a8079", font=font(15))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def render_star_detail_cards(base_path: Path, city: str, rows: list[dict[str, Any]]) -> list[Path]:
    cleanup_detail_pages(base_path)
    pages = detail_page_rows(rows)
    output_paths: list[Path] = []
    for page_index, page_rows in enumerate(pages):
        output_path = detail_page_path(base_path, page_index)
        render_star_detail_page(
            output_path,
            city,
            page_rows,
            total_count=len(rows),
            page_index=page_index,
            page_count=len(pages),
        )
        output_paths.append(output_path)
    return output_paths


def render_family_detail_page(
    output_path: Path,
    city: str,
    rows: list[dict[str, Any]],
    *,
    total_count: int,
    page_index: int,
    page_count: int,
) -> None:
    row_count = max(1, len(rows))
    row_h = 94
    gap = 8
    header_h = 286
    foot_h = 150
    height = max(1440, header_h + row_count * (row_h + gap) + foot_h)
    canvas = Image.new("RGB", (1080, height), "#f6f0e6")
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, 1080, 18), fill="#123f39")
    draw.rounded_rectangle((60, 48, 306, 90), radius=21, fill="#123f39")
    draw.text((82, 56), f"亲子详细榜 {page_index + 1}/{page_count}", fill="#ffffff", font=font(21, True))
    draw.text((60, 116), f"{city}-端午不涨价亲子酒店详细榜", fill="#123f39", font=font(46, True))
    subtitle = "四星级以上 + 儿童设施 + 泳池。囊括全部入选酒店，按端午每晚含税价从低到高。"
    for offset, line in enumerate(wrap_text(draw, subtitle, font(21), 900, 2)):
        draw.text((62, 182 + offset * 29), line, fill="#6b675e", font=font(21))
    draw.text((860, 58), "制作人：小中", fill="#7b6b59", font=font(18, True))
    draw.rounded_rectangle((60, 232, 1020, 266), radius=17, fill="#ead6bd")
    draw.text(
        (84, 239),
        f"共{total_count}家入选 · 第{page_index + 1}/{page_count}页 · {city}-2026端午 · 价格为每晚含税均价",
        fill="#60422f",
        font=font(16, True),
    )

    y = header_h
    if not rows:
        draw.rounded_rectangle((60, y, 1020, y + 180), radius=24, fill="#fffaf1", outline="#e1d1ba")
        draw.text((100, y + 64), "暂无符合条件的亲子酒店结果。", fill="#657069", font=font(30, True))
    start_rank = page_index * DETAIL_PAGE_SIZE
    for index, item in enumerate(rows):
        row_y = y + index * (row_h + gap)
        fill = "#fffaf1" if index % 2 == 0 else "#ffffff"
        outline = "#d9c7ad" if index < 3 else "#ead9c0"
        draw.rounded_rectangle((56, row_y, 1024, row_y + row_h), radius=18, fill=fill, outline=outline, width=2 if index < 3 else 1)

        badge_fill = "#b24f31" if index < 3 else "#123f39"
        draw.rounded_rectangle((78, row_y + 18, 132, row_y + 64), radius=14, fill=badge_fill)
        rank = str(start_rank + index + 1)
        draw.text((105 - text_width(draw, rank, font(22, True)) / 2, row_y + 29), rank, fill="#ffffff", font=font(22, True))

        name_font = font(23, True)
        name = ellipsize(draw, str(item["name"]), name_font, 590)
        draw.text((152, row_y + 8), name, fill="#17212b", font=name_font)

        area = display_area(city, str(item.get("display_area") or item.get("area") or ""))
        meta_line = f"{area}｜四星级以上｜儿童设施+泳池"
        draw.text((152, row_y + 36), ellipsize(draw, meta_line, font(16), 500), fill="#6a6258", font=font(16))
        note = "具备儿童设施和泳池，适合亲子短途。"
        if row_h >= 86:
            draw.text((152, row_y + 59), ellipsize(draw, note, font(15), 560), fill="#7e7468", font=font(15))

        draw.text((806, row_y + 12), price_text(item["price"]), fill="#b24f31", font=font(28, True))
        diff = int(item.get("diff") or 0)
        pill_text = diff_text(diff)
        pill_fill = "#edf6ef" if diff < 0 else "#f1f2e8"
        pill_w = text_width(draw, pill_text, font(17, True)) + 24
        draw.rounded_rectangle((804, row_y + 50, 804 + pill_w, row_y + 79), radius=15, fill=pill_fill)
        draw.text((816, row_y + 56), pill_text, fill="#1f6a50" if diff < 0 else "#67622f", font=font(17, True))

    footer_y = height - 114
    draw.rounded_rectangle((60, footer_y, 508, footer_y + 44), radius=22, fill="#ead6bd")
    draw.text((84, footer_y + 10), f"四星级以上｜亲子设施+泳池｜{city}｜2026端午", fill="#60422f", font=font(17, True))
    foot = "参考价格仅供参考，实际价格以各大软件订购为准。差额=端午每晚含税均价 - 平日代表时段每晚含税均价。制作人：小中"
    for offset, line in enumerate(wrap_text(draw, foot, font(15), 940, 2)):
        draw.text((60, footer_y + 66 + offset * 22), line, fill="#7a8079", font=font(15))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def render_family_detail_cards(base_path: Path, city: str, rows: list[dict[str, Any]]) -> list[Path]:
    cleanup_detail_pages(base_path)
    pages = detail_page_rows(rows)
    output_paths: list[Path] = []
    for page_index, page_rows in enumerate(pages):
        output_path = detail_page_path(base_path, page_index)
        render_family_detail_page(
            output_path,
            city,
            page_rows,
            total_count=len(rows),
            page_index=page_index,
            page_count=len(pages),
        )
        output_paths.append(output_path)
    return output_paths


def render_cards_html(output_path: Path, city: str, lists: dict[str, list[dict[str, Any]]]) -> None:
    rows = [
        "<!doctype html><html><head><meta charset='utf-8'><title>",
        f"{city}-2026端午反向旅游榜单",
        "</title></head><body>",
        f"<h1>{city}-2026端午反向旅游榜单</h1>",
    ]
    for key in ("star_no_rise", "family_no_rise", "discount_star"):
        items = lists[key]
        rows.append(f"<h2>{list_title(city, key)}</h2><ol>")
        for item in items:
            rows.append(
                f"<li>{item['name']}｜{display_area(city, item.get('display_area') or item.get('area'))}｜端午每晚含税{price_text(item['price'])}｜{diff_text(item['diff'])}</li>"
            )
        rows.append("</ol>")
    for title, detail_items in (
        (f"{city}-端午不涨价星级酒店详细榜", lists.get("star_no_rise_all") or []),
        (f"{city}-端午不涨价亲子酒店详细榜", lists.get("family_no_rise_all") or []),
    ):
        pages = detail_page_rows(detail_items)
        rows.append(f"<h2>{title}</h2>")
        for page_index, page_items in enumerate(pages):
            if len(pages) > 1:
                rows.append(f"<h3>第{page_index + 1}/{len(pages)}页</h3>")
            rows.append(f"<ol start='{page_index * DETAIL_PAGE_SIZE + 1}'>")
            for item in page_items:
                rows.append(
                    f"<li>{item['name']}｜{display_area(city, item.get('display_area') or item.get('area'))}｜端午每晚含税{price_text(item['price'])}｜{diff_text(item['diff'])}</li>"
                )
            rows.append("</ol>")
    rows.append("</body></html>")
    output_path.write_text("\n".join(rows), encoding="utf-8")


def render_post_markdown(output_path: Path, city: str, lists: dict[str, list[dict[str, Any]]], area_rows: list[dict[str, Any]]) -> None:
    lines = [
        f"# {city}-2026端午反向旅游榜单",
        "",
        "价格前均为“约”，参考价格仅供参考，实际价格以各大软件订购为准。制作人：小中",
        "",
    ]
    for key in ("star_no_rise", "family_no_rise", "discount_star"):
        items = lists[key]
        lines.extend([f"## {list_title(city, key)}", ""])
        if not items:
            lines.append("暂无符合条件的酒店。")
        for index, item in enumerate(items, start=1):
            lines.append(
                f"{index}. {item['name']}｜{display_area(city, item.get('display_area') or item.get('area'))}｜端午每晚含税{price_text(item['price'])}｜{diff_text(item['diff'])}"
            )
        lines.append("")
    star_all = lists.get("star_no_rise_all") or []
    lines.extend([f"## {city}-端午不涨价星级酒店详细榜", ""])
    if not star_all:
        lines.append("暂无符合条件的星级酒店。")
    for page_index, page_items in enumerate(detail_page_rows(star_all)):
        if len(star_all) > DETAIL_PAGE_SIZE:
            lines.extend([f"### 第{page_index + 1}/{len(detail_page_rows(star_all))}页", ""])
        for index, item in enumerate(page_items, start=page_index * DETAIL_PAGE_SIZE + 1):
            lines.append(
                f"{index}. {item['name']}｜{display_area(city, item.get('display_area') or item.get('area'))}｜端午每晚含税{price_text(item['price'])}｜{diff_text(item['diff'])}"
            )
        if page_items:
            lines.append("")
    lines.append("")
    family_all = lists.get("family_no_rise_all") or []
    lines.extend([f"## {city}-端午不涨价亲子酒店详细榜", ""])
    if not family_all:
        lines.append("暂无符合条件的亲子酒店。")
    for page_index, page_items in enumerate(detail_page_rows(family_all)):
        if len(family_all) > DETAIL_PAGE_SIZE:
            lines.extend([f"### 第{page_index + 1}/{len(detail_page_rows(family_all))}页", ""])
        for index, item in enumerate(page_items, start=page_index * DETAIL_PAGE_SIZE + 1):
            lines.append(
                f"{index}. {item['name']}｜{display_area(city, item.get('display_area') or item.get('area'))}｜端午每晚含税{price_text(item['price'])}｜{diff_text(item['diff'])}"
            )
        if page_items:
            lines.append("")
    lines.append("")
    lines.extend([f"## {city}-2026端午反向旅游推荐片区榜", ""])
    for item in area_rows:
        lines.append(
            f"{item['rank']}. {display_area(city, item.get('area'))}｜{item['hotel_count']}家高级酒店｜"
            f"端午每晚含税均价{price_text(item['average_price'])}｜{diff_text(item['average_diff'], average=True)}"
        )
        lines.append(f"   代表酒店：{'、'.join(item['representative_hotels'])}")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_source_data(
    path: Path,
    city: str,
    lists: dict[str, list[dict[str, Any]]],
    area_rows: list[dict[str, Any]],
    meta: dict[str, Any],
    city_intro: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(
        {
            "city": city,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "lists": lists,
            "latest_city_rank": lists["star_no_rise"],
            "latest_discount_rank": lists["discount_star"],
            "area_region_rank": area_rows,
            "latest_rank_source": meta,
            "area_region_rank_source": meta,
            "city_wikipedia_intro": city_intro,
        }
    )
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate_city(args: argparse.Namespace, city: str) -> dict[str, Any]:
    cache_dir = Path(args.cache_dir)
    output_dir = output_dir_for_city(Path(args.output_root), city)
    source_path = output_dir / "source_data.json"
    hotels, meta = collect_city_hotels_with_rerun(
        city=city,
        cache_dir=cache_dir,
        holiday_contains=args.holiday_contains,
        max_rise=args.max_rise,
        min_count=max(0, args.min_city_rank_count),
        rerun_under_count=not args.no_rerun_under_count,
    )
    limit = max(1, args.limit)
    lists = build_lists(hotels, limit)
    area_rows = build_area_rows(hotels, limit)
    city_intro = load_city_intro(city, source_path, Path(args.city_intro_cache))
    persist_city_intro(city, city_intro, Path(args.city_intro_cache))
    write_source_data(source_path, city, lists, area_rows, meta, city_intro)
    render_cover(output_dir / "00_cover.png", city, lists)
    render_list_card(output_dir / "01_star_no_rise.png", city, "star_no_rise", lists["star_no_rise"])
    render_list_card(output_dir / "02_family_no_rise.png", city, "family_no_rise", lists["family_no_rise"])
    render_list_card(output_dir / "03_discount_star.png", city, "discount_star", lists["discount_star"])
    render_area_card(output_dir / "04_area_region_rank.png", city, area_rows, meta, city_intro)
    family_detail_pages = render_family_detail_cards(output_dir / "05_family_no_rise_detail.png", city, lists["family_no_rise_all"])
    star_detail_pages = render_star_detail_cards(output_dir / "06_star_no_rise_detail.png", city, lists["star_no_rise_all"])
    render_cards_html(output_dir / "cards.html", city, lists)
    render_post_markdown(output_dir / "xhs_post.md", city, lists, area_rows)
    return {
        "city": city,
        "output_dir": str(output_dir),
        "star": len(lists["star_no_rise"]),
        "star_all": len(lists["star_no_rise_all"]),
        "star_pages": len(star_detail_pages),
        "family": len(lists["family_no_rise"]),
        "family_all": len(lists["family_no_rise_all"]),
        "family_pages": len(family_detail_pages),
        "discount": len(lists["discount_star"]),
        "area": len(area_rows),
        "included": len(hotels),
        "rerun": meta.get("low_count_rerun"),
    }


def main() -> None:
    args = parse_args()
    cities = []
    if args.guangdong_main:
        cities.extend(GUANGDONG_SHENZHEN_DISTANCE_ORDER)
    if args.city:
        cities.extend(args.city)
    deduped: list[str] = []
    for city in cities:
        city = str(city or "").strip()
        if city and city not in deduped:
            deduped.append(city)
    if not deduped:
        raise SystemExit("请传入 --city 或 --guangdong-main")

    results = []
    for city in deduped:
        print(f"[{city}] 开始生成榜单图片...", file=sys.stderr, flush=True)
        result = generate_city(args, city)
        print(
            f"[{city}] 完成：星级前10图{result['star']}，星级详细{result['star_all']}家/{result['star_pages']}页，亲子前10图{result['family']}，亲子详细{result['family_all']}家/{result['family_pages']}页，降价{result['discount']}，片区{result['area']}",
            file=sys.stderr,
            flush=True,
        )
        results.append(result)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
