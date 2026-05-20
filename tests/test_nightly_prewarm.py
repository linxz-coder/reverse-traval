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
    assert len(cities) == 10
    assert len([city for city in cities if city in nightly_prewarm.INTERNATIONAL_CITIES]) == 3
    assert nightly_prewarm.CORE_CITIES[:4] == ("深圳", "广州", "东莞", "惠州")


def test_nightly_prewarm_rotating_batch_allows_zero_size():
    assert nightly_prewarm.rotating_batch(("深圳", "广州"), 0, dt.date(2026, 5, 9)) == []


def test_nightly_prewarm_start_window_skips_daytime():
    assert nightly_prewarm.within_start_window(dt.datetime(2026, 5, 20, 2, 10), 2, 6) is True
    assert nightly_prewarm.within_start_window(dt.datetime(2026, 5, 20, 15, 10), 2, 6) is False
    assert nightly_prewarm.within_any_start_window(dt.datetime(2026, 5, 20, 6, 30), [(2, 3), (6, 8)]) is True
    assert nightly_prewarm.within_any_start_window(dt.datetime(2026, 5, 20, 5, 30), [(2, 3), (6, 8)]) is False


def test_nightly_prewarm_random_international_batch_is_daily_stable():
    first = nightly_prewarm.random_international_batch(day=dt.date(2026, 5, 20), count=3)
    second = nightly_prewarm.random_international_batch(day=dt.date(2026, 5, 20), count=3)

    assert first == second
    assert len(first) == 3
    assert len(first) == len(set(first))


def test_nightly_prewarm_detects_today_completed_run():
    status = {"preset": "nightly", "run_date": "2026-05-20", "status": "succeeded"}

    assert nightly_prewarm.nightly_ran_for_day(status, dt.date(2026, 5, 20)) is True
    assert nightly_prewarm.nightly_ran_for_day(status, dt.date(2026, 5, 21)) is False
    assert nightly_prewarm.nightly_ran_for_day({"preset": "manual", "run_date": "2026-05-20"}, dt.date(2026, 5, 20)) is False
