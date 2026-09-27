/**
 * Pivot table computation (spec §33): rows, columns, values, filters, subtotals, grand totals, sorting.
 *
 * Two input modes:
 *  - "raw": each record is a source row; aggregates are computed exactly (including distinct counts).
 *  - "partial": records are server-side aggregates at the rows×columns leaf grain carrying partial
 *    columns (`__sum_x`, `__count_x`, `__min_x`, `__max_x`, `__cd_x`). Subtotals merge partials exactly;
 *    distinct counts are not additive, so their subtotals are null unless a subtotal covers one leaf.
 */
import { toNumber } from "@/lib/format";

export type PivotAgg = "sum" | "count" | "avg" | "min" | "max" | "count_distinct";

export interface PivotValue {
  field: string;
  agg: PivotAgg;
  label?: string;
  format?: string | null;
}

export interface PivotFilter {
  field: string;
  values: unknown[];
  exclude?: boolean;
}

export interface PivotSpec {
  rows: string[];
  cols: string[];
  values: PivotValue[];
  filters?: PivotFilter[];
  subtotals?: boolean;
  grandTotals?: boolean;
  sort?: { by: "label" | "value"; direction: "asc" | "desc"; valueIndex?: number };
}

export interface PivotInput {
  columns: string[];
  rows: unknown[][];
  mode?: "raw" | "partial";
}

export interface PivotRowHeader {
  /** Values for each row dimension; subtotal rows have the tail filled with null. */
  key: (string | null)[];
  depth: number;
  kind: "leaf" | "subtotal" | "grand";
  label: string;
}

export interface PivotResult {
  rowHeaders: PivotRowHeader[];
  /** Leaf column keys (one entry per combination of column-dimension values). */
  colKeys: string[][];
  /** values[rowIndex][colIndex][valueIndex]; the last column index is the row total when grandTotals is on. */
  values: (number | null)[][][];
  hasRowTotalColumn: boolean;
  nonAdditiveNote: string | null;
}

export const partialCol = (kind: "sum" | "count" | "min" | "max" | "cd", field: string) => `__${kind}_${field}`;

class Acc {
  sum = 0;
  count = 0;
  hasValue = false;
  min = Infinity;
  max = -Infinity;
  distinct: Set<string> | null = null;
  partialDistinct: number | null = null;
  leaves = 0;

  addRaw(v: unknown, trackDistinct: boolean) {
    if (v === null || v === undefined || v === "") return;
    this.count += 1;
    if (trackDistinct) {
      if (!this.distinct) this.distinct = new Set();
      this.distinct.add(typeof v === "object" ? JSON.stringify(v) : String(v));
    }
    const n = toNumber(v);
    if (n === null) return;
    this.hasValue = true;
    this.sum += n;
    if (n < this.min) this.min = n;
    if (n > this.max) this.max = n;
  }

  addPartial(p: { sum: unknown; count: unknown; min: unknown; max: unknown; cd: unknown }) {
    this.leaves += 1;
    const s = toNumber(p.sum);
    const c = toNumber(p.count);
    const mn = toNumber(p.min);
    const mx = toNumber(p.max);
    const cd = toNumber(p.cd);
    if (s !== null) {
      this.sum += s;
      this.hasValue = true;
    }
    if (c !== null) this.count += c;
    if (mn !== null && mn < this.min) this.min = mn;
    if (mx !== null && mx > this.max) this.max = mx;
    this.partialDistinct = this.leaves === 1 ? cd : null;
  }

  result(agg: PivotAgg, mode: "raw" | "partial"): number | null {
    switch (agg) {
      case "sum":
        return this.hasValue ? this.sum : null;
      case "count":
        return this.count;
      case "avg":
        return this.count > 0 && this.hasValue ? this.sum / this.count : null;
      case "min":
        return Number.isFinite(this.min) ? this.min : null;
      case "max":
        return Number.isFinite(this.max) ? this.max : null;
      case "count_distinct":
        return mode === "raw" ? (this.distinct?.size ?? 0) : this.partialDistinct;
    }
  }
}

