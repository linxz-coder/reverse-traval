import datetime as dt

from holiday_helper import HolidayCalendar


class FakeHolidayCalendar(HolidayCalendar):
    def __init__(self, holiday_days=None):
        self.holiday_days = holiday_days or {}
        self.calls = []

    def _fetch_day(self, day: dt.date, max_retries: int = 30) -> dict:
        self.calls.append(day)
        name = self.holiday_days.get(day)
        if name:
            return {"status": 1, "is_workingday": 0, "info": name}
        return {"status": 1, "is_workingday": 1, "info": ""}


def test_2026_official_holidays_are_used_while_remaining():
    calendar = FakeHolidayCalendar()

    holidays = calendar.get_upcoming_holidays(today=dt.date(2026, 6, 22))

    assert [item.name for item in holidays] == ["中秋节", "国庆节"]
    assert holidays[0].start == dt.date(2026, 9, 25)
    assert holidays[-1].end == dt.date(2026, 10, 7)
    assert calendar.calls == []


def test_after_2026_official_holidays_end_api_fallback_finds_next_year():
    spring_festival_days = {
        dt.date(2027, 2, 15): "春节",
        dt.date(2027, 2, 16): "春节",
        dt.date(2027, 2, 17): "春节",
    }
    calendar = FakeHolidayCalendar(spring_festival_days)

    holidays = calendar.get_upcoming_holidays(days_ahead=160, today=dt.date(2026, 10, 8))

    assert holidays
    assert holidays[0].name == "春节"
    assert holidays[0].start == dt.date(2027, 2, 15)
    assert holidays[0].end == dt.date(2027, 2, 17)
    assert dt.date(2027, 2, 15) in calendar.calls
