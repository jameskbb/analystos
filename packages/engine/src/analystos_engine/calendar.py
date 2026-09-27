"""Business calendar: resolve natural period expressions to half-open windows.

All windows are ``[start, end)`` (end exclusive). Fiscal years are named by the
calendar year in which they *end* by default (``fiscal_year_naming="end_year"``):
with a February fiscal start, FY2027 runs 2026-02-01 to 2027-01-31.

Supported expressions (case-insensitive):

* ``today``, ``yesterday``
* ``this|last|previous|prior week|month|quarter|year``
* ``August``, ``Aug 2026``, ``August 2026``, ``2026-08``, ``08/2026``
* ``Q3``, ``Q3 2026``, ``2026 Q3``, ``2026-Q3``
* ``FY2026``, ``FY26``, ``fiscal Q1``, ``FQ1 2026``, ``fiscal Q1 FY2026``, ``this fiscal year``, ``last fiscal quarter``
* ``2026``
* ``YTD``, ``QTD``, ``MTD``, ``FYTD``
* ``rolling 30 days``, ``last 30 days``, ``past 7 days``, ``trailing 90 days``,
  ``last 3 months``, ``last 12 weeks`` (N units ending today, inclusive)
* ``2026-08-01 to 2026-08-15`` (inclusive of both dates)
"""

from __future__ import annotations

import calendar as _cal
import datetime as dt
import re
from typing import Literal

from .semantic.models import CalendarConfig
from .types import TimeWindow

__all__ = [
    "CalendarError",
    "resolve_period",
    "previous_period",
    "fiscal_year_of",
    "fiscal_quarter_of",
    "fiscal_year_start",
    "add_months",
    "month_window",
    "quarter_window",
    "year_window",
    "periods_between",
    "TimeWindow",
]


class CalendarError(ValueError):
    """The period expression could not be resolved."""


_MONTHS = {name.lower(): i for i, name in enumerate(_cal.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(_cal.month_abbr) if name})
_MONTHS["sept"] = 9