const SEP = "␟";
const label = (v: unknown): string => (v === null || v === undefined || v === "" ? "(blank)" : String(v));

function matchesFilters(get: (f: string) => unknown, filters: PivotFilter[] | undefined): boolean {
  if (!filters?.length) return true;
  return filters.every((f) => {
    if (!f.values.length) return true;
    const v = label(get(f.field));
    const hit = f.values.some((x) => label(x) === v);
    return f.exclude ? !hit : hit;
  });
}

function compareLabels(a: string, b: string): number {
  const na = Number(a);
  const nb = Number(b);
  if (a !== "" && b !== "" && Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return a.localeCompare(b, undefined, { numeric: true });
}

export function computePivot(input: PivotInput, spec: PivotSpec): PivotResult {
  const mode = input.mode ?? "raw";
  const idx = new Map(input.columns.map((c, i) => [c, i]));
  const get = (row: unknown[], f: string) => {
    const i = idx.get(f);
    return i === undefined ? undefined : row[i];
  };
  const subtotals = spec.subtotals ?? true;
  const grandTotals = spec.grandTotals ?? true;

  // Accumulators keyed by (rowPrefixKey, colKey). colKey "" = row total column.
  const accs = new Map<string, Acc[]>();
  const accFor = (rk: string, ck: string) => {
    const k = `${rk}${SEP}${SEP}${ck}`;
    let a = accs.get(k);
    if (!a) {
      a = spec.values.map(() => new Acc());
      accs.set(k, a);
    }
    return a;
  };

  const leafRowKeys = new Map<string, string[]>();
  const colKeyMap = new Map<string, string[]>();
  const rowTotalsForSort = new Map<string, number>();

  for (const row of input.rows) {
    if (!matchesFilters((f) => get(row, f), spec.filters)) continue;
    const rvals = spec.rows.map((f) => label(get(row, f)));
    const cvals = spec.cols.map((f) => label(get(row, f)));
    const rk = rvals.join(SEP);
    const ck = cvals.join(SEP);
    if (!leafRowKeys.has(rk)) leafRowKeys.set(rk, rvals);
    if (spec.cols.length && !colKeyMap.has(ck)) colKeyMap.set(ck, cvals);

    // Every row-prefix (for subtotals, depth 0 = grand) × {leaf col, total col}.
    const prefixes: string[] = [];
    for (let d = 0; d <= spec.rows.length; d++) prefixes.push(`${d}:${rvals.slice(0, d).join(SEP)}`);
    const colTargets = spec.cols.length ? [ck, ""] : [""];
    for (const p of prefixes) {
      for (const c of colTargets) {
        const a = accFor(p, c);
        spec.values.forEach((v, vi) => {
          if (mode === "raw") a[vi].addRaw(get(row, v.field), v.agg === "count_distinct");
          else
            a[vi].addPartial({
              sum: get(row, partialCol("sum", v.field)),
              count: get(row, partialCol("count", v.field)),
              min: get(row, partialCol("min", v.field)),
              max: get(row, partialCol("max", v.field)),
              cd: get(row, partialCol("cd", v.field)),
            });
        });
      }
    }
  }

  const colKeys = [...colKeyMap.values()].sort((a, b) => {
    for (let i = 0; i < a.length; i++) {
      const c = compareLabels(a[i], b[i]);
      if (c) return c;
    }
    return 0;
  });

  const cell = (prefix: string, ck: string): (number | null)[] => {
    const a = accs.get(`${prefix}${SEP}${SEP}${ck}`);
    return spec.values.map((v, vi) => (a ? a[vi].result(v.agg, mode) : null));
  };

  // Sort value for a row prefix: chosen measure at the row-total column.
  const sortVi = spec.sort?.valueIndex ?? 0;
  const sortValue = (prefix: string) => {
    if (rowTotalsForSort.has(prefix)) return rowTotalsForSort.get(prefix)!;
    const v = cell(prefix, "")[sortVi] ?? -Infinity;
    rowTotalsForSort.set(prefix, v);
    return v;
  };

  // Build a hierarchical ordering of row keys.
  const rowHeaders: PivotRowHeader[] = [];
  const values: (number | null)[][][] = [];
  const pushRow = (prefix: string, key: (string | null)[], depth: number, kind: PivotRowHeader["kind"], lbl: string) => {
    rowHeaders.push({ key, depth, kind, label: lbl });
    const r = colKeys.map((ck) => cell(prefix, ck.join(SEP)));
    if (!spec.cols.length || grandTotals) r.push(cell(prefix, ""));
    values.push(r);
  };

  const leaves = [...leafRowKeys.values()];
  const dir = spec.sort?.direction === "desc" ? -1 : 1;
  const walk = (depth: number, parent: string[], group: string[][]) => {
    if (depth === spec.rows.length) return;
    const children = new Map<string, string[][]>();
    for (const k of group) {
      const v = k[depth];
      const arr = children.get(v);
      if (arr) arr.push(k);
      else children.set(v, [k]);
    }
    const ordered = [...children.keys()].sort((a, b) => {
      if (spec.sort?.by === "value") {
        const pa = `${depth + 1}:${[...parent, a].join(SEP)}`;
        const pb = `${depth + 1}:${[...parent, b].join(SEP)}`;
        return (sortValue(pa) - sortValue(pb)) * dir;
      }
      return compareLabels(a, b) * dir;
    });
    for (const v of ordered) {
      const path = [...parent, v];
      const prefix = `${depth + 1}:${path.join(SEP)}`;
      const isLeaf = depth + 1 === spec.rows.length;
      if (isLeaf) {
        pushRow(prefix, path, depth, "leaf", v);
      } else {
        walk(depth + 1, path, children.get(v)!);
        if (subtotals) {
          const key: (string | null)[] = [...path, ...new Array(spec.rows.length - path.length).fill(null)];
          pushRow(prefix, key, depth, "subtotal", `${v} total`);
        }
      }
    }
  };
  if (spec.rows.length) walk(0, [], leaves);
  if (grandTotals || !spec.rows.length) {
    if (leaves.length || !spec.rows.length) {
      pushRow("0:", new Array(spec.rows.length).fill(null), 0, "grand", "Grand total");
    }
  }

  const nonAdditive =
    mode === "partial" && spec.values.some((v) => v.agg === "count_distinct") && (subtotals || grandTotals)
      ? "Distinct counts are not additive: subtotals and totals covering more than one group are left blank."
      : null;

  return {
    rowHeaders,
    colKeys,
    values,
    hasRowTotalColumn: !spec.cols.length || grandTotals,
    nonAdditiveNote: nonAdditive,
  };
}

/** Aggregates the server should return per leaf group for an exact "partial" pivot. */
export function partialAggregatesFor(values: PivotValue[]): { column: string | null; fn: string; alias: string }[] {
  const out: { column: string | null; fn: string; alias: string }[] = [];
  const seen = new Set<string>();
  const add = (fn: string, field: string, kind: "sum" | "count" | "min" | "max" | "cd") => {
    const alias = partialCol(kind, field);
    if (seen.has(alias)) return;
    seen.add(alias);
    out.push({ column: field, fn, alias });
  };
  for (const v of values) {
    if (v.agg === "sum" || v.agg === "avg") add("sum", v.field, "sum");
    if (v.agg === "count" || v.agg === "avg") add("count", v.field, "count");
    if (v.agg === "min") add("min", v.field, "min");
    if (v.agg === "max") add("max", v.field, "max");
    if (v.agg === "count_distinct") add("count_distinct", v.field, "cd");
  }
  return out;
}

export function pivotValueLabel(v: PivotValue): string {
  if (v.label) return v.label;
  const agg = { sum: "Sum", count: "Count", avg: "Avg", min: "Min", max: "Max", count_distinct: "Distinct" }[v.agg];
  return `${agg} of ${v.field}`;
}
