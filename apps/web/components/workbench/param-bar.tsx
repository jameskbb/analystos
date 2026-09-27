"use client";
import * as React from "react";
import type { ParamType, QueryParameterDef } from "@/lib/api/resources/workbench";
import { cn } from "@/lib/utils";

const TYPES: { value: ParamType; label: string }[] = [
  { value: "string", label: "text" },
  { value: "number", label: "number" },
  { value: "integer", label: "integer" },
  { value: "date", label: "date" },
  { value: "boolean", label: "bool" },
  { value: "string_list", label: "list" },
];

/** Inputs for detected `$name` parameters: value, type and whether it is required. */
export function ParamBar({
  defs,
  values,
  errors,
  onValue,
  onType,
}: {
  defs: QueryParameterDef[];
  values: Record<string, string>;
  errors: Record<string, string>;
  onValue: (name: string, value: string) => void;
  onType: (name: string, type: ParamType) => void;
}) {
  if (!defs.length) return null;
  return (
    <div className="flex flex-wrap items-end gap-2 border-b border-border bg-bg-subtle px-2 py-1.5" aria-label="Query parameters" role="group">
      {defs.map((d) => {
        const id = `param-${d.name}`;
        const err = errors[d.name];
        return (
          <div key={d.name} className="flex flex-col gap-0.5">
            <label htmlFor={id} className="font-mono text-2xs text-fg-subtle">
              ${d.name}
            </label>
            <div className="flex items-center">
              {d.type === "boolean" ? (
                <select
                  id={id}
                  value={values[d.name] ?? ""}
                  onChange={(e) => onValue(d.name, e.target.value)}
                  className={cn(
                    "h-6 w-28 rounded-l border border-border-strong bg-bg px-1.5 text-xs",
                    err && "border-negative",
                  )}
                >
                  <option value="">(unset)</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  id={id}
                  type={d.type === "date" ? "date" : "text"}
                  inputMode={d.type === "number" || d.type === "integer" ? "decimal" : undefined}
                  value={values[d.name] ?? ""}
                  placeholder={d.type === "string_list" ? "a, b, c" : d.type}
                  onChange={(e) => onValue(d.name, e.target.value)}
                  aria-invalid={!!err}
                  aria-describedby={err ? `${id}-err` : undefined}
                  className={cn(
                    "h-6 w-36 rounded-l border border-border-strong bg-bg px-1.5 font-mono text-xs outline-none focus-visible:outline-2 focus-visible:outline-ring",
                    err && "border-negative",
                  )}
                />
              )}
              <select
                aria-label={`Type of ${d.name}`}
                value={d.type}
                onChange={(e) => onType(d.name, e.target.value as ParamType)}
                className="h-6 rounded-r border border-l-0 border-border-strong bg-bg-muted px-1 text-2xs text-fg-subtle"
              >
                {TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            {err ? (
              <span id={`${id}-err`} className="text-2xs text-negative">
                {err}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
