"""Investigation templates (spec §63): reusable strategies, not schemas.

A template never names a table or column. It refers to metric *roles* (matched against
metric tags, ids and names) and dimension *roles* (matched against dimension names,
labels and synonyms). The planner turns a template plus the semantic model into concrete
steps, so the same template works on any workspace whose model carries the roles.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from analystos_engine.semantic.models import Dimension, Metric, SemanticModel

    from .models import Interpretation

DIMENSION_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "geography": (
        "region",
        "branch",
        "city",
        "state",
        "territory",
        "store",
        "market",
        "location",
        "country",
        "site",
        "warehouse",
        "district",
        "zone",
        "area",
    ),
    "customer": ("customer", "account", "client", "buyer", "company"),
    "customer_segment": ("segment", "industry", "tier_customer", "customer_type", "vertical"),
    "product": ("product", "category", "sku", "item", "brand", "subcategory", "line", "tier", "family"),
    "channel": ("channel", "source", "medium", "campaign", "platform"),
    "sales_team": ("rep", "salesperson", "seller", "owner", "team", "manager"),
    "status": ("status", "state_code", "stage"),
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def dimension_roles(dim: Dimension) -> list[str]:
    """Roles a dimension plays, from its name, label and synonyms (deterministic order)."""
    words = _tokens(dim.name) | _tokens(dim.label or "") | {w for s in dim.synonyms for w in _tokens(s)}
    roles = []
    for role, keys in DIMENSION_ROLE_KEYWORDS.items():
        if any(k in words or any(w.startswith(k) for w in words) for k in keys):
            roles.append(role)
    # "customer segment" is a segment, not an individual customer.
    if "customer_segment" in roles and "customer" in roles and ("segment" in words or "type" in words):
        roles.remove("customer")
    return roles


def metric_matches_role(metric: Metric, role: str) -> bool:
    role = role.lower()
    if role in (t.lower() for t in metric.tags):
        return True
    words = _tokens(metric.id) | _tokens(metric.name) | _tokens(metric.label or "")
    words |= {w for s in metric.synonyms for w in _tokens(s)}
    return role in words or any(w.startswith(role) for w in words if len(role) >= 4)


class TemplateCheck(BaseModel):
    """A related metric worth comparing across the same periods (e.g. discount rate)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    metric_roles: list[str]
    rationale: str
    hypothesis_category: str = "price_discount"
    prefer_rate: bool = False
    """Prefer a rate (ratio, percent) metric over an amount: discount *rate*, not dollars."""


