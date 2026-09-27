"""Semantic model: entities, dimensions, metrics, relationships, metric trees,
glossary and business calendar configuration.

The semantic model is the single source of truth for business definitions.
It round-trips through YAML (``to_yaml`` / ``from_yaml``) and has a stable
content hash so investigations can record exactly which definitions they used.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "Entity",
    "Dimension",
    "Filter",
    "Metric",
    "Relationship",
    "DriverEdge",
    "MetricTree",
    "GlossaryTerm",
    "CalendarConfig",
    "SemanticModel",
    "ModelIssue",
    "SemanticModelError",
    "TIME_GRAINS",
    "FilterOp",
    "formula_metric_refs",
]

TIME_GRAINS = ("day", "week", "month", "quarter", "year", "fiscal_quarter", "fiscal_year")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

FilterOp = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "not_null",
    "contains",
    "starts_with",
]


class SemanticModelError(ValueError):
    """Raised for invalid or inconsistent semantic model definitions."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _check_ident(value: str, what: str) -> str:
    if not _IDENT_RE.match(value):
        raise ValueError(
            f"{what} {value!r} must be an identifier (letters, digits, underscore; not starting with a digit)"
        )
    return value


class Entity(_Model):
    """A business entity backed by a table, with a declared grain."""

    name: str
    table: str
    primary_key: str | list[str]
    grain_description: str = ""
    label: str | None = None
    description: str | None = None
    default_time_dimension: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_ident(v, "entity name")

    @property
    def key_columns(self) -> list[str]:
        return [self.primary_key] if isinstance(self.primary_key, str) else list(self.primary_key)


class Dimension(_Model):
    """A categorical, time or numeric attribute of an entity.

    ``expr`` is a SQL expression over the entity table's columns (unqualified),
    e.g. ``region`` or ``upper(trim(region))``.
    """

    name: str
    entity: str
    expr: str
    type: Literal["categorical", "time", "numeric"] = "categorical"
    time_grains: list[str] = Field(default_factory=list)
    label: str | None = None
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_ident(v, "dimension name")

    @field_validator("time_grains")
    @classmethod
    def _grains(cls, v: list[str]) -> list[str]:
        for g in v:
            if g not in TIME_GRAINS:
                raise ValueError(f"unknown time grain {g!r}; expected one of {TIME_GRAINS}")
        return v

    @model_validator(mode="after")
    def _time_defaults(self) -> Dimension:
        if self.type == "time" and not self.time_grains:
            self.time_grains = ["day", "week", "month", "quarter", "year"]
        return self


class Filter(_Model):
    """A predicate on a dimension. Values are always rendered as escaped literals."""

    dimension: str
    op: FilterOp = "eq"
    values: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _arity(self) -> Filter:
        n = len(self.values)
        if self.op in ("is_null", "not_null"):
            if n:
                raise ValueError(f"filter op {self.op} takes no values")
        elif self.op == "between":
            if n != 2:
                raise ValueError("filter op between takes exactly two values")
        elif self.op in ("in", "not_in"):
            if n == 0:
                raise ValueError(f"filter op {self.op} needs at least one value")
        elif n != 1:
            raise ValueError(f"filter op {self.op} takes exactly one value")
        return self

    def describe(self) -> str:
        symbols = {
            "eq": "=",
            "neq": "!=",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }
        if self.op in symbols:
            return f"{self.dimension} {symbols[self.op]} {self.values[0]}"
        if self.op in ("in", "not_in"):
            word = "in" if self.op == "in" else "not in"
            return f"{self.dimension} {word} ({', '.join(map(str, self.values))})"
        if self.op == "between":
            return f"{self.dimension} between {self.values[0]} and {self.values[1]}"
        if self.op == "is_null":
            return f"{self.dimension} is null"
        if self.op == "not_null":
            return f"{self.dimension} is not null"
        if self.op == "contains":
            return f"{self.dimension} contains {self.values[0]}"
        return f"{self.dimension} starts with {self.values[0]}"


