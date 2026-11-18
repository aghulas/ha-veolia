"""Row parsing and the closed-day baseline (synthetic series)."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.veolia_eau.leak import closed_day_baseline, possible_leak
from custom_components.veolia_eau.models import (
    DailyReading,
    months_between,
    parse_daily,
    parse_monthly,
)


def row(day: str, litres: object, index: object = 100.0, code: object = "M") -> dict:
    return {
        "date_releve": day,
        "consommation": {"litre": litres},
        "index": {"m3": index},
        "fiabilite_index": code,
    }


def test_parse_daily_sorts_dedupes_and_tolerates_junk() -> None:
    rows = [
        row("2026-10-03", 300),
        row("2026-10-01", 5000, 99.5, "E"),
        row("2026-10-03", 320, code="X"),
        row("not-a-date", 1),
        {"date_releve": "2026-10-02T00:00:00", "consommation": None, "index": {"m3": "abc"}},
        row("2026-10-04", True, None, None),
    ]
    readings = parse_daily(rows)
    assert [r.day.isoformat() for r in readings] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
        "2026-10-04",
    ]
    assert readings[0] == DailyReading(date(2026, 10, 1), 5000.0, 99.5, "estimated")
    assert readings[1].litres is None and readings[1].index_m3 is None
    assert readings[2].litres == 320.0 and readings[2].reliability == "X"
    assert readings[3].litres is None and readings[3].reliability is None


def test_parse_monthly() -> None:
    rows = [
        {"annee": 2026, "mois": 2, "consommation": {"m3": 80}, "fiabilite_conso": "M"},
        {"annee": 2026, "mois": 1, "consommation": {"m3": 90.5}, "index": {"m3": 1200}},
        {"annee": 2026, "mois": 13},
        {"annee": "2026", "mois": 3},
    ]
    readings = parse_monthly(rows)
    assert [(r.year, r.month, r.m3) for r in readings] == [(2026, 1, 90.5), (2026, 2, 80.0)]
    assert readings[0].index_m3 == 1200.0 and readings[1].reliability == "measured"


def test_months_between() -> None:
    assert months_between(date(2025, 11, 20), date(2026, 2, 1)) == [
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]
    assert months_between(date(2026, 3, 1), date(2026, 2, 1)) == []


def series(sundays: list[float | None], weekday_litres: float = 5500.0) -> list[DailyReading]:
    """Weeks starting Monday 2026-09-07; `sundays` gives each week's Sunday volume."""
    out = []
    monday = date(2026, 9, 7)
    for week, sunday in enumerate(sundays):
        for d in range(7):
            day = monday + timedelta(days=7 * week + d)
            out.append(DailyReading(day, sunday if d == 6 else weekday_litres, None, "measured"))
    return out


def test_baseline_keeps_closed_days_with_a_volume() -> None:
    readings = series([40.0, None, 200.0])
    assert closed_day_baseline(readings) == [(date(2026, 9, 13), 40.0), (date(2026, 9, 27), 200.0)]
    holiday = date(2026, 9, 9)
    with_holiday = closed_day_baseline(readings, closed_weekdays={5, 6}, closed_dates={holiday})
    assert (holiday, 5500.0) in with_holiday and len(with_holiday) == 6


def test_two_sundays_above_150_is_a_possible_leak() -> None:
    assert possible_leak(closed_day_baseline(series([40.0, 185.0, 327.0])))


def test_no_leak_otherwise() -> None:
    assert not possible_leak(closed_day_baseline(series([185.0, 327.0, 80.0])))
    assert not possible_leak(closed_day_baseline(series([40.0, 80.0, 327.0])))
    assert not possible_leak(closed_day_baseline(series([150.0, 150.0])))  # strictly above
    assert not possible_leak(closed_day_baseline(series([327.0])))  # not enough closed days
    assert not possible_leak([])
    assert not possible_leak(closed_day_baseline(series([327.0, 327.0])), consecutive=0)


def test_custom_threshold_and_count() -> None:
    baseline = closed_day_baseline(series([120.0, 130.0, 140.0]))
    assert possible_leak(baseline, threshold_l=100.0, consecutive=3)
    assert not possible_leak(baseline, threshold_l=125.0, consecutive=3)