class ConditionalHypothesis(BaseModel):
    """An idea that is only testable when the model has a metric for it.

    When a metric matches ``metric_roles`` the planner adds a check on it and the idea
    becomes a tested hypothesis; otherwise it stays untestable, saying which data is missing.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    metric_roles: list[str]
    missing: str


class InvestigationTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    metric_roles: list[str]
    intents: list[str] = Field(default_factory=lambda: ["why_change"])
    comparison_kinds: list[str] = Field(default_factory=list)
    dimension_roles: list[str]
    drill_roles: list[str] = Field(default_factory=list)
    checks: list[TemplateCheck] = Field(default_factory=list)
    untestable_hypotheses: list[str] = Field(default_factory=list)
    seasonality_check: bool = True
    drill_depth: int = 2
    max_dimensions: int = 6
    drill_group_roles: list[str] | None = None
    """Dimension roles whose breakdowns may be drilled into (None: any). Product tiers or
    categories explain revenue through mix and price (see the price driver breakdowns); drilling
    a whole tier by customer rarely adds anything."""
    explicit_only: bool = False
    """Chosen only when the interpretation names it (e.g. an inventory *health* question)."""
    dimension_priority: list[str] = Field(default_factory=list)
    """Keywords that order dimensions inside one role (coarse, low-cardinality levels first)."""
    conditional_hypotheses: list[ConditionalHypothesis] = Field(default_factory=list)

    def score(self, metric: Metric, interp: Interpretation) -> int:
        """Deterministic match score: role match dominates, intent and comparison refine."""
        s = 0
        if any(metric_matches_role(metric, r) for r in self.metric_roles):
            s += 10
        if interp.intent in self.intents:
            s += 3
        if self.comparison_kinds and interp.comparison_kind in self.comparison_kinds:
            s += 6
        return s

    def resolve_checks(self, model: SemanticModel, exclude: set[str]) -> list[tuple[TemplateCheck, Metric]]:
        out: list[tuple[TemplateCheck, Metric]] = []
        used: set[str] = set(exclude)
        for check in self.checks:

            def key(m: Metric, check: TemplateCheck = check) -> tuple[bool, bool, str]:
                is_rate = m.kind == "ratio" or m.format == "percent"
                return (not m.canonical, (not is_rate) if check.prefer_rate else False, m.id)

            plan_roles = {"plan", "budget", "forecast", "target"}
            for m in sorted(model.metrics, key=key):
                if m.id in used:
                    continue
                is_plan = bool(plan_roles & {t.lower() for t in m.tags})
                if is_plan and not plan_roles & {r.lower() for r in check.metric_roles}:
                    continue
                if any(metric_matches_role(m, r) for r in check.metric_roles):
                    out.append((check, m))
                    used.add(m.id)
                    break
        return out

    def resolve_conditional(
        self, model: SemanticModel, exclude: set[str]
    ) -> list[tuple[ConditionalHypothesis, Metric | None]]:
        """Each conditional hypothesis with the metric that can test it (or None)."""
        out: list[tuple[ConditionalHypothesis, Metric | None]] = []
        for h in self.conditional_hypotheses:
            found = None
            for m in sorted(model.metrics, key=lambda m: (not m.canonical, m.id)):
                if m.id not in exclude and any(metric_matches_role(m, r) for r in h.metric_roles):
                    found = m
                    break
            out.append((h, found))
        return out


_COMMON_UNTESTABLE = [
    "marketing or promotional activity changed demand",
    "competitor pricing or new competitors took share",
    "weather, macroeconomic or other external conditions affected demand",
]

TEMPLATES: dict[str, InvestigationTemplate] = {
    t.id: t
    for t in [
        InvestigationTemplate(
            id="revenue_decline",
            name="Revenue change",
            description="Decompose revenue into volume and price drivers, then locate the segments, "
            "customers and products behind the change.",
            metric_roles=["revenue", "sales", "bookings", "gmv", "net_sales"],
            dimension_roles=["geography", "customer_segment", "product", "channel", "customer", "sales_team"],
            drill_roles=["customer", "product", "channel"],
            max_dimensions=8,
            dimension_priority=["branch", "region", "category", "tier"],
            drill_group_roles=["geography", "customer_segment", "channel", "customer", "sales_team"],
            checks=[
                TemplateCheck(
                    title="Discounting",
                    metric_roles=["discount"],
                    rationale="Higher discounts lower realised price.",
                    prefer_rate=True,
                ),
                TemplateCheck(
                    title="Returns and cancellations",
                    metric_roles=["return", "cancel", "refund"],
                    rationale="Returns or cancellations reduce recognised revenue.",
                    hypothesis_category="cancellations_returns",
                ),
            ],
            untestable_hypotheses=list(_COMMON_UNTESTABLE),
        ),
        InvestigationTemplate(
            id="margin_variance",
            name="Margin variance",
            description="Split margin into revenue and cost effects, then find the products, customers "
            "and regions where margin moved.",
            metric_roles=["margin", "profit", "gross_margin", "contribution"],
            dimension_roles=["product", "customer_segment", "geography", "customer", "channel"],
            drill_roles=["product", "customer"],
            dimension_priority=["category", "tier", "region", "branch"],
            checks=[
                TemplateCheck(
                    title="Discounting",
                    metric_roles=["discount"],
                    rationale="Discounting compresses margin at constant cost.",
                    prefer_rate=True,
                ),
                TemplateCheck(
                    title="Revenue",
                    metric_roles=["revenue"],
                    rationale="Confirms whether revenue moved alongside margin.",
                    hypothesis_category="driver_decomposition",
                ),
            ],
            conditional_hypotheses=[
                ConditionalHypothesis(
                    text="freight or delivery costs changed",
                    metric_roles=["freight", "delivery"],
                    missing="no freight or delivery cost metric is defined",
                ),
            ],
            untestable_hypotheses=[
                "supplier price increases that are not yet in recorded costs (future cost pressure)",
            ],
        ),
        InvestigationTemplate(
            id="conversion_decline",
            name="Conversion decline",
            description="Separate lead volume from conversion rate and locate the channels, regions and "
            "reps where conversion fell.",
            metric_roles=["conversion", "win_rate", "close_rate", "conversion_rate"],
            dimension_roles=["channel", "geography", "sales_team", "customer_segment", "product"],
            drill_roles=["sales_team", "channel"],
            checks=[
                TemplateCheck(
                    title="Lead volume",
                    metric_roles=["lead", "leads", "opportunities", "opportunity"],
                    rationale="Distinguishes a volume problem from a conversion problem.",
                    hypothesis_category="driver_decomposition",
                ),
            ],
            untestable_hypotheses=[
                "lead quality from a campaign changed",
                "sales process or staffing changes slowed follow-up",
            ],
        ),
        InvestigationTemplate(
            id="churn",
            name="Customer churn",
            description="Measure active and lost customers, then find the segments and regions where "
            "customers stopped buying.",
            metric_roles=["churn", "retention", "active_customers", "customers", "customer_count"],
            dimension_roles=["customer_segment", "geography", "product", "channel", "sales_team"],
            drill_roles=["customer"],
            untestable_hypotheses=[
                "service quality issues drove customers away",
                "competitor offers attracted customers",
            ],
        ),
        InvestigationTemplate(
            id="inventory_spike",
            name="Inventory build-up",
            description="Locate the products and locations where inventory grew faster than sales.",
            metric_roles=["inventory", "stock", "on_hand", "days_of_supply"],
            intents=["why_change", "anomaly"],
            dimension_roles=["product", "geography"],
            drill_roles=["product"],
            checks=[
                TemplateCheck(
                    title="Unit sales",
                    metric_roles=["units", "quantity", "unit"],
                    rationale="Inventory that grows while sales do not points to slow movers.",
                    hypothesis_category="driver_decomposition",
                ),
            ],
            untestable_hypotheses=["purchasing placed large forward buys ahead of expected demand"],
        ),
        InvestigationTemplate(
            id="inventory_health",
            name="Inventory health",
            description="Rank products at the latest snapshot by days of supply and recent sales, flag slow "
            "movers, and measure how much of the inventory change they account for.",
            metric_roles=["inventory", "stock", "on_hand"],
            intents=["breakdown", "lookup"],
            dimension_roles=["product", "geography"],
            drill_roles=["product"],
            untestable_hypotheses=["purchasing placed large forward buys ahead of expected demand"],
            seasonality_check=False,
            explicit_only=True,
        ),
        InvestigationTemplate(
            id="forecast_miss",
            name="Forecast miss",
            description="Compare actuals with forecast and find the segments where the gap is concentrated.",
            metric_roles=["forecast"],
            comparison_kinds=["forecast", "budget"],
            intents=["why_change", "compare"],
            dimension_roles=["customer_segment", "product", "geography", "customer", "channel"],
            drill_roles=["customer", "product"],
            untestable_hypotheses=["the forecast assumptions were optimistic for reasons outside the data"],
            seasonality_check=False,
        ),
        InvestigationTemplate(
            id="regional_performance",
            name="Regional performance",
            description="Compare regions and branches and explain which locations drive the total.",
            metric_roles=["region", "branch", "territory"],
            intents=["compare", "breakdown"],
            dimension_roles=["geography", "customer_segment", "product", "channel", "customer"],
            drill_roles=["customer", "product"],
            untestable_hypotheses=list(_COMMON_UNTESTABLE[:1]),
            seasonality_check=False,
        ),
        InvestigationTemplate(
            id="general_change",
            name="General metric change",
            description="Decompose any metric by its metric tree and locate the segments behind the change.",
            metric_roles=[],
            intents=["why_change", "anomaly", "compare", "breakdown", "lookup", "trend"],
            dimension_roles=["geography", "customer_segment", "product", "channel", "customer", "sales_team"],
            drill_roles=["customer", "product"],
            untestable_hypotheses=list(_COMMON_UNTESTABLE[:2]),
        ),
    ]
}


def get_template(template_id: str) -> InvestigationTemplate:
    try:
        return TEMPLATES[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown template {template_id!r}; available: {sorted(TEMPLATES)}") from exc


def choose_template(interp: Interpretation, model: SemanticModel) -> InvestigationTemplate:
    """Highest-scoring template for the interpretation's primary metric (ties by id)."""
    if interp.template_id and interp.template_id in TEMPLATES:
        return TEMPLATES[interp.template_id]
    mid = interp.primary_metric_id
    if mid is None or not model.has_metric(mid):
        return TEMPLATES["general_change"]
    metric = model.get_metric(mid)
    geo_breakdown = any(
        "geography" in dimension_roles(model.get_dimension(d))
        for d in interp.breakdown_dimensions
        if model.has_dimension(d)
    )
    best = TEMPLATES["general_change"]
    best_score = 0
    for t in sorted(TEMPLATES.values(), key=lambda t: t.id):
        if t.id == "general_change" or t.explicit_only:
            continue
        s = t.score(metric, interp)
        if t.id == "regional_performance":
            s = s + 12 if geo_breakdown and interp.intent in ("compare", "breakdown") else 0
        if s >= 10 and s > best_score:
            best, best_score = t, s
    return best


