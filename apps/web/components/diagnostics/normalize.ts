/** Shape-tolerant normalizers for diagnostics payloads (system info and per-category summaries). */
import type { DiagnosticsSummaryRow } from "@/lib/api/types";
import { humanize } from "@/lib/format";

export interface InfoSection {
  title: string;
  rows: { key: string; value: string }[];
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : String(Math.round(v * 1000) / 1000);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.every((x) => typeof x !== "object" || x === null) ? v.join(", ") : JSON.stringify(v);
  return JSON.stringify(v);
}

/** Top-level scalars become a "Server" section; each nested object becomes its own section. */
export function infoSections(info: Record<string, unknown> | null | undefined): InfoSection[] {
  if (!info) return [];
  const top: InfoSection = { title: "Server", rows: [] };
  const out: InfoSection[] = [];
  for (const [k, v] of Object.entries(info)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      out.push({
        title: humanize(k),
        rows: Object.entries(v as Record<string, unknown>).map(([kk, vv]) => ({ key: humanize(kk), value: show(vv) })),
      });
    } else {
      top.rows.push({ key: humanize(k), value: show(v) });
    }
  }
  return top.rows.length ? [top, ...out] : out;
}

/** Accepts `{categories: [...]}`, `{by_category: {cat: stats}}`, `[...]` or `{cat: stats}`. */
export function summaryRows(raw: unknown): DiagnosticsSummaryRow[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw as DiagnosticsSummaryRow[];
  const o = raw as Record<string, unknown>;
  for (const key of ["categories", "by_category", "items"]) {
    const v = o[key];
    if (Array.isArray(v)) return v as DiagnosticsSummaryRow[];
    if (v && typeof v === "object") return summaryRows(v);
  }
  return Object.entries(o)
    .filter(([, v]) => v && typeof v === "object" && !Array.isArray(v))
    .map(([category, v]) => ({ category, ...(v as Record<string, unknown>) }) as DiagnosticsSummaryRow);
}

export function errorRate(r: DiagnosticsSummaryRow): number | null {
  if (typeof r.error_rate === "number") return r.error_rate;
  if (typeof r.count === "number" && r.count > 0 && typeof r.errors === "number") return r.errors / r.count;
  return null;
}
