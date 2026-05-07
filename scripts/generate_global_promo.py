from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:5012"
HOLIDAY_CODE = "2026-06-19::端午节"
OUTPUT_DIR = Path("exports/promo")
RAW_DIR = OUTPUT_DIR / "raw"
CITY_CASES = [
    ("曼谷", "Bangkok", "global-bangkok.png"),
    ("吉隆坡", "Kuala Lumpur", "global-kuala-lumpur.png"),
    ("芝加哥", "Chicago", "global-chicago.png"),
    ("巴黎", "Paris", "global-paris.png"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size, index=8 if bold and path.endswith(".ttc") else 0)
        except OSError:
            continue
    return ImageFont.load_default()


def capture_city_screenshots() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        for city, _english, filename in CITY_CASES:
            page = context.new_page()
            page.goto(BASE_URL, wait_until="networkidle")
            page.fill('input[name="city"]', city)
            page.select_option('select[name="holiday_code"]', HOLIDAY_CODE)
            page.select_option('select[name="advanced_filter"]', "all")
            page.select_option('select[name="pool_filter"]', "all")
            page.select_option('select[name="child_facility_filter"]', "all")
            page.click("#submit-btn")
            page.wait_for_selector("#result:not(.hidden)", timeout=90000)
            page.locator("#result").scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            page.screenshot(path=str(RAW_DIR / filename), full_page=False)
            page.close()
        browser.close()


def rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def make_collage() -> None:
    canvas = Image.new("RGB", (1080, 1080), "#eef3f8")
    draw = ImageDraw.Draw(canvas)
    title_font = font(56, bold=True)
    sub_font = font(25)
    label_font = font(30, bold=True)
    small_font = font(18)

    draw.rectangle((0, 0, 1080, 10), fill="#1657d8")
    draw.text((58, 54), "全世界城市都能查", fill="#111827", font=title_font)
    draw.text((60, 126), "曼谷、吉隆坡、芝加哥、巴黎，同样比较假期每晚含税价。", fill="#5f6f84", font=sub_font)
    draw.rounded_rectangle((872, 52, 1022, 92), radius=20, fill="#e7f6f5", outline="#c8e9e7")
    draw.text((902, 61), "Trip.com", fill="#0f8b8d", font=small_font)

    tile_w, tile_h = 468, 380
    positions = [(58, 188), (554, 188), (58, 606), (554, 606)]
    for (city, english, filename), (x, y) in zip(CITY_CASES, positions):
        rounded_rect(draw, (x, y, x + tile_w, y + tile_h), 18, "#ffffff", "#d8e0ea")
        draw.text((x + 24, y + 18), city, fill="#111827", font=label_font)
        draw.text((x + 24, y + 55), english, fill="#6b7a90", font=small_font)

        raw = Image.open(RAW_DIR / filename).convert("RGB")
        crop = raw.crop((0, 0, raw.width, min(raw.height, 1120)))
        target_w = tile_w - 48
        target_h = tile_h - 104
        ratio = max(target_w / crop.width, target_h / crop.height)
        resized = crop.resize((round(crop.width * ratio), round(crop.height * ratio)), Image.LANCZOS)
        left = max(0, (resized.width - target_w) // 2)
        top = 0
        panel = resized.crop((left, top, left + target_w, top + target_h))

        mask = Image.new("L", (target_w, target_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, target_w, target_h), radius=12, fill=255)
        canvas.paste(panel, (x + 24, y + 82), mask)

    draw.text((60, 1015), "输入任意城市名，系统自动识别目的地并对比法定假期与后续代表日期。", fill="#526278", font=small_font)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    canvas.save(OUTPUT_DIR / "09-global-cities.png", quality=95)


def main() -> None:
    capture_city_screenshots()
    make_collage()


if __name__ == "__main__":
    main()
