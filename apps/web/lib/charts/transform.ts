/** Pure data transforms used by chart rendering: series pivoting, histogram bins, box stats, waterfall bridge. */
import type { QueryColumn } from "@/lib/api/types";
import { toNumber } from "@/lib/format";

export function colIndex(columns: QueryColumn[], name: string | null | undefined): number {
  if (!name) return -1;
  return columns.findIndex((c) => c.name === name);
}

export function categoryLabel(v: unknown): string {
  if (v === null || v === undefined) return "(null)";
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T00:00:00(\.0+)?Z?$/.test(v)) return v.slice(0, 10);
  return String(v);
}

/** Unique x categories in first-seen order (or sorted when temporal-looking). */
export function uniqueCategories(rows: unknown[][], idx: number): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of rows) {
    const k = categoryLabel(r[idx]);
    if (!seen.has(k)) {
      seen.add(k);
      out.push(k);
    }
  }
  const temporal = out.length > 1 && out.every((s) => /^\d{4}-\d{2}/.test(s));
  return temporal ? out.sort() : out;
}

/**
 * Long → wide: one series per distinct `series` value (or per y column), aligned to categories.
 * Sums duplicates so pre-aggregated and raw results both render correctly.
 */
export function toSeries(
  columns: QueryColumn[],
  rows: unknown[][],
  x: string,
  y: string[],
  series?: string | null,
  maxSeries = 12,
): { categories: string[]; series: { name: string; data: (number | null)[] }[]; truncatedSeries: number } {
  const xi = colIndex(columns, x);
  const categories = xi >= 0 ? uniqueCategories(rows, xi) : rows.map((_, i) => String(i + 1));
  const catIndex = new Map(categories.map((c, i) => [c, i]));
  const si = colIndex(columns, series);
  if (si >= 0 && y.length) {
    const yi = colIndex(columns, y[0]);
    const totals = new Map<string, number>();
    const buckets = new Map<string, (number | null)[]>();
    for (const r of rows) {
      const s = categoryLabel(r[si]);
      const v = toNumber(r[yi]);
      const c = xi >= 0 ? catIndex.get(categoryLabel(r[xi])) : undefined;
      if (c === undefined) continue;
      let arr = buckets.get(s);
      if (!arr) {
        arr = new Array(categories.length).fill(null);
        buckets.set(s, arr);
      }
      if (v !== null) {
        arr[c] = (arr[c] ?? 0) + v;
        totals.set(s, (totals.get(s) ?? 0) + Math.abs(v));
      }
    }
    const ordered = [...buckets.keys()].sort((a, b) => (totals.get(b) ?? 0) - (totals.get(a) ?? 0));
    const kept = ordered.slice(0, maxSeries);
    const out = kept.map((name) => ({ name, data: buckets.get(name)! }));
    if (ordered.length > maxSeries) {
      const other = new Array<number | null>(categories.length).fill(null);
      for (const name of ordered.slice(maxSeries)) {
        buckets.get(name)!.forEach((v, i) => {
          if (v !== null) other[i] = (other[i] ?? 0) + v;
        });
      }
      out.push({ name: "Other", data: other });
    }
    return { categories, series: out, truncatedSeries: Math.max(ordered.length - maxSeries, 0) };
  }
  const out = y.map((name) => {
    const yi = colIndex(columns, name);
    const data = new Array<number | null>(categories.length).fill(null);
    rows.forEach((r, ri) => {
      const c = xi >= 0 ? catIndex.get(categoryLabel(r[xi])) : ri;
      if (c === undefined) return;
      const v = toNumber(r[yi]);
      if (v !== null) data[c] = (data[c] ?? 0) + v;
    });
    return { name, data };
  });
  return { categories, series: out, truncatedSeries: 0 };
}

/** Freedman–Diaconis bins (bounded to [5, 50]). */
export function histogram(values: number[], maxBins = 50): { edges: number[]; counts: number[] } {
  const vs = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!vs.length) return { edges: [], counts: [] };
  const min = vs[0];
  const max = vs[vs.length - 1];
  if (min === max) return { edges: [min, max], counts: [vs.length] };
  const iqr = quantile(vs, 0.75) - quantile(vs, 0.25);
  const width = iqr > 0 ? (2 * iqr) / Math.cbrt(vs.length) : (max - min) / Math.ceil(Math.sqrt(vs.length));
  const bins = Math.max(5, Math.min(maxBins, Math.ceil((max - min) / width)));
  const step = (max - min) / bins;
  const edges = Array.from({ length: bins + 1 }, (_, i) => min + i * step);
  const counts = new Array(bins).fill(0);
  for (const v of vs) {
    const b = Math.min(Math.floor((v - min) / step), bins - 1);
    counts[b]++;
  }
  return { edges, counts };
}

