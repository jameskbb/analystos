"use client";
import * as React from "react";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { isNumericType } from "@/lib/format";

export type FilterOp =
  | "eq"
  | "neq"
  | "in"
  | "not_in"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "between"
  | "is_null"
  | "not_null"
  | "contains"
  | "starts_with";

export const FILTER_OPS: { value: FilterOp; label: string }[] = [
  { value: "eq", label: "=" },
  { value: "neq", label: "≠" },
  { value: "in", label: "in" },
  { value: "not_in", label: "not in" },
  { value: "gt", label: ">" },
  { value: "gte", label: "≥" },
  { value: "lt", label: "<" },
  { value: "lte", label: "≤" },
  { value: "between", label: "between" },
  { value: "contains", label: "contains" },
  { value: "starts_with", label: "starts with" },
  { value: "is_null", label: "is empty" },
  { value: "not_null", label: "is not empty" },
];

/** Editable filter row; `text` holds raw input ("a, b" for lists, "x..y" never used: between has two inputs). */
export interface FilterDraft {
  field: string;
  op: FilterOp;
  text: string;
  text2?: string;
}

export interface FieldOption {
  name: string;
  type?: string;
}

function coerce(v: string, type?: string): unknown {
  const t = v.trim();
  if (type && isNumericType(type) && t !== "" && Number.isFinite(Number(t))) return Number(t);
  return t;
}

/** Converts a draft to API `values`, or null when incomplete. */
export function filterValues(f: FilterDraft, type?: string): unknown[] | null {
  if (!f.field) return null;
  if (f.op === "is_null" || f.op === "not_null") return [];
  if (f.op === "between") {
    if (!f.text.trim() || !(f.text2 ?? "").trim()) return null;
    return [coerce(f.text, type), coerce(f.text2 ?? "", type)];
  }
  if (f.op === "in" || f.op === "not_in") {
    const vs = f.text
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => coerce(s, type));
    return vs.length ? vs : null;
  }
  if (!f.text.trim()) return null;
  return [coerce(f.text, type)];
}

export function FilterEditor({
  filters,
  onChange,
  fields,
  fieldLabel = "Field",
}: {
  filters: FilterDraft[];
  onChange: (f: FilterDraft[]) => void;
  fields: FieldOption[];
  fieldLabel?: string;
}) {
  const set = (i: number, patch: Partial<FilterDraft>) => onChange(filters.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <div className="flex flex-col gap-1.5">
      {filters.map((f, i) => {
        const incomplete = f.field && filterValues(f, fields.find((x) => x.name === f.field)?.type) === null;
        return (
          <div key={i} className="flex flex-col gap-1 rounded border border-border p-1.5" role="group" aria-label={`Filter ${i + 1}`}>
            <div className="flex items-center gap-1">
              <select
                aria-label={`${fieldLabel} for filter ${i + 1}`}
                value={f.field}
                onChange={(e) => set(i, { field: e.target.value })}
                className="h-6 min-w-0 flex-1 rounded border border-border-strong bg-bg px-1 text-xs"
              >
                <option value="">{fieldLabel}…</option>
                {fields.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={`Operator for filter ${i + 1}`}
                value={f.op}
                onChange={(e) => set(i, { op: e.target.value as FilterOp })}
                className="h-6 w-24 rounded border border-border-strong bg-bg px-1 text-xs"
              >
                {FILTER_OPS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <Button variant="ghost" size="icon-xs" aria-label={`Remove filter ${i + 1}`} onClick={() => onChange(filters.filter((_, j) => j !== i))}>
                <X />
              </Button>
            </div>
            {f.op === "is_null" || f.op === "not_null" ? null : (
              <div className="flex items-center gap-1">
                <input
                  aria-label={`Value for filter ${i + 1}`}
                  value={f.text}
                  onChange={(e) => set(i, { text: e.target.value })}
                  placeholder={f.op === "in" || f.op === "not_in" ? "Dallas, Houston" : f.op === "between" ? "from" : "value"}
                  className="h-6 min-w-0 flex-1 rounded border border-border-strong bg-bg px-1.5 text-xs"
                />
                {f.op === "between" ? (
                  <input
                    aria-label={`Upper value for filter ${i + 1}`}
                    value={f.text2 ?? ""}
                    onChange={(e) => set(i, { text2: e.target.value })}
                    placeholder="to"
                    className="h-6 min-w-0 flex-1 rounded border border-border-strong bg-bg px-1.5 text-xs"
                  />
                ) : null}
              </div>
            )}
            {incomplete ? <span className="text-2xs text-warning">Incomplete: ignored until a value is set.</span> : null}
          </div>
        );
      })}
      <Button size="xs" variant="ghost" className="self-start" onClick={() => onChange([...filters, { field: "", op: "eq", text: "" }])}>
        <Plus /> Add filter
      </Button>
    </div>
  );
}