def _priority(dim: Dimension, keywords: list[str]) -> int:
    words = _tokens(dim.name) | _tokens(dim.label or "")
    for i, k in enumerate(keywords):
        if k in words:
            return i
    return len(keywords)


def rank_dimensions(
    dims: list[Dimension], template: InvestigationTemplate, requested: list[str] | None = None
) -> list[Dimension]:
    """Requested dimensions first, then round-robin over the template's role priority (the
    first dimension of every role before the second of any). Inside a role, dimensions follow
    the template's ``dimension_priority`` keywords, then their name. Round-robin keeps one role
    (for example three geography columns) from crowding out the others."""
    requested = requested or []
    first = sorted((d for d in dims if d.name in requested), key=lambda d: requested.index(d.name))
    order = {r: i for i, r in enumerate(template.dimension_roles)}
    buckets: dict[int, list[Dimension]] = {}
    ordered = sorted(
        (d for d in dims if d.name not in requested),
        key=lambda d: (_priority(d, template.dimension_priority), d.name),
    )
    for d in ordered:
        roles = dimension_roles(d)
        pri = min((order[r] for r in roles if r in order), default=len(order) + 1)
        buckets.setdefault(pri, []).append(d)
    rest: list[Dimension] = []
    level = 0
    while any(len(b) > level for b in buckets.values()):
        for pri in sorted(buckets):
            if len(buckets[pri]) > level:
                rest.append(buckets[pri][level])
        level += 1
    return first + rest
