from __future__ import annotations

from analystos_investigator import FindingInput, InvestigationRun, executive_summary, set_node_status
from analystos_investigator.llm import ScriptedProvider


def _confirmed(run: InvestigationRun) -> InvestigationRun:
    inv = run.investigation
    tree = inv.tree
    root = tree.root()
    orders = next(n for n in tree.children_of(root.id) if n.metric_id == "orders")
    dallas = next(
        n
        for n in tree.nodes
        if n.segment and n.segment.value == "Dallas" and len(n.segment_path) == 1 and n.metric_id == "revenue"
    )
    hyp = next(n for n in tree.nodes if n.kind == "hypothesis")
    for nid in (root.id, orders.id, dallas.id, hyp.id):
        inv = set_node_status(inv, nid, "confirmed")
    return run.model_copy(update={"investigation": inv})


def test_summary_uses_confirmed_findings_only_in_three_sections(revenue_run: InvestigationRun) -> None:
    run = _confirmed(revenue_run)
    s = executive_summary(run.investigation.tree.nodes)
    assert len(s.observations) == 1 and s.observations[0].text.startswith("Revenue declined 10.0%")
    assert len(s.supported_explanations) == 2
    assert any("Orders declined 7.0%" in i.text for i in s.supported_explanations)
    assert any("Dallas" in i.text and "60%" in i.text for i in s.supported_explanations)
    assert all("(strong evidence)" in i.text for i in s.supported_explanations)
    assert len(s.hypotheses) == 1 and s.hypotheses[0].text.startswith("Hypothesis (not tested)")
    assert s.excluded_count == len(run.investigation.tree.nodes) - 4
    assert s.narrative_source == "template"
    md = s.to_markdown()
    assert (
        "### Observed facts" in md
        and "### Supported explanations" in md
        and "### Unresolved hypotheses" in md
    )


def test_unconfirmed_findings_never_appear(revenue_run: InvestigationRun) -> None:
    s = executive_summary(revenue_run.investigation.tree.nodes)
    assert not s.observations and not s.supported_explanations and not s.hypotheses
    assert s.narrative == "No confirmed findings yet."


def test_finding_inputs_from_the_api(revenue_run: InvestigationRun) -> None:
    items = [
        FindingInput(
            statement="Revenue declined 10.0%",
            statement_type="observation",
            status="Confirmed",
            evidence_strength="strong",
        ),
        FindingInput(
            statement="Marketing spend fell",
            statement_type="hypothesis",
            status="confirmed",
            evidence_strength="hypothesis_only",
        ),
        FindingInput(statement="Draft idea", statement_type="supported_explanation", status="draft"),
    ]
    s = executive_summary(items)
    assert s.observations[0].text == "Revenue declined 10.0% (strong evidence)."
    assert s.hypotheses[0].text == "Hypothesis (not tested): marketing spend fell."
    assert s.excluded_count == 1


def test_llm_narrative_accepted_when_every_number_is_traceable(revenue_run: InvestigationRun) -> None:
    run = _confirmed(revenue_run)
    llm = ScriptedProvider(
        {
            "synthesize_findings": {
                "narrative": "Revenue declined 10.0% from $1.00M to $900.0k. Fewer orders explain 69% of the change, and "
                "Dallas accounts for 60%. Marketing activity may also have played a role, but this is untested."
            }
        }
    )
    s = executive_summary(
        run.investigation.tree.nodes, llm=llm, artifacts=run.artifacts, nodes=run.investigation.tree.nodes
    )
    assert s.narrative_source == "llm_verified", s.notes


def test_llm_narrative_with_invented_number_is_rejected(revenue_run: InvestigationRun) -> None:
    run = _confirmed(revenue_run)
    llm = ScriptedProvider(
        {"synthesize_findings": {"narrative": "Revenue declined 14.2%, mostly in Dallas."}}
    )
    s = executive_summary(
        run.investigation.tree.nodes, llm=llm, artifacts=run.artifacts, nodes=run.investigation.tree.nodes
    )
    assert s.narrative_source == "template"
    assert any("14.2%" in n for n in s.notes)


def test_llm_narrative_stating_hypothesis_as_fact_is_rejected(revenue_run: InvestigationRun) -> None:
    run = _confirmed(revenue_run)
    hyp = run.investigation.tree
    text = next(n.statement for n in hyp.nodes if n.kind == "hypothesis" and n.status == "confirmed")
    topic = text.split(":", 1)[1].split(".")[0].strip()
    llm = ScriptedProvider({"synthesize_findings": {"narrative": f"Revenue declined 10.0% because {topic}."}})
    s = executive_summary(
        run.investigation.tree.nodes, llm=llm, artifacts=run.artifacts, nodes=run.investigation.tree.nodes
    )
    assert s.narrative_source == "template"
    assert any("hypothesis as fact" in n for n in s.notes)
