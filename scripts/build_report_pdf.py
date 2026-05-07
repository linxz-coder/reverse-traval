from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def join_names(values: list[str]) -> str:
    return "、".join(item for item in values if item)


def render_report(data: dict[str, Any], generated_at: str) -> str:
    holiday = data.get("holiday") or {}
    feature_filters = data.get("feature_filters") or {}
    choices = data.get("choices") or []
    areas = data.get("area_recommendations") or []
    filter_labels = [
        f"{feature_filters.get(key, {}).get('name', key)}：{feature_filters.get(key, {}).get('label', '全部')}"
        for key in ("advanced", "pool", "child_facility")
    ]

    area_rows = "\n".join(
        f"""
        <tr>
          <td>{esc(item.get("area_name"))}</td>
          <td class="num">{esc(item.get("hotel_count"))}</td>
          <td class="num">{esc(item.get("average_holiday_nightly_tax_total_price"))}</td>
          <td class="num diff">{esc(item.get("average_price_diff_nightly_text"))}</td>
          <td>{esc(join_names(item.get("representative_hotels") or []))}</td>
        </tr>
        """
        for item in areas
    )

    choice_rows = "\n".join(
        f"""
        <tr>
          <td>{esc(index)}</td>
          <td>{esc(item.get("hotel_name"))}</td>
          <td>{esc(item.get("area_name") or "片区待补充")}</td>
          <td>{esc(item.get("room_type_label"))}</td>
          <td class="num">{esc(item.get("holiday_avg_nightly_tax_total_price"))}</td>
          <td class="num">{esc(item.get("comparison_average_nightly_tax_total_price"))}</td>
          <td class="num diff">{esc(item.get("price_diff_nightly_text"))}</td>
          <td class="num">{esc(item.get("comparison_sample_count"))}</td>
        </tr>
        """
        for index, item in enumerate(choices, start=1)
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{esc(data.get("city"))}反向旅游好选择</title>
  <style>
    @page {{ size: A4; margin: 15mm 12mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #1f2933;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      line-height: 1.55;
    }}
    header {{
      padding-bottom: 14px;
      border-bottom: 2px solid #1f2933;
      margin-bottom: 16px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 25px;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 20px 0 8px;
      font-size: 16px;
    }}
    .meta, .note {{
      color: #52606d;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      margin: 14px 0 16px;
    }}
    .stat {{
      border: 1px solid #d9e2ec;
      border-radius: 6px;
      padding: 8px 10px;
      min-height: 54px;
    }}
    .stat .label {{
      color: #627d98;
      font-size: 11px;
    }}
    .stat .value {{
      margin-top: 3px;
      font-size: 17px;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      page-break-inside: auto;
    }}
    tr {{ page-break-inside: avoid; page-break-after: auto; }}
    th, td {{
      border: 1px solid #d9e2ec;
      padding: 6px 7px;
      vertical-align: top;
    }}
    th {{
      background: #f0f4f8;
      color: #334e68;
      font-weight: 700;
      text-align: left;
    }}
    .num {{
      text-align: right;
      white-space: nowrap;
    }}
    .diff {{
      font-weight: 700;
    }}
    .filters {{
      margin-top: 8px;
    }}
    .filters span {{
      display: inline-block;
      margin: 0 6px 6px 0;
      padding: 3px 7px;
      border: 1px solid #bcccdc;
      border-radius: 999px;
      color: #334e68;
      background: #f8fafc;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{esc(data.get("city"))}端午节反向旅游好选择</h1>
    <div class="meta">
      假期：{esc(holiday.get("name"))}，{esc(holiday.get("check_in"))} 至 {esc(holiday.get("check_out"))}
      （{esc(holiday.get("days"))} 晚） · 生成时间：{esc(generated_at)}
    </div>
    <div class="filters">{"".join(f"<span>{esc(label)}</span>" for label in filter_labels)}</div>
  </header>

  <section class="summary">
    <div class="stat"><div class="label">命中酒店</div><div class="value">{len(choices)} 家</div></div>
    <div class="stat"><div class="label">推荐片区</div><div class="value">{len(areas)} 个</div></div>
    <div class="stat"><div class="label">假期涨幅标准</div><div class="value">≤ 100 元/晚</div></div>
    <div class="stat"><div class="label">价格口径</div><div class="value">含税均价/晚</div></div>
  </section>

  <p class="note">本报告使用“高级酒店=是、游泳池=是、儿童设施=是”的筛选条件。酒店价格取假期含税均价/晚，与后续 30 天非法定假期样本均价/晚对比；差额为假期均价减对比均价。</p>

  <h2>推荐旅游片区</h2>
  <table>
    <thead>
      <tr>
        <th>片区</th>
        <th>酒店数</th>
        <th>假期均价/晚</th>
        <th>均价差额/晚</th>
        <th>代表酒店</th>
      </tr>
    </thead>
    <tbody>{area_rows}</tbody>
  </table>

  <h2>酒店明细</h2>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>酒店</th>
        <th>片区</th>
        <th>房型</th>
        <th>假期含税均价/晚</th>
        <th>平日含税均价/晚</th>
        <th>差额/晚</th>
        <th>样本数</th>
      </tr>
    </thead>
    <tbody>{choice_rows}</tbody>
  </table>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--html-path", type=Path)
    args = parser.parse_args()

    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_text = render_report(data, generated_at)

    if args.html_path:
        args.html_path.parent.mkdir(parents=True, exist_ok=True)
        args.html_path.write_text(html_text, encoding="utf-8")

    args.pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1600})
        page.set_content(html_text, wait_until="load")
        page.pdf(path=str(args.pdf_path), format="A4", print_background=True)
        browser.close()


if __name__ == "__main__":
    main()
