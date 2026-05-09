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
