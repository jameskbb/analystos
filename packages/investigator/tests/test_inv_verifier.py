from __future__ import annotations

import datetime as dt

from analystos_investigator import Contribution, TreeNode
from analystos_investigator.llm import EvidenceNumbers, extract_claims, hypothesis_leaks, verify_text


def _root() -> TreeNode:
    return TreeNode(
        id="n1",
        parent_id=None,
        kind="root",
        statement="",
        statement_type="observation",
        metric_id="revenue",
        metric_label="Revenue",
        metric_format="currency",
        current=900_000.0,
        baseline=1_000_000.0,
        abs_change=-100_000.0,
        pct_change=-0.10,
        evidence_strength="strong",
    )


def _seg() -> TreeNode:
    return TreeNode(
        id="n2",
        parent_id="g",
        kind="segment",
        statement="",
        statement_type="supported_explanation",
        metric_id="revenue",
        metric_label="Revenue",
        current=240_000.0,
        baseline=300_000.0,
        abs_change=-60_000.0,
        pct_change=-0.2,
        evidence_strength="strong",
        contribution_to_parent=Contribution(effect=-60_000.0, share=0.5, method="additive"),
    )


def test_claim_extraction() -> None:
    claims = extract_claims("Revenue fell 11.8% to $4.2M, -$420k in Q3 for SKU123; orders 1,250 (+3.5 pp)")
    texts = [c.text for c in claims]
    assert "11.8%" in texts and "$4.2M" in texts and "1,250" in texts
    assert not any("123" in t for t in texts) and not any(t == "3" for t in texts)
    money = next(c for c in claims if c.text == "$4.2M")
    assert money.value == 4_200_000 and money.kind == "currency"
    neg = next(c for c in claims if "420" in c.text)
    assert neg.value == -420_000


def test_numbers_must_come_from_evidence() -> None:
    ev = EvidenceNumbers.from_run([], [_root(), _seg()], dates=[dt.date(2026, 8, 1)])
    assert verify_text("Revenue declined 10.0% to $900.0k in August 2026.", ev).ok
    assert verify_text("Revenue fell from $1.00M to $0.90M.", ev).ok
    assert verify_text("One segment explains 50% of the change.", ev).ok
    bad = verify_text("Revenue declined 12.5%.", ev)
    assert not bad.ok and "12.5%" in bad.rejected_texts
    assert not verify_text("Revenue was $950k.", ev).ok


def test_metric_movement_claims_are_bound_to_the_metric_and_direction() -> None:
    ev = EvidenceNumbers.from_run([], [_root(), _seg()])
    # 50% exists in the evidence (a segment share) but revenue did not rise 50%
    res = verify_text("Revenue is up 50% in August.", ev)
    assert not res.ok and any("up 50%" in t for t in res.rejected_texts)
    # right magnitude, wrong direction
    assert not verify_text("Revenue increased 10.0%.", ev).ok
    assert verify_text("Revenue decreased by 10.0% overall.", ev).ok
    assert verify_text("Revenue in Dallas fell 20%.", ev).ok


def test_hypothesis_leaks() -> None:
    hyps = [
        "Hypothesis (not tested): marketing or promotional activity changed demand. No data in the model."
    ]
    assert hypothesis_leaks("Revenue fell because marketing activity dropped.", hyps)
    assert not hypothesis_leaks("Revenue fell; marketing activity may have played a role (untested).", hyps)
    assert not hypothesis_leaks("Revenue fell 10%.", hyps)