class Metric(_Model):
    """A governed metric definition.

    * ``simple``: ``agg(expr)`` over rows of ``entity`` (optionally filtered).
    * ``ratio``: ``numerator / denominator`` where both are metric ids; always
      computed as a ratio of aggregates, never an average of row ratios.
    * ``derived``: ``formula`` over other metric ids, e.g. ``revenue - cogs``.
    """

    id: str
    name: str
    label: str | None = None
    description: str = ""
    kind: Literal["simple", "ratio", "derived"] = "simple"
    expr: str | None = None
    entity: str | None = None
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"] | None = None
    numerator: str | None = None
    denominator: str | None = None
    formula: str | None = None
    filters: list[Filter] = Field(default_factory=list)
    format: Literal["currency", "number", "percent", "integer"] = "number"
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    canonical: bool = True
    synonyms: list[str] = Field(default_factory=list)
    default_time_dimension: str | None = None
    higher_is_better: bool = True
    time_aggregation: Literal["sum", "last", "first", "avg"] = "sum"
    """How values combine over time. ``sum``: a flow (revenue, orders), summed over the window.
    ``last``/``first``: a balance (inventory, headcount), taken at the latest/earliest date of the
    metric's time dimension inside each output time bucket. ``avg``: the average over those dates
    of the per-date total. Semi-additive metrics are never summed across dates."""

    maturity_days: int | None = Field(default=None, ge=0)
    """Cohort metrics (conversion by lead creation date) keep changing after the period ends.
    A period is immature until ``maturity_days`` after its end; queries with an ``as_of`` date
    flag immature values so they are not compared with mature ones as if final."""

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return _check_ident(v, "metric id")

    @model_validator(mode="after")
    def _shape(self) -> Metric:
        if self.kind == "simple":
            if not self.entity or not self.agg:
                raise ValueError(f"simple metric {self.id!r} needs entity and agg")
            if self.expr is None:
                if self.agg != "count":
                    raise ValueError(f"simple metric {self.id!r} needs expr (only count may omit it)")
                self.expr = "*"
        elif self.kind == "ratio":
            if not self.numerator or not self.denominator:
                raise ValueError(f"ratio metric {self.id!r} needs numerator and denominator metric ids")
        elif self.kind == "derived" and not self.formula:
            raise ValueError(f"derived metric {self.id!r} needs a formula")
        if self.time_aggregation != "sum":
            if self.kind != "simple":
                raise ValueError(
                    f"metric {self.id!r}: time_aggregation applies to simple metrics; "
                    f"{self.kind} metrics inherit it from the metrics they combine"
                )
            if self.time_aggregation == "avg" and self.agg not in ("sum", "count", "count_distinct"):
                raise ValueError(
                    f"metric {self.id!r}: time_aggregation 'avg' needs agg sum, count or count_distinct"
                )
        return self

    @property
    def semi_additive(self) -> bool:
        """True when the metric must not be summed across dates (a balance or snapshot)."""
        return self.time_aggregation != "sum"

    @property
    def display_name(self) -> str:
        return self.label or self.name

    def definition_hash(self) -> str:
        payload = self.model_dump(
            mode="json", exclude={"owner", "tags", "synonyms", "description", "label", "canonical"}
        )
        if payload.get("time_aggregation") == "sum":
            # The default keeps the hash of definitions written before the field existed.
            payload.pop("time_aggregation")
        if payload.get("maturity_days") is None:
            payload.pop("maturity_days", None)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]

    @property
    def version_id(self) -> str:
        """Stable identifier of this exact definition: ``{id}@v{version}:{hash}``."""
        return f"{self.id}@v{self.version}:{self.definition_hash()}"


class Relationship(_Model):
    """A join between two entities. ``cardinality`` reads from -> to.

    Only ``approved`` relationships are ever used by the compiler.
    """

    from_entity: str
    from_col: str
    to_entity: str
    to_col: str
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"
    approved: bool = False
    name: str | None = None
    notes: str | None = None

    @property
    def id(self) -> str:
        return self.name or f"{self.from_entity}.{self.from_col}->{self.to_entity}.{self.to_col}"


class DriverEdge(_Model):
    parent: str
    child: str
    relation: Literal["additive", "multiplicative", "ratio_numerator", "ratio_denominator", "subtractive"]
    approved: bool = True
    suggested: bool = False
    notes: str | None = None


class MetricTree(_Model):
    root_metric: str
    nodes: list[DriverEdge] = Field(default_factory=list)
    name: str | None = None
    description: str | None = None

    def children(self, parent: str, *, approved_only: bool = True) -> list[DriverEdge]:
        return [e for e in self.nodes if e.parent == parent and (e.approved or not approved_only)]

    def metric_ids(self, *, approved_only: bool = True) -> list[str]:
        seen: list[str] = [self.root_metric]
        for e in self.nodes:
            if approved_only and not e.approved:
                continue
            for m in (e.parent, e.child):
                if m not in seen:
                    seen.append(m)
        return seen


class GlossaryTerm(_Model):
    term: str
    definition: str
    formula: str | None = None
    metric_id: str | None = None
    related: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    candidate_metric_ids: list[str] = Field(default_factory=list)


