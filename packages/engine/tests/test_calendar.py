from __future__ import annotations

import datetime as dt

import pytest
from analystos_engine.calendar import (
    CalendarError,
    add_months,
    fiscal_quarter_of,
    fiscal_year_of,
    periods_between,
    previous_period,
    resolve_period,
)
from analystos_engine.semantic.models import CalendarConfig

TODAY = dt.date(2026, 9, 18)
D = dt.date


@pytest.mark.parametrize(
    ("text", "start", "end", "kind"),
    [
        ("August", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("aug", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("in August", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("October", D(2025, 10, 1), D(2025, 11, 1), "month"),
        ("September", D(2026, 9, 1), D(2026, 10, 1), "month"),
        ("Aug 2025", D(2025, 8, 1), D(2025, 9, 1), "month"),
        ("August 2026", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("aug '25", D(2025, 8, 1), D(2025, 9, 1), "month"),
        ("2026-08", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("08/2026", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("last month", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("this month", D(2026, 9, 1), D(2026, 10, 1), "month"),
        ("previous month", D(2026, 8, 1), D(2026, 9, 1), "month"),
        ("Q3", D(2026, 7, 1), D(2026, 10, 1), "quarter"),
        ("Q4", D(2025, 10, 1), D(2026, 1, 1), "quarter"),
        ("Q2 2025", D(2025, 4, 1), D(2025, 7, 1), "quarter"),
        ("2025-Q2", D(2025, 4, 1), D(2025, 7, 1), "quarter"),
        ("last quarter", D(2026, 4, 1), D(2026, 7, 1), "quarter"),
        ("this quarter", D(2026, 7, 1), D(2026, 10, 1), "quarter"),
        ("last year", D(2025, 1, 1), D(2026, 1, 1), "year"),
        ("2025", D(2025, 1, 1), D(2026, 1, 1), "year"),
        ("YTD", D(2026, 1, 1), D(2026, 9, 19), "ytd"),
        ("QTD", D(2026, 7, 1), D(2026, 9, 19), "qtd"),
        ("MTD", D(2026, 9, 1), D(2026, 9, 19), "mtd"),
        ("rolling 30 days", D(2026, 8, 20), D(2026, 9, 19), "rolling"),
        ("last 7 days", D(2026, 9, 12), D(2026, 9, 19), "rolling"),
        ("past 3 months", D(2026, 6, 19), D(2026, 9, 19), "rolling"),
        ("last 2 weeks", D(2026, 9, 5), D(2026, 9, 19), "rolling"),
        ("today", D(2026, 9, 18), D(2026, 9, 19), "day"),
        ("yesterday", D(2026, 9, 17), D(2026, 9, 18), "day"),
        ("last week", D(2026, 9, 7), D(2026, 9, 14), "week"),
        ("this week", D(2026, 9, 14), D(2026, 9, 21), "week"),
        ("2026-08-01 to 2026-08-15", D(2026, 8, 1), D(2026, 8, 16), "custom"),
        ("2026-08-12", D(2026, 8, 12), D(2026, 8, 13), "day"),
        ("FY2026", D(2026, 1, 1), D(2027, 1, 1), "fiscal_year"),
    ],
)
def test_resolve_period(text, start, end, kind):
    w = resolve_period(text, TODAY)
    assert (w.start, w.end, w.kind) == (start, end, kind)
    assert w.label


def test_fiscal_calendar_february_start():
    cfg = CalendarConfig(fiscal_year_start_month=2)
    assert fiscal_year_of(D(2026, 1, 31), cfg) == 2026
    assert fiscal_year_of(D(2026, 2, 1), cfg) == 2027
    assert fiscal_quarter_of(D(2026, 2, 1), cfg) == 1
    assert fiscal_quarter_of(D(2026, 9, 18), cfg) == 3
    w = resolve_period("FY2027", TODAY, cfg)
    assert (w.start, w.end) == (D(2026, 2, 1), D(2027, 2, 1))
    w = resolve_period("fiscal Q1 FY2027", TODAY, cfg)
    assert (w.start, w.end) == (D(2026, 2, 1), D(2026, 5, 1))
    w = resolve_period("this fiscal quarter", TODAY, cfg)
    assert (w.start, w.end) == (D(2026, 8, 1), D(2026, 11, 1))
    w = resolve_period("last fiscal quarter", TODAY, cfg)
    assert (w.start, w.end) == (D(2026, 5, 1), D(2026, 8, 1))
    w = resolve_period("FYTD", TODAY, cfg)
    assert (w.start, w.end) == (D(2026, 2, 1), D(2026, 9, 19))
    w = resolve_period("last fiscal year", TODAY, cfg)
    assert (w.start, w.end) == (D(2025, 2, 1), D(2026, 2, 1))


def test_fiscal_start_year_naming():
    cfg = CalendarConfig(fiscal_year_start_month=7, fiscal_year_naming="start_year")
    assert fiscal_year_of(D(2026, 8, 1), cfg) == 2026
    w = resolve_period("FY2026", TODAY, cfg)
    assert (w.start, w.end) == (D(2026, 7, 1), D(2027, 7, 1))


def test_sunday_weeks():
    cfg = CalendarConfig(week_start="sunday")
    w = resolve_period("this week", TODAY, cfg)
    assert w.start == D(2026, 9, 13)


@pytest.mark.parametrize(
    ("text", "kind", "start", "end"),
    [
        ("August", "pop", D(2026, 7, 1), D(2026, 8, 1)),
        ("August", "yoy", D(2025, 8, 1), D(2025, 9, 1)),
        ("Q3", "pop", D(2026, 4, 1), D(2026, 7, 1)),
        ("Q1 2026", "pop", D(2025, 10, 1), D(2026, 1, 1)),
        ("Q3", "yoy", D(2025, 7, 1), D(2025, 10, 1)),
        ("MTD", "pop", D(2026, 8, 1), D(2026, 8, 19)),
        ("YTD", "pop", D(2025, 1, 1), D(2025, 9, 19)),
        ("rolling 30 days", "pop", D(2026, 7, 21), D(2026, 8, 20)),
        ("last week", "pop", D(2026, 8, 31), D(2026, 9, 7)),
        ("last week", "yoy", D(2025, 9, 8), D(2025, 9, 15)),
        ("2025", "pop", D(2024, 1, 1), D(2025, 1, 1)),
        ("2026-08-01 to 2026-08-10", "pop", D(2026, 7, 22), D(2026, 8, 1)),
    ],
)
def test_previous_period(text, kind, start, end):
    w = previous_period(resolve_period(text, TODAY), kind)
    assert (w.start, w.end) == (start, end)
    assert w.days > 0


def test_month_labels():
    w = previous_period(resolve_period("August", TODAY))
    assert w.label == "July 2026"
    assert previous_period(resolve_period("August", TODAY), "yoy").label == "August 2025"


def test_mtd_pop_clamped_to_shorter_month():
    w = resolve_period("MTD", D(2026, 3, 31))
    p = previous_period(w)
    assert (p.start, p.end) == (D(2026, 2, 1), D(2026, 3, 1))


def test_leap_day_yoy():
    w = resolve_period("2028-02-29", TODAY)
    p = previous_period(w, "yoy")
    assert p.start == D(2027, 2, 28)


def test_add_months():
    assert add_months(D(2026, 1, 31), 1) == D(2026, 2, 28)
    assert add_months(D(2026, 1, 15), -13) == D(2024, 12, 15)


@pytest.mark.parametrize("bad", ["", "next tuesday-ish", "Q5", "2026-13", "fiscal Q9"])
def test_errors(bad):
    with pytest.raises(CalendarError):
        resolve_period(bad, TODAY)


def test_range_end_before_start():
    with pytest.raises(CalendarError):
        resolve_period("2026-08-15 to 2026-08-01", TODAY)


def test_bad_kind():
    with pytest.raises(CalendarError):
        previous_period(resolve_period("August", TODAY), "wow")  # type: ignore[arg-type]


def test_periods_between():
    assert periods_between(D(2026, 7, 15), D(2026, 9, 1), "month") == [D(2026, 7, 1), D(2026, 8, 1)]
    assert len(periods_between(D(2026, 8, 1), D(2026, 8, 8), "day")) == 7
    assert periods_between(D(2026, 1, 1), D(2027, 1, 1), "quarter")[-1] == D(2026, 10, 1)
    cfg = CalendarConfig(fiscal_year_start_month=2)
    assert periods_between(D(2026, 3, 1), D(2026, 9, 1), "fiscal_quarter", cfg) == [
        D(2026, 2, 1),
        D(2026, 5, 1),
        D(2026, 8, 1),
    ]
    assert periods_between(D(2026, 9, 1), D(2026, 9, 20), "week")[0] == D(2026, 8, 31)
    with pytest.raises(CalendarError):
        periods_between(D(2026, 1, 1), D(2026, 2, 1), "decade")
