"""Number formatting for statements.

Statements are rendered from node values with these helpers only, so the numeric-claim
verifier (``llm.verifier``) can reproduce every rendered number from artifact values.
"""

from __future__ import annotations

import math


def _is_bad(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))


def fmt_value(value: float | None, fmt: str | None) -> str:
    if _is_bad(value):
        return "n/a"
    assert value is not None
    if fmt == "percent":
        return f"{value * 100:.1f}%"
    if fmt == "currency":
        return fmt_currency(value)
    if fmt == "integer" or (fmt in (None, "number") and float(value).is_integer()):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def fmt_currency(value: float) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.2f}M"
    if v >= 10_000:
        return f"{sign}${v / 1_000:.1f}k"
    if v >= 100:
        return f"{sign}${v:,.0f}"
    if v >= 0.005 or v == 0:
        return f"{sign}${v:,.2f}"
    return f"{sign}${v:.2g}"


def fmt_change(value: float | None, fmt: str | None) -> str:
    """Signed absolute change. Percent-format metrics change in percentage points."""
    if _is_bad(value):
        return "n/a"
    assert value is not None
    if fmt == "percent":
        return f"{value * 100:+.1f} pp"
    text = fmt_value(abs(value), fmt)
    return ("+" if value >= 0 else "-") + text


def fmt_pct(value: float | None) -> str:
    if _is_bad(value):
        return "n/a"
    assert value is not None
    return f"{value * 100:+.1f}%"


def fmt_share(value: float | None) -> str:
    if _is_bad(value):
        return "n/a"
    assert value is not None
    return f"{value * 100:.0f}%"


def direction_word(change: float | None, *, past: bool = True) -> str:
    if _is_bad(change) or change == 0:
        return "was unchanged" if past else "unchanged"
    assert change is not None
    if past:
        return "declined" if change < 0 else "increased"
    return "decline" if change < 0 else "increase"


def fmt_abs_pct(value: float | None) -> str:
    """Unsigned percentage for use after a direction word ("declined 10.0%")."""
    if _is_bad(value):
        return "n/a"
    assert value is not None
    return f"{abs(value) * 100:.1f}%"


def change_phrase(change: float | None, pct: float | None) -> str:
    """ "declined 10.0%" / "increased 3.2%" / "was unchanged"."""
    if _is_bad(change) or change == 0 or (pct is not None and abs(pct) < 0.0005):
        return "was unchanged"
    word = direction_word(change)
    return f"{word} {fmt_abs_pct(pct)}" if pct is not None else word
