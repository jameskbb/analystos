/** Data-quality rule kinds, their parameters and client-side validation (spec §11). Pure. */
import type { QualityRuleKind } from "@/lib/api/types";

export interface RuleKindMeta {
  kind: QualityRuleKind;
  label: string;
  description: string;
  needsColumn: boolean;
}

export const RULE_KINDS: RuleKindMeta[] = [
  { kind: "not_null", label: "Not null", description: "The column has no missing values.", needsColumn: true },
  { kind: "unique", label: "Unique", description: "Every value appears once (no duplicate IDs).", needsColumn: true },
  { kind: "range", label: "Range (≥ / ≤)", description: "Values are at least a minimum and/or at most a maximum, e.g. amount ≥ 0.", needsColumn: true },
  { kind: "between", label: "Between", description: "Values fall inside [min, max], e.g. a percentage in [0, 1].", needsColumn: true },
  { kind: "fk_exists", label: "Foreign key exists", description: "Every value exists in a referenced table column.", needsColumn: true },
  { kind: "not_future", label: "Date not in future", description: "Dates are not later than today.", needsColumn: true },
  { kind: "allowed_values", label: "Allowed values", description: "Values belong to a fixed set.", needsColumn: true },
  { kind: "regex", label: "Pattern (regex)", description: "Values match a regular expression.", needsColumn: true },
  { kind: "custom_sql", label: "Custom SQL", description: "A read-only SELECT that returns the failing rows.", needsColumn: false },
];

export function ruleKindMeta(kind: string): RuleKindMeta | undefined {
  return RULE_KINDS.find((k) => k.kind === kind);
}

export interface RuleDraft {
  name: string;
  table_name: string;
  kind: QualityRuleKind;
  column: string;
  severity: "info" | "warning" | "error";
  description: string;
  /** Raw form strings; converted by buildRuleParams. */
  min: string;
  max: string;
  ref_table: string;
  ref_column: string;
  values: string;
  pattern: string;
  sql: string;
}

export function emptyDraft(table = "", kind: QualityRuleKind = "not_null"): RuleDraft {
  return {
    name: "",
    table_name: table,
    kind,
    column: "",
    severity: "warning",
    description: "",
    min: "",
    max: "",
    ref_table: "",
    ref_column: "",
    values: "",
    pattern: "",
    sql: "",
  };
}

