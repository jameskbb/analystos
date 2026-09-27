"use client";
import * as React from "react";
import { X } from "lucide-react";

/** Ordered multi-select as removable chips plus an "add" select. */
export function MultiPick({
  label,
  value,
  onChange,
  options,
  placeholder = "Add…",
}: {
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
  options: string[];
  placeholder?: string;
}) {
  const remaining = options.filter((o) => !value.includes(o));
  return (
    <div className="flex flex-col gap-1">
      {value.length ? (
        <ul className="flex flex-wrap gap-1" aria-label={label}>
          {value.map((v) => (
            <li key={v} className="inline-flex h-5 items-center gap-1 rounded-sm border border-border bg-bg-subtle pr-0.5 pl-1.5 font-mono text-xs">
              {v}
              <button
                type="button"
                onClick={() => onChange(value.filter((x) => x !== v))}
                className="rounded-sm p-0.5 text-fg-subtle hover:bg-bg-muted hover:text-fg"
                aria-label={`Remove ${v} from ${label}`}
              >
                <X className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <select
        aria-label={`Add to ${label}`}
        value=""
        disabled={!remaining.length}
        onChange={(e) => e.target.value && onChange([...value, e.target.value])}
        className="h-6 rounded border border-border-strong bg-bg px-1 text-xs text-fg-subtle disabled:opacity-50"
      >
        <option value="">{remaining.length ? placeholder : "No more fields"}</option>
        {remaining.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}

export function ConfigSection({ title, children, hint }: { title: string; children: React.ReactNode; hint?: string }) {
  return (
    <section className="border-b border-border px-3 py-2.5">
      <h3 className="mb-1.5 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{title}</h3>
      {children}
      {hint ? <p className="mt-1 text-2xs text-fg-faint">{hint}</p> : null}
    </section>
  );
}