class CalendarConfig(_Model):
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    week_start: Literal["monday", "sunday"] = "monday"
    fiscal_year_naming: Literal["end_year", "start_year"] = "end_year"


class ModelIssue(_Model):
    severity: Literal["error", "warning"]
    object: str
    message: str


class SemanticModel(_Model):
    name: str = "default"
    version: int = 1
    entities: list[Entity] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    metric_trees: list[MetricTree] = Field(default_factory=list)
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)

    # ------------------------------------------------------------------ lookup
    def get_entity(self, name: str) -> Entity:
        for e in self.entities:
            if e.name == name:
                return e
        raise SemanticModelError(f"unknown entity {name!r}")

    def get_dimension(self, name: str) -> Dimension:
        for d in self.dimensions:
            if d.name == name:
                return d
        raise SemanticModelError(f"unknown dimension {name!r}")

    def get_metric(self, metric_id: str) -> Metric:
        for m in self.metrics:
            if m.id == metric_id:
                return m
        raise SemanticModelError(f"unknown metric {metric_id!r}")

    def has_metric(self, metric_id: str) -> bool:
        return any(m.id == metric_id for m in self.metrics)

    def has_dimension(self, name: str) -> bool:
        return any(d.name == name for d in self.dimensions)

    def get_tree(self, root_metric: str) -> MetricTree | None:
        for t in self.metric_trees:
            if t.root_metric == root_metric:
                return t
        return None

    def dimensions_for_entity(self, entity: str) -> list[Dimension]:
        return [d for d in self.dimensions if d.entity == entity]

    def approved_relationships(self) -> list[Relationship]:
        return [r for r in self.relationships if r.approved]

    def find_metrics(self, text: str) -> list[Metric]:
        """Metrics whose id, name, label or synonyms match ``text`` (case-insensitive)."""
        needle = _norm(text)
        out = []
        for m in self.metrics:
            names = {_norm(m.id), _norm(m.name)} | {_norm(s) for s in m.synonyms}
            if m.label:
                names.add(_norm(m.label))
            if needle in names:
                out.append(m)
        return out

    def find_dimensions(self, text: str) -> list[Dimension]:
        needle = _norm(text)
        out = []
        for d in self.dimensions:
            names = {_norm(d.name)} | {_norm(s) for s in d.synonyms}
            if d.label:
                names.add(_norm(d.label))
            if needle in names:
                out.append(d)
        return out

    def find_glossary(self, text: str) -> list[GlossaryTerm]:
        needle = _norm(text)
        return [g for g in self.glossary if needle in {_norm(g.term), *(_norm(s) for s in g.synonyms)}]

    def metric_versions(self, metric_ids: list[str]) -> dict[str, str]:
        """Version ids of the given metrics and every metric they depend on."""
        out: dict[str, str] = {}
        for mid in metric_ids:
            for dep in self.metric_dependencies(mid):
                out[dep] = self.get_metric(dep).version_id
        return out

    def metric_dependencies(self, metric_id: str, _stack: tuple[str, ...] = ()) -> list[str]:
        """``metric_id`` plus all metrics it depends on (depth first, no duplicates)."""
        if metric_id in _stack:
            raise SemanticModelError(f"circular metric definition: {' -> '.join((*_stack, metric_id))}")
        m = self.get_metric(metric_id)
        deps = [metric_id]
        children: list[str] = []
        if m.kind == "ratio":
            children = [m.numerator or "", m.denominator or ""]
        elif m.kind == "derived":
            children = formula_metric_refs(m.formula or "", self)
        for c in children:
            for d in self.metric_dependencies(c, (*_stack, metric_id)):
                if d not in deps:
                    deps.append(d)
        return deps

    # -------------------------------------------------------------- validation
    def validate_model(self) -> list[ModelIssue]:
        """Check referential integrity; returns issues (errors and warnings)."""
        issues: list[ModelIssue] = []
        entity_names = [e.name for e in self.entities]
        for dup in {n for n in entity_names if entity_names.count(n) > 1}:
            issues.append(
                ModelIssue(severity="error", object=f"entity:{dup}", message="duplicate entity name")
            )
        dim_names = [d.name for d in self.dimensions]
        for dup in {n for n in dim_names if dim_names.count(n) > 1}:
            issues.append(
                ModelIssue(severity="error", object=f"dimension:{dup}", message="duplicate dimension name")
            )
        metric_ids = [m.id for m in self.metrics]
        for dup in {n for n in metric_ids if metric_ids.count(n) > 1}:
            issues.append(ModelIssue(severity="error", object=f"metric:{dup}", message="duplicate metric id"))
        ents = set(entity_names)
        for d in self.dimensions:
            if d.entity not in ents:
                issues.append(
                    ModelIssue(
                        severity="error", object=f"dimension:{d.name}", message=f"unknown entity {d.entity!r}"
                    )
                )
        for m in self.metrics:
            obj = f"metric:{m.id}"
            if m.kind == "simple" and m.entity not in ents:
                issues.append(
                    ModelIssue(severity="error", object=obj, message=f"unknown entity {m.entity!r}")
                )
            for ref in [m.numerator, m.denominator] if m.kind == "ratio" else []:
                if ref and ref not in metric_ids:
                    issues.append(ModelIssue(severity="error", object=obj, message=f"unknown metric {ref!r}"))
            if m.kind == "derived":
                try:
                    refs = formula_metric_refs(m.formula or "", self)
                    if not refs:
                        issues.append(
                            ModelIssue(severity="error", object=obj, message="formula references no metrics")
                        )
                except SemanticModelError as exc:
                    issues.append(ModelIssue(severity="error", object=obj, message=str(exc)))
            for f in m.filters:
                if f.dimension not in dim_names:
                    issues.append(
                        ModelIssue(
                            severity="error",
                            object=obj,
                            message=f"filter on unknown dimension {f.dimension!r}",
                        )
                    )
            if m.default_time_dimension and m.default_time_dimension not in dim_names:
                issues.append(
                    ModelIssue(
                        severity="error",
                        object=obj,
                        message=f"unknown time dimension {m.default_time_dimension!r}",
                    )
                )
            try:
                self.metric_dependencies(m.id)
            except SemanticModelError as exc:
                issues.append(ModelIssue(severity="error", object=obj, message=str(exc)))
        for r in self.relationships:
            obj = f"relationship:{r.id}"
            for e in (r.from_entity, r.to_entity):
                if e not in ents:
                    issues.append(ModelIssue(severity="error", object=obj, message=f"unknown entity {e!r}"))
            if r.cardinality == "many_to_many" and r.approved:
                issues.append(
                    ModelIssue(
                        severity="warning",
                        object=obj,
                        message="many-to-many relationships are never used for metric joins; model a bridge entity",
                    )
                )
        for t in self.metric_trees:
            obj = f"metric_tree:{t.root_metric}"
            for mid in [t.root_metric, *[x for e in t.nodes for x in (e.parent, e.child)]]:
                if mid not in metric_ids:
                    issues.append(ModelIssue(severity="error", object=obj, message=f"unknown metric {mid!r}"))
        names_lower: dict[str, list[str]] = {}
        for m in self.metrics:
            for n in {_norm(m.name), *(_norm(s) for s in m.synonyms)}:
                names_lower.setdefault(n, []).append(m.id)
        for n, ids in names_lower.items():
            if len(ids) > 1:
                issues.append(
                    ModelIssue(
                        severity="warning",
                        object=f"metric_name:{n}",
                        message=f"name/synonym {n!r} is shared by metrics {sorted(ids)}; references will be ambiguous",
                    )
                )
        return issues

    def raise_for_errors(self) -> None:
        errors = [i for i in self.validate_model() if i.severity == "error"]
        if errors:
            raise SemanticModelError("; ".join(f"{i.object}: {i.message}" for i in errors))

    # ------------------------------------------------------------ persistence
    def to_yaml(self) -> str:
        data = self.model_dump(mode="json", exclude_defaults=False)
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, text: str) -> SemanticModel:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise SemanticModelError("semantic model YAML must be a mapping at the top level")
        return cls.model_validate(data)

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON form; changes whenever any definition changes."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.strip().lower())


def formula_metric_refs(formula: str, model: SemanticModel) -> list[str]:
    """Metric ids referenced by a derived-metric formula (in order of appearance)."""
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(formula, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise SemanticModelError(f"cannot parse formula {formula!r}: {exc}") from exc
    refs: list[str] = []
    for col in tree.find_all(exp.Column):
        name = col.name
        if col.table:
            raise SemanticModelError(f"formula {formula!r} must reference metric ids, not qualified columns")
        if not model.has_metric(name):
            raise SemanticModelError(f"formula {formula!r} references unknown metric {name!r}")
        if name not in refs:
            refs.append(name)
    if any(isinstance(n, exp.AggFunc) for n in tree.walk()):
        raise SemanticModelError(f"formula {formula!r} must combine metrics, not aggregate columns")
    return refs
