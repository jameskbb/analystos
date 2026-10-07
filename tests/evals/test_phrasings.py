"""Phrasing corpus scorecard: how well the interpreter reads questions the way people ask them.

``phrasings.yaml`` was written blind to the interpreter (see its header). Every entry runs through
``interpret()`` on the demo model with today = 2026-09-30, and every asserted field is scored; an
entry passes when all of its asserted fields match. The dev split is what the interpreter is tuned
against; the holdout split is only run to report how well that tuning generalises.

Run the scorecard with ``make evals`` or
``uv run pytest tests/evals/test_phrasings.py -s -k scorecard``.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from .conftest import TODAY

CORPUS = Path(__file__).with_name("phrasings.yaml")

DEV_FLOOR = 0.936
"""Required dev pass rate (the achieved rate minus 3 points; a regression guard)."""
HOLDOUT_FLOOR = 0.634
"""Required holdout pass rate (the achieved rate minus 5 points; a regression guard)."""

_EXPECTATION = {"up": "increase", "down": "decrease"}


@dataclass
class EntryResult:
    id: str
    split: str
    story: str
    speaker: str
    question: str
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def _window_matches(want: Any, win: Any) -> tuple[bool, str]:
    """Corpus window notation: 2026-Q2 | 2026-08 | 2026 (year to date) | a date (snapshot) |
    2025-01..2026-09 (a range)."""
    if win is None:
        return False, "no window"
    got = f"{win.start.isoformat()}..{win.last_day.isoformat()}"
    if isinstance(want, dt.date):
        return win.last_day == want, got
    text = str(want)
    if ".." in text:
        a, b = text.split("..")
        start = dt.date.fromisoformat(f"{a}-01")
        end = _month_end(b)
        return win.start == start and win.last_day == end, got
    if "-Q" in text:
        year, q = int(text[:4]), int(text[-1])
        start = dt.date(year, 3 * (q - 1) + 1, 1)
        end = _month_end(f"{year}-{3 * q:02d}")
        return win.start == start and win.last_day == end, got
    if len(text) == 7:
        return win.start == dt.date.fromisoformat(f"{text}-01") and win.last_day == _month_end(text), got
    if len(text) == 4:  # a calendar year (to date when it is the current year)
        return win.start == dt.date(int(text), 1, 1) and win.last_day <= dt.date(int(text), 12, 31), got
    return False, got


def _month_end(ym: str) -> dt.date:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def score_entry(entry: dict[str, Any], model: Any, value_index: Any) -> EntryResult:
    from analystos_investigator import interpret

    res = EntryResult(entry["id"], entry["split"], entry["story"], entry["speaker"], entry["question"])
    exp = entry.get("expect") or {}
    try:
        i = interpret(entry["question"], model, dt.date.fromisoformat(TODAY), value_index=value_index)
    except Exception as exc:  # a crash is a failure, never a pass
        res.failures.append(f"crash: {type(exc).__name__}: {exc}")
        return res
    subject = i.primary_metric_id
    amb = {a.term: {c.metric_id for c in a.candidates} for a in i.ambiguous}
    fail = res.failures.append
    if "metric" in exp and (subject != exp["metric"] or i.ambiguous):
        fail(f"metric {subject} (ambiguous {amb}) != {exp['metric']}")
    if "metric_any_of" in exp and (subject not in exp["metric_any_of"] or i.ambiguous):
        fail(f"metric {subject} (ambiguous {amb}) not in {exp['metric_any_of']}")
    if "ambiguous_among" in exp:
        want = set(exp["ambiguous_among"])
        if not any(want <= cands for cands in amb.values()):
            fail(f"expected a question among {sorted(want)}, got metric {subject}, ambiguous {amb}")
    if exp.get("unresolved") and (i.metric_ids or i.ambiguous):
        fail(f"expected 'not governed', got metric {i.metric_ids}, ambiguous {amb}")
    if "comparison" in exp:
        want = exp["comparison"]
        ok = i.comparison_kind == want or (want == "none" and i.comparison_defaulted)
        if not ok:
            fail(
                f"comparison {i.comparison_kind}{' (defaulted)' if i.comparison_defaulted else ''} != {want}"
            )
    for key, win in (("window", i.window), ("baseline", i.baseline)):
        if key in exp:
            ok, got = _window_matches(exp[key], win)
            if not ok:
                fail(f"{key} {got} != {exp[key]}")
    if "baseline_metric" in exp and i.baseline_metric_id != exp["baseline_metric"]:
        fail(f"baseline_metric {i.baseline_metric_id} != {exp['baseline_metric']}")
    for p in exp.get("premises") or []:
        want_e = _EXPECTATION.get(p["expectation"], p["expectation"])
        if not any(q.metric_id == p["metric"] and q.expectation == want_e for q in i.premises):
            got = [(q.metric_id, q.expectation) for q in i.premises]
            fail(f"premise {p['metric']} {p['expectation']} missing (got {got})")
    for dim, values in (exp.get("filters") or {}).items():
        want_v = {str(v) for v in (values if isinstance(values, list) else [values])}
        got_v = {v for f in i.filters if f.dimension == dim and f.op in ("eq", "in") for v in f.values}
        if got_v != want_v:
            fail(f"filter {dim}={sorted(want_v)} (got {[(f.dimension, f.op, f.values) for f in i.filters]})")
    for text in exp.get("unresolved_filters") or []:
        if not any(
            text.lower() in u.text.lower() or u.text.lower() in text.lower() for u in i.unresolved_filters
        ):
            fail(f"unresolved filter '{text}' not surfaced (got {[u.text for u in i.unresolved_filters]})")
    for dim in exp.get("breakdown") or []:
        if dim not in i.breakdown_dimensions:
            fail(f"breakdown {dim} missing (got {i.breakdown_dimensions})")
    if "intent" in exp and i.intent != exp["intent"]:
        fail(f"intent {i.intent} != {exp['intent']}")
    return res


def load_corpus() -> list[dict[str, Any]]:
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8"))["entries"]


@pytest.fixture(scope="module")
def phrasing_results(model: Any, store: Any) -> list[EntryResult]:
    from analystos_investigator import build_value_index

    value_index = build_value_index(store, model)
    return [score_entry(e, model, value_index) for e in load_corpus()]


def _rate(rs: list[EntryResult]) -> float:
    return sum(r.passed for r in rs) / len(rs) if rs else 0.0


def format_phrasing_scorecard(results: list[EntryResult], *, show: str = "dev") -> str:
    lines = ["", "Phrasing corpus scorecard (interpreter only, today = 2026-09-30)", ""]
    for split in ("dev", "holdout"):
        rs = [r for r in results if r.split == split]
        lines.append(f"{split:8} {sum(r.passed for r in rs):3}/{len(rs):<3} {_rate(rs):6.1%}")
    for key in ("story", "speaker"):
        lines.append("")
        groups: dict[str, dict[str, list[EntryResult]]] = defaultdict(lambda: defaultdict(list))
        for r in results:
            groups[getattr(r, key)][r.split].append(r)
        for name in sorted(groups):
            parts = [
                f"{split} {sum(x.passed for x in groups[name][split])}/{len(groups[name][split])}"
                for split in ("dev", "holdout")
                if groups[name][split]
            ]
            lines.append(f"  {key} {name:32} " + "   ".join(parts))
    lines.append("")
    for r in results:
        if not r.passed and r.split == show:
            lines.append(f"  FAIL {r.id:10} {r.question}")
            lines.extend(f"         - {f}" for f in r.failures)
    return "\n".join(lines)


def test_phrasing_scorecard(phrasing_results: list[EntryResult]) -> None:
    """Prints the scorecard (run with -s); fails when a split drops below its regression floor."""
    print(format_phrasing_scorecard(phrasing_results))
    dev = [r for r in phrasing_results if r.split == "dev"]
    holdout = [r for r in phrasing_results if r.split == "holdout"]
    assert _rate(dev) >= DEV_FLOOR, f"dev pass rate {_rate(dev):.1%} below the floor {DEV_FLOOR:.0%}"
    assert _rate(holdout) >= HOLDOUT_FLOOR, (
        f"holdout pass rate {_rate(holdout):.1%} below the floor {HOLDOUT_FLOOR:.0%}"
    )