const num = (s: string): number | null => {
  if (s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
};

/** Splits allowed values on newlines or commas, trimming blanks; keeps quoted commas out of scope. */
export function parseAllowedValues(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter((v, i, arr) => v.length > 0 && arr.indexOf(v) === i);
}

export type RuleErrors = Partial<Record<keyof RuleDraft, string>>;

export function validateRule(d: RuleDraft): RuleErrors {
  const errors: RuleErrors = {};
  const meta = ruleKindMeta(d.kind);
  if (!d.table_name) errors.table_name = "Choose a table.";
  if (meta?.needsColumn && !d.column) errors.column = "Choose a column.";
  const min = num(d.min);
  const max = num(d.max);
  switch (d.kind) {
    case "range":
      if (Number.isNaN(min)) errors.min = "Minimum must be a number.";
      if (Number.isNaN(max)) errors.max = "Maximum must be a number.";
      if (min === null && max === null) errors.min = "Set a minimum, a maximum or both.";
      if (min !== null && max !== null && !Number.isNaN(min) && !Number.isNaN(max) && min > max)
        errors.max = "Maximum must be ≥ minimum.";
      break;
    case "between":
      if (min === null || Number.isNaN(min)) errors.min = "Minimum is required and must be a number.";
      if (max === null || Number.isNaN(max)) errors.max = "Maximum is required and must be a number.";
      if (min !== null && max !== null && !Number.isNaN(min) && !Number.isNaN(max) && min > max)
        errors.max = "Maximum must be ≥ minimum.";
      break;
    case "fk_exists":
      if (!d.ref_table) errors.ref_table = "Choose the referenced table.";
      if (!d.ref_column) errors.ref_column = "Choose the referenced column.";
      if (d.ref_table && d.ref_table === d.table_name && d.ref_column === d.column)
        errors.ref_column = "A column cannot reference itself.";
      break;
    case "allowed_values":
      if (parseAllowedValues(d.values).length === 0) errors.values = "List at least one allowed value.";
      break;
    case "regex":
      if (!d.pattern) errors.pattern = "Enter a pattern.";
      else {
        try {
          new RegExp(d.pattern);
        } catch {
          errors.pattern = "Not a valid regular expression.";
        }
      }
      break;
    case "custom_sql": {
      const sql = d.sql.trim().replace(/^\(+/, "").toLowerCase();
      if (!sql) errors.sql = "Enter a SELECT that returns failing rows.";
      else if (!/^(select|with)\b/.test(sql)) errors.sql = "Only read-only SELECT / WITH queries are allowed.";
      else if (/;\s*\S/.test(d.sql.trim())) errors.sql = "Use a single statement.";
      break;
    }
  }
  return errors;
}

export function buildRuleParams(d: RuleDraft): Record<string, unknown> {
  switch (d.kind) {
    case "range": {
      const p: Record<string, unknown> = {};
      const min = num(d.min);
      const max = num(d.max);
      if (min !== null) p.min = min;
      if (max !== null) p.max = max;
      return p;
    }
    case "between":
      return { min: num(d.min), max: num(d.max) };
    case "fk_exists":
      return { ref_table: d.ref_table, ref_column: d.ref_column };
    case "allowed_values":
      return { values: parseAllowedValues(d.values) };
    case "regex":
      return { pattern: d.pattern };
    case "custom_sql":
      return { sql: d.sql.trim().replace(/;\s*$/, "") };
    default:
      return {};
  }
}

export function defaultRuleName(d: RuleDraft): string {
  const meta = ruleKindMeta(d.kind);
  const target = d.kind === "custom_sql" ? d.table_name : `${d.table_name}.${d.column}`;
  switch (d.kind) {
    case "range": {
      const parts = [d.min !== "" ? `≥ ${d.min}` : null, d.max !== "" ? `≤ ${d.max}` : null].filter(Boolean);
      return `${target} ${parts.join(" and ")}`.trim();
    }
    case "between":
      return `${target} between ${d.min} and ${d.max}`;
    case "fk_exists":
      return `${target} exists in ${d.ref_table}.${d.ref_column}`;
    default:
      return `${target}: ${meta?.label.toLowerCase() ?? d.kind}`;
  }
}

/** Human description of a stored rule's params for tables. */
export function describeRule(kind: string, column: string | null | undefined, params: Record<string, unknown>): string {
  const col = column ?? "";
  switch (kind) {
    case "not_null":
      return `${col} is not null`;
    case "unique":
      return `${col} is unique`;
    case "range": {
      const bits = [params.min !== undefined && params.min !== null ? `≥ ${params.min}` : null, params.max !== undefined && params.max !== null ? `≤ ${params.max}` : null].filter(Boolean);
      return `${col} ${bits.join(" and ")}`;
    }
    case "between":
      return `${col} between ${params.min} and ${params.max}`;
    case "fk_exists":
      return `${col} → ${params.ref_table}.${params.ref_column}`;
    case "not_future":
      return `${col} not in the future`;
    case "allowed_values": {
      const vs = Array.isArray(params.values) ? (params.values as unknown[]).map(String) : [];
      return `${col} in {${vs.slice(0, 5).join(", ")}${vs.length > 5 ? `, +${vs.length - 5}` : ""}}`;
    }
    case "regex":
      return `${col} matches /${String(params.pattern ?? "")}/`;
    case "custom_sql":
      return "Custom SQL";
    default:
      return `${kind}${col ? ` on ${col}` : ""}`;
  }
}