def add_months(d: dt.date, months: int) -> dt.date:
    """Shift ``d`` by ``months`` calendar months, clamping the day to month end."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, _cal.monthrange(year, month)[1])
    return dt.date(year, month, day)


def month_window(year: int, month: int) -> TimeWindow:
    start = dt.date(year, month, 1)
    return TimeWindow(start=start, end=add_months(start, 1), kind="month", label=start.strftime("%B %Y"))


def quarter_window(year: int, quarter: int) -> TimeWindow:
    if not 1 <= quarter <= 4:
        raise CalendarError(f"quarter must be 1-4, got {quarter}")
    start = dt.date(year, 3 * (quarter - 1) + 1, 1)
    return TimeWindow(start=start, end=add_months(start, 3), kind="quarter", label=f"Q{quarter} {year}")


def year_window(year: int) -> TimeWindow:
    return TimeWindow(start=dt.date(year, 1, 1), end=dt.date(year + 1, 1, 1), kind="year", label=str(year))


def fiscal_year_of(d: dt.date, cfg: CalendarConfig) -> int:
    """Fiscal year label containing ``d``."""
    fs = cfg.fiscal_year_start_month
    if fs == 1:
        return d.year
    start_year = d.year if d.month >= fs else d.year - 1
    return start_year + 1 if cfg.fiscal_year_naming == "end_year" else start_year


def fiscal_year_start(fy: int, cfg: CalendarConfig) -> dt.date:
    fs = cfg.fiscal_year_start_month
    if fs == 1:
        return dt.date(fy, 1, 1)
    start_year = fy - 1 if cfg.fiscal_year_naming == "end_year" else fy
    return dt.date(start_year, fs, 1)


def fiscal_quarter_of(d: dt.date, cfg: CalendarConfig) -> int:
    fs = cfg.fiscal_year_start_month
    offset = (d.month - fs) % 12
    return offset // 3 + 1


def _fiscal_year_window(fy: int, cfg: CalendarConfig) -> TimeWindow:
    start = fiscal_year_start(fy, cfg)
    label = f"FY{fy}" if cfg.fiscal_year_start_month != 1 else f"FY{fy}"
    return TimeWindow(start=start, end=add_months(start, 12), kind="fiscal_year", label=label)


def _fiscal_quarter_window(fy: int, q: int, cfg: CalendarConfig) -> TimeWindow:
    if not 1 <= q <= 4:
        raise CalendarError(f"fiscal quarter must be 1-4, got {q}")
    start = add_months(fiscal_year_start(fy, cfg), 3 * (q - 1))
    return TimeWindow(start=start, end=add_months(start, 3), kind="fiscal_quarter", label=f"FQ{q} FY{fy}")


def _week_start(d: dt.date, cfg: CalendarConfig) -> dt.date:
    if cfg.week_start == "monday":
        return d - dt.timedelta(days=d.weekday())
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def _week_window(start: dt.date) -> TimeWindow:
    return TimeWindow(
        start=start, end=start + dt.timedelta(days=7), kind="week", label=f"Week of {start.isoformat()}"
    )


def _parse_year(text: str) -> int:
    y = int(text)
    if y < 100:
        y += 2000
    return y


def _most_recent_month(month: int, today: dt.date) -> int:
    """Year of the most recent occurrence of ``month`` that has started by ``today``."""
    return today.year if month <= today.month else today.year - 1


def _parse_date(text: str) -> dt.date:
    text = text.strip()
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CalendarError(f"cannot parse date {text!r}")


def resolve_period(text: str, today: dt.date | None = None, cfg: CalendarConfig | None = None) -> TimeWindow:
    """Resolve a period expression relative to ``today`` into a :class:`TimeWindow`."""
    today = today or dt.date.today()
    cfg = cfg or CalendarConfig()
    raw = text
    t = re.sub(r"\s+", " ", text.strip().lower())
    t = re.sub(r"^(in|for|during|over|the)\s+", "", t)
    t = re.sub(r"^(the)\s+", "", t)
    if not t:
        raise CalendarError("empty period expression")

    if t == "today":
        return TimeWindow(start=today, end=today + dt.timedelta(days=1), kind="day", label=today.isoformat())
    if t == "yesterday":
        yday = today - dt.timedelta(days=1)
        return TimeWindow(start=yday, end=today, kind="day", label=yday.isoformat())

    # explicit date range
    body = re.sub(r"^(from|between) ", "", t)
    for sep in (" to ", " through ", " until ", " and ", " - ", " – "):
        if sep not in body:
            continue
        left, right = body.split(sep, 1)
        try:
            a, b = _parse_date(left), _parse_date(right)
        except CalendarError:
            continue
        if b < a:
            raise CalendarError(f"range end {b} is before start {a}")
        return TimeWindow(
            start=a,
            end=b + dt.timedelta(days=1),
            kind="custom",
            label=f"{a.isoformat()} to {b.isoformat()}",
        )

    # to-date windows
    if t in ("ytd", "year to date", "this year to date"):
        start = dt.date(today.year, 1, 1)
        return TimeWindow(
            start=start, end=today + dt.timedelta(days=1), kind="ytd", label=f"YTD {today.year}"
        )
    if t in ("fytd", "fiscal ytd", "fiscal year to date"):
        fy = fiscal_year_of(today, cfg)
        start = fiscal_year_start(fy, cfg)
        return TimeWindow(start=start, end=today + dt.timedelta(days=1), kind="fytd", label=f"FYTD FY{fy}")
    if t in ("qtd", "quarter to date"):
        start = dt.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
        return TimeWindow(
            start=start,
            end=today + dt.timedelta(days=1),
            kind="qtd",
            label=f"QTD Q{(today.month - 1) // 3 + 1} {today.year}",
        )
    if t in ("mtd", "month to date"):
        start = today.replace(day=1)
        return TimeWindow(
            start=start, end=today + dt.timedelta(days=1), kind="mtd", label=f"MTD {start.strftime('%B %Y')}"
        )

    # rolling windows: "rolling 30 days", "last 30 days", "past 3 months"
    m = re.fullmatch(
        r"(?:rolling|last|past|trailing|previous|prior) (\d+) (day|week|month|quarter|year)s?", t
    )
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            raise CalendarError("rolling window length must be positive")
        end = today + dt.timedelta(days=1)
        if unit == "day":
            start = end - dt.timedelta(days=n)
        elif unit == "week":
            start = end - dt.timedelta(days=7 * n)
        else:
            months = n * {"month": 1, "quarter": 3, "year": 12}[unit]
            start = add_months(end, -months)
        return TimeWindow(
            start=start, end=end, kind="rolling", label=f"Rolling {n} {unit}{'s' if n != 1 else ''}"
        )

    # relative named periods
    m = re.fullmatch(r"(this|current|last|previous|prior) (fiscal )?(week|month|quarter|year)", t)
    if m:
        rel, fiscal, unit = m.group(1), bool(m.group(2)), m.group(3)
        back = rel in ("last", "previous", "prior")
        if unit == "week":
            start = _week_start(today, cfg) - dt.timedelta(days=7 if back else 0)
            return _week_window(start)
        if unit == "month":
            start = add_months(today.replace(day=1), -1 if back else 0)
            w = month_window(start.year, start.month)
            return w.model_copy(update={"kind": "fiscal_month"}) if fiscal else w
        if unit == "quarter":
            if fiscal:
                fy, q = fiscal_year_of(today, cfg), fiscal_quarter_of(today, cfg)
                if back:
                    q -= 1
                    if q == 0:
                        fy, q = fy - 1, 4
                return _fiscal_quarter_window(fy, q, cfg)
            q = (today.month - 1) // 3 + 1
            y = today.year
            if back:
                q -= 1
                if q == 0:
                    y, q = y - 1, 4
            return quarter_window(y, q)
        if fiscal:
            fy = fiscal_year_of(today, cfg) - (1 if back else 0)
            return _fiscal_year_window(fy, cfg)
        return year_window(today.year - (1 if back else 0))

    # fiscal quarter: "fiscal q1", "fq1 2026", "fiscal q1 fy2026", "q1 fy26"
    m = re.fullmatch(r"(?:fiscal q|fq|q)([1-4])(?: (?:fy)?'?(\d{2}|\d{4}))?", t)
    if m and (t.startswith(("fiscal", "fq")) or "fy" in t):
        q = int(m.group(1))
        fy = _parse_year(m.group(2)) if m.group(2) else fiscal_year_of(today, cfg)
        if not m.group(2):
            w = _fiscal_quarter_window(fy, q, cfg)
            if w.start > today:
                w = _fiscal_quarter_window(fy - 1, q, cfg)
            return w
        return _fiscal_quarter_window(fy, q, cfg)
    m = re.fullmatch(r"(?:fiscal q|fq|q)([1-4]) fy'?(\d{2}|\d{4})", t)
    if m:
        return _fiscal_quarter_window(_parse_year(m.group(2)), int(m.group(1)), cfg)

    # fiscal year: "fy2026", "fy 26", "fiscal 2026", "fiscal year 2026"
    m = re.fullmatch(r"(?:fy|fiscal year|fiscal) ?'?(\d{2}|\d{4})", t)
    if m:
        return _fiscal_year_window(_parse_year(m.group(1)), cfg)
    if t in ("fiscal year", "this fiscal year", "current fiscal year"):
        return _fiscal_year_window(fiscal_year_of(today, cfg), cfg)

    # calendar quarter: "q3", "q3 2026", "2026 q3", "2026-q3"
    m = re.fullmatch(r"q([1-4])(?:[ -]'?(\d{2}|\d{4}))?", t) or None
    if m:
        q = int(m.group(1))
        if m.group(2):
            return quarter_window(_parse_year(m.group(2)), q)
        y = today.year
        w = quarter_window(y, q)
        if w.start > today:
            w = quarter_window(y - 1, q)
        return w
    m = re.fullmatch(r"(\d{4})[ -]?q([1-4])", t)
    if m:
        return quarter_window(int(m.group(1)), int(m.group(2)))

    # year-month: "2026-08", "2026/08", "08/2026"
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise CalendarError(f"invalid month in {raw!r}")
        return month_window(y, mo)
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise CalendarError(f"invalid month in {raw!r}")
        return month_window(y, mo)

    # month names: "august", "aug 2026", "august of 2026", "aug '26"
    m = re.fullmatch(r"([a-z]+)\.?(?:,? (?:of )?'?(\d{2}|\d{4}))?", t)
    if m and m.group(1) in _MONTHS:
        mo = _MONTHS[m.group(1)]
        y = _parse_year(m.group(2)) if m.group(2) else _most_recent_month(mo, today)
        return month_window(y, mo)

    # year: "2026", "calendar 2026"
    m = re.fullmatch(r"(?:calendar (?:year )?|cy ?)?(\d{4})", t)
    if m:
        return year_window(int(m.group(1)))

    # single ISO date
    try:
        d = _parse_date(t)
    except CalendarError:
        pass
    else:
        return TimeWindow(start=d, end=d + dt.timedelta(days=1), kind="day", label=d.isoformat())

    raise CalendarError(f"could not resolve period {raw!r}")


def _shift_years(d: dt.date, years: int) -> dt.date:
    return add_months(d, 12 * years)


def previous_period(w: TimeWindow, kind: Literal["pop", "yoy"] = "pop") -> TimeWindow:
    """The comparison window for ``w``.

    * ``pop`` (period over period): the immediately preceding period of the same kind
      (August -> July; Q3 -> Q2; week -> previous week; MTD Sep 1-18 -> Aug 1-18;
      a rolling or custom window shifts back by its own length).
    * ``yoy``: the same period one year earlier (weeks shift by 52 weeks so weekdays align).
    """
    if kind not in ("pop", "yoy"):
        raise CalendarError(f"unknown comparison kind {kind!r}")
    label = None
    if kind == "yoy":
        if w.kind == "week":
            delta = dt.timedelta(weeks=52)
            start, end = w.start - delta, w.end - delta
        else:
            start, end = _shift_years(w.start, -1), _shift_years(w.end, -1)
        label = _yoy_label(w, start, end)
        return w.model_copy(update={"start": start, "end": end, "label": label})

    months = {"month": 1, "fiscal_month": 1, "quarter": 3, "fiscal_quarter": 3, "year": 12, "fiscal_year": 12}
    if w.kind in months:
        n = months[w.kind]
        start, end = add_months(w.start, -n), add_months(w.end, -n)
    elif w.kind == "mtd":
        start, end = add_months(w.start, -1), _clamped_end(add_months(w.start, -1), w.days, 1)
    elif w.kind == "qtd":
        start, end = add_months(w.start, -3), _clamped_end(add_months(w.start, -3), w.days, 3)
    elif w.kind in ("ytd", "fytd"):
        start, end = _shift_years(w.start, -1), _shift_years(w.end, -1)
    elif w.kind == "rolling" and _is_month_aligned(w) or _is_month_aligned(w):
        n = _month_span(w)
        start, end = add_months(w.start, -n), add_months(w.end, -n)
    else:
        length = w.end - w.start
        start, end = w.start - length, w.start
    return w.model_copy(update={"start": start, "end": end, "label": _pop_label(w, start, end)})


def _clamped_end(period_start: dt.date, days: int, period_months: int) -> dt.date:
    period_end = add_months(period_start, period_months)
    return min(period_start + dt.timedelta(days=days), period_end)


def _is_month_aligned(w: TimeWindow) -> bool:
    return w.start.day == 1 and w.end.day == 1 and w.end > w.start


def _month_span(w: TimeWindow) -> int:
    return (w.end.year - w.start.year) * 12 + (w.end.month - w.start.month)


def _pop_label(w: TimeWindow, start: dt.date, end: dt.date) -> str:
    probe = TimeWindow(start=start, end=end, kind=w.kind)
    if w.kind == "month":
        return start.strftime("%B %Y")
    if w.kind == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if w.kind == "year":
        return str(start.year)
    if w.kind == "week":
        return f"Week of {start.isoformat()}"
    if w.kind == "day":
        return start.isoformat()
    return (
        probe.display()
        if probe.label
        else f"{start.isoformat()} to {(end - dt.timedelta(days=1)).isoformat()}"
    )


def _yoy_label(w: TimeWindow, start: dt.date, end: dt.date) -> str:
    if w.kind == "month":
        return start.strftime("%B %Y")
    if w.kind == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if w.kind == "year":
        return str(start.year)
    if w.label and re.search(r"(19|20)\d{2}", w.label) and w.kind not in ("custom", "rolling", "day", "week"):
        return re.sub(r"(19|20)(\d{2})", lambda m: str(int(m.group(0)) - 1), w.label)
    return f"{start.isoformat()} to {(end - dt.timedelta(days=1)).isoformat()}"


def periods_between(
    start: dt.date, end: dt.date, grain: str, cfg: CalendarConfig | None = None
) -> list[dt.date]:
    """Start dates of every ``grain`` period intersecting ``[start, end)``."""
    cfg = cfg or CalendarConfig()
    out: list[dt.date] = []
    if grain == "day":
        d = start
        while d < end:
            out.append(d)
            d += dt.timedelta(days=1)
        return out
    if grain == "week":
        d = _week_start(start, cfg)
        while d < end:
            out.append(d)
            d += dt.timedelta(days=7)
        return out
    step = {"month": 1, "quarter": 3, "year": 12, "fiscal_quarter": 3, "fiscal_year": 12}.get(grain)
    if step is None:
        raise CalendarError(f"unknown grain {grain!r}")
    if grain == "month":
        d = start.replace(day=1)
    elif grain == "quarter":
        d = dt.date(start.year, 3 * ((start.month - 1) // 3) + 1, 1)
    elif grain == "year":
        d = dt.date(start.year, 1, 1)
    elif grain == "fiscal_year":
        d = fiscal_year_start(fiscal_year_of(start, cfg), cfg)
    else:
        fy_start = fiscal_year_start(fiscal_year_of(start, cfg), cfg)
        d = add_months(fy_start, 3 * (fiscal_quarter_of(start, cfg) - 1))
    while d < end:
        out.append(d)
        d = add_months(d, step)
    return out