/** Linear-interpolated quantile on a sorted array (same as numpy's default). */
export function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export interface BoxStats {
  category: string;
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  outliers: number[];
  n: number;
}

/** Tukey box stats (whiskers at 1.5×IQR) per category. */
export function boxStats(rows: unknown[][], catIdx: number, valIdx: number): BoxStats[] {
  const groups = new Map<string, number[]>();
  for (const r of rows) {
    const v = toNumber(r[valIdx]);
    if (v === null) continue;
    const k = catIdx >= 0 ? categoryLabel(r[catIdx]) : "All";
    const g = groups.get(k);
    if (g) g.push(v);
    else groups.set(k, [v]);
  }
  return [...groups.entries()].map(([category, vals]) => {
    const s = vals.sort((a, b) => a - b);
    const q1 = quantile(s, 0.25);
    const q3 = quantile(s, 0.75);
    const iqr = q3 - q1;
    const lo = q1 - 1.5 * iqr;
    const hi = q3 + 1.5 * iqr;
    const inside = s.filter((v) => v >= lo && v <= hi);
    return {
      category,
      min: inside.length ? inside[0] : s[0],
      q1,
      median: quantile(s, 0.5),
      q3,
      max: inside.length ? inside[inside.length - 1] : s[s.length - 1],
      outliers: s.filter((v) => v < lo || v > hi),
      n: s.length,
    };
  });
}

export interface WaterfallStep {
  label: string;
  value: number;
  base: number;
  kind: "start" | "delta" | "end";
}

/**
 * Bridge from a start value through signed deltas to an end value.
 * Rows labelled like totals ("start", "baseline", "total", "end", "current") are treated as anchors.
 */
export function waterfallSteps(labels: string[], values: (number | null)[]): WaterfallStep[] {
  const steps: WaterfallStep[] = [];
  let running = 0;
  const anchor = /^(start|baseline|previous|prior|opening|begin|total|end|current|closing|final)\b/i;
  labels.forEach((label, i) => {
    const v = values[i] ?? 0;
    const isAnchor = anchor.test(label.trim());
    if (isAnchor && (i === 0 || i === labels.length - 1)) {
      steps.push({ label, value: v, base: 0, kind: i === 0 ? "start" : "end" });
      running = v;
    } else {
      const base = v >= 0 ? running : running + v;
      steps.push({ label, value: v, base, kind: "delta" });
      running += v;
    }
  });
  const hasEnd = steps.some((s) => s.kind === "end");
  if (!hasEnd && steps.length > 1) {
    steps.push({ label: "Net", value: running, base: 0, kind: "end" });
  }
  return steps;
}

/** Heatmap matrix from long rows. */
export function heatmapCells(
  rows: unknown[][],
  xi: number,
  yi: number,
  vi: number,
): { xs: string[]; ys: string[]; cells: [number, number, number][]; min: number; max: number } {
  const xs = uniqueCategories(rows, xi);
  const ys = uniqueCategories(rows, yi);
  const xm = new Map(xs.map((x, i) => [x, i]));
  const ym = new Map(ys.map((y, i) => [y, i]));
  const acc = new Map<string, number>();
  for (const r of rows) {
    const v = toNumber(r[vi]);
    if (v === null) continue;
    const key = `${xm.get(categoryLabel(r[xi]))}|${ym.get(categoryLabel(r[yi]))}`;
    acc.set(key, (acc.get(key) ?? 0) + v);
  }
  let min = Infinity;
  let max = -Infinity;
  const cells: [number, number, number][] = [];
  acc.forEach((v, k) => {
    const [x, y] = k.split("|").map(Number);
    cells.push([x, y, v]);
    min = Math.min(min, v);
    max = Math.max(max, v);
  });
  return { xs, ys, cells, min: Number.isFinite(min) ? min : 0, max: Number.isFinite(max) ? max : 0 };
}

export function sortRows(
  columns: QueryColumn[],
  rows: unknown[][],
  sort: { field: string; direction: "asc" | "desc" } | null | undefined,
): unknown[][] {
  if (!sort) return rows;
  const i = colIndex(columns, sort.field);
  if (i < 0) return rows;
  const dir = sort.direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = toNumber(a[i]);
    const y = toNumber(b[i]);
    if (x !== null && y !== null) return (x - y) * dir;
    return String(a[i] ?? "").localeCompare(String(b[i] ?? "")) * dir;
  });
}
