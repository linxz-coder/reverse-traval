import datetime as dt

from scripts import nightly_prewarm


def test_nightly_prewarm_city_batch_keeps_priority_first_without_duplicates():
    cities = nightly_prewarm.build_city_batch(
        day=dt.date(2026, 5, 9),
        batch_size=5,
        priority_cities=["深圳", "广州", "深圳"],
    )

    assert cities[:2] == ["深圳", "广州"]
    assert len(cities) == len(set(cities))
    assert len(cities) == 7


def test_nightly_prewarm_rotating_batch_allows_zero_size():
    assert nightly_prewarm.rotating_batch(("深圳", "广州"), 0, dt.date(2026, 5, 9)) == []


def test_nightly_prewarm_slot_offset_changes_rotating_batch():
    cities = ("深圳", "广州", "北京", "上海", "杭州", "苏州")

    first = nightly_prewarm.rotating_batch(cities, 2, dt.date(2026, 5, 9), slot_offset=0)
    second = nightly_prewarm.rotating_batch(cities, 2, dt.date(2026, 5, 9), slot_offset=1)

    assert first != second
    assert len(first) == 2
    assert len(second) == 2


def test_nightly_prewarm_allowed_hours_supports_ranges():
    assert nightly_prewarm.is_allowed_hour(dt.datetime(2026, 5, 9, 2, 0), "0-7,22-23")
    assert nightly_prewarm.is_allowed_hour(dt.datetime(2026, 5, 9, 22, 0), "0-7,22-23")
    assert not nightly_prewarm.is_allowed_hour(dt.datetime(2026, 5, 9, 14, 0), "0-7,22-23")
