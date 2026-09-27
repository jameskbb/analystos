/**
 * Chart suggestion by data shape (spec §34). Pure and deterministic.
 *
 * Columns are classified into measures (numeric, not identifiers), temporal fields and categorical
 * fields; each chart type has a rule for when it is appropriate. Pie charts are never suggested.
 */
import type { ChartConfig, ChartType, QueryColumn } from "@/lib/api/types";
import { isNumericType, isTemporalType, toNumber } from "@/lib/format";

export type FieldRole = "measure" | "temporal" | "categorical" | "identifier" | "boolean";

export interface FieldProfile {
  name: string;
  type: string;
  role: FieldRole;
  distinct: number;
  nonNull: number;
  hasNegative: boolean;
}

export interface ChartSuggestion {
  config: ChartConfig;
  reason: string;
  score: number;
}

const ISO_DATE = /^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;
const ID_NAME = /(^id$|_id$|^id_|_key$|^key$|uuid|_code$|^sku$|zip|postal)/i;
const TEMPORAL_NAME = /(date|month|week|day|year|quarter|period|time|_at$|_on$)/i;
const CHANGE_NAME = /(change|delta|diff|variance|impact|contribution|effect|bridge)/i;

export function profileFields(columns: QueryColumn[], rows: unknown[][], sampleSize = 2000): FieldProfile[] {
  const sample = rows.length > sampleSize ? rows.slice(0, sampleSize) : rows;
  return columns.map((col, idx) => {
    const values = sample.map((r) => r[idx]).filter((v) => v !== null && v !== undefined && v !== "");
    const distinct = new Set(values.map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v)))).size;
    const nums = values.map(toNumber);
    const allNumeric = values.length > 0 && nums.every((n) => n !== null);
    const numericType = isNumericType(col.type) || (!col.type && allNumeric);
    const isBool = /bool/i.test(col.type) || (values.length > 0 && values.every((v) => typeof v === "boolean"));
    const temporalByValue =
      values.length > 0 && values.every((v) => typeof v === "string" && ISO_DATE.test(v));
    const temporal =
      isTemporalType(col.type) ||
      temporalByValue ||
      (numericType && /^(year|yr|fiscal_year)$/i.test(col.name) && nums.every((n) => n !== null && n > 1900 && n < 2200));

    let role: FieldRole;
    if (isBool) role = "boolean";
    else if (temporal) role = "temporal";
    else if (numericType) {
      const looksId = ID_NAME.test(col.name) && distinct >= Math.max(values.length * 0.9, 1);
      role = looksId || (ID_NAME.test(col.name) && /int/i.test(col.type)) ? "identifier" : "measure";
    } else {
      role =
        ID_NAME.test(col.name) && distinct > 50 && distinct >= values.length * 0.9 ? "identifier" : "categorical";
      if (role === "categorical" && TEMPORAL_NAME.test(col.name) && values.length && values.every((v) => typeof v === "string" && /^\d{4}([-/]\d{1,2})?/.test(v))) {
        role = "temporal";
      }
    }
    return {
      name: col.name,
      type: col.type,
      role,
      distinct,
      nonNull: values.length,
      hasNegative: nums.some((n) => n !== null && n < 0),
    };
  });
}

function push(out: ChartSuggestion[], type: ChartType, cfg: Omit<ChartConfig, "type">, reason: string, score: number) {
  out.push({ config: { type, ...cfg }, reason, score });
}

export function suggestCharts(columns: QueryColumn[], rows: unknown[][]): ChartSuggestion[] {
  const out: ChartSuggestion[] = [];
  if (!columns.length) return out;
  const fields = profileFields(columns, rows);
  const measures = fields.filter((f) => f.role === "measure");
  const temporals = fields.filter((f) => f.role === "temporal");
  const cats = fields.filter((f) => f.role === "categorical" || f.role === "boolean");
  const n = rows.length;

  // KPI: a single row of one or two measures and no dimensions to plot.
  if (n === 1 && measures.length >= 1 && measures.length <= 3 && temporals.length + cats.length <= 1) {
    push(
      out,
      "kpi",
      { y: measures.slice(0, 2).map((m) => m.name), value: measures[0].name },
      "A single row of measures reads best as a KPI.",
      100,
    );
  }

  // Time series.
  if (temporals.length >= 1 && measures.length >= 1 && n > 1) {
    const x = temporals[0].name;
    const seriesField = cats.find((c) => c.distinct >= 2 && c.distinct <= 12);
    if (seriesField) {
      push(
        out,
        "line",
        { x, y: [measures[0].name], series: seriesField.name },
        `Trend of ${measures[0].name} over ${x}, one line per ${seriesField.name}.`,
        92,
      );
      push(
        out,
        "stacked_bar",
        { x, y: [measures[0].name], series: seriesField.name },
        `Composition of ${measures[0].name} by ${seriesField.name} over time.`,
        80,
      );
      push(
        out,
        "area",
        { x, y: [measures[0].name], series: seriesField.name },
        "Stacked area shows the total and its parts over time.",
        70,
      );
    } else {
      push(
        out,
        "line",
        { x, y: measures.slice(0, 4).map((m) => m.name) },
        `${measures[0].name} over ${x} is a time series: a line shows the trend.`,
        95,
      );
      push(out, "area", { x, y: measures.slice(0, 1).map((m) => m.name) }, "Area emphasizes volume over time.", 72);
      push(
        out,
        "bar",
        { x, y: measures.slice(0, 2).map((m) => m.name) },
        "Bars compare discrete periods.",
        n <= 36 ? 78 : 55,
      );
    }
  }

  // Waterfall: a categorical bridge of signed changes.
  const changeMeasure = measures.find((m) => CHANGE_NAME.test(m.name) && m.hasNegative) ?? null;
  if (cats.length >= 1 && changeMeasure && n >= 2 && n <= 40) {
    push(
      out,
      "waterfall",
      { x: cats[0].name, y: [changeMeasure.name] },
      `Signed ${changeMeasure.name} per ${cats[0].name} forms a bridge: a waterfall shows how each part moves the total.`,
      temporals.length ? 75 : 94,
    );
  }

  // Categorical comparisons.
  if (cats.length >= 1 && measures.length >= 1 && temporals.length === 0) {
    const x = cats[0];
    const aggregated = x.distinct === n || x.distinct >= n * 0.9;
    if (aggregated) {
      push(
        out,
        "bar",
        {
          x: x.name,
          y: measures.slice(0, 3).map((m) => m.name),
          sort: { field: measures[0].name, direction: "desc" },
          horizontal: x.distinct > 12,
        },
        `Compare ${measures[0].name} across ${x.distinct} ${x.name} values, sorted by size.`,
        changeMeasure ? 85 : 90,
      );
    }
    const second = cats.find((c) => c.name !== x.name && c.distinct >= 2 && c.distinct <= 30);
    if (second) {
      push(
        out,
        "stacked_bar",
        { x: x.name, y: [measures[0].name], series: second.name },
        `Break ${measures[0].name} by ${x.name} and ${second.name}.`,
        78,
      );
      push(
        out,
        "heatmap",
        { x: x.name, series: second.name, value: measures[0].name, y: [measures[0].name] },
        `Two categorical fields and one measure: a heatmap exposes the ${x.name} × ${second.name} pattern.`,
        x.distinct > 6 && second.distinct > 4 ? 84 : 66,
      );
    }
    if (!aggregated && x.distinct <= 30 && n >= x.distinct * 5) {
      push(
        out,
        "box",
        { x: x.name, y: [measures[0].name] },
        `Many ${measures[0].name} values per ${x.name}: a box plot compares distributions.`,
        82,
      );
    }
  }

  // Two measures: relationship.
  if (measures.length >= 2 && n >= 8) {
    push(
      out,
      "scatter",
      { x: measures[0].name, y: [measures[1].name], series: cats.find((c) => c.distinct <= 8)?.name ?? null },
      `Relationship between ${measures[0].name} and ${measures[1].name} (exploratory, not causal).`,
      temporals.length || cats.length ? 60 : 88,
    );
  }

  // Distribution of a single measure.
  if (measures.length >= 1 && n >= 20) {
    const m = measures[0];
    if (m.distinct > 10) {
      push(
        out,
        "histogram",
        { x: m.name, y: [m.name] },
        `Distribution of ${m.name} across ${n} rows.`,
        temporals.length || (cats.length && cats[0].distinct === n) ? 45 : cats.length ? 60 : measures.length === 1 ? 86 : 62,
      );
    }
  }

  push(out, "table", {}, "Every result can be shown as a table.", 10);

  // De-duplicate by type (keep highest score) and sort.
  const best = new Map<ChartType, ChartSuggestion>();
  for (const s of out) {
    const prev = best.get(s.config.type);
    if (!prev || s.score > prev.score) best.set(s.config.type, s);
  }
  return [...best.values()].sort((a, b) => b.score - a.score);
}

export function suggestChart(columns: QueryColumn[], rows: unknown[][]): ChartConfig {
  return suggestCharts(columns, rows)[0]?.config ?? { type: "table" };
}

/** Which chart types are valid for a given shape, used to enable/disable the type switcher. */
export function compatibleTypes(columns: QueryColumn[], rows: unknown[][]): Set<ChartType> {
  const types = new Set<ChartType>(suggestCharts(columns, rows).map((s) => s.config.type));
  const fields = profileFields(columns, rows);
  const measures = fields.filter((f) => f.role === "measure");
  if (measures.length >= 1) {
    ["bar", "line", "area", "table"].forEach((t) => types.add(t as ChartType));
    if (fields.length >= 2) types.add("waterfall");
    if (rows.length >= 2) types.add("histogram");
  }
  types.add("table");
  return types;
}

export const CHART_TYPE_LABELS: Record<ChartType, string> = {
  line: "Line",
  bar: "Bar",
  stacked_bar: "Stacked bar",
  area: "Area",
  scatter: "Scatter",
  histogram: "Histogram",
  box: "Box plot",
  heatmap: "Heatmap",
  waterfall: "Waterfall",
  kpi: "KPI",
  table: "Table",
};

export const ALL_CHART_TYPES: ChartType[] = [
  "line",
  "bar",
  "stacked_bar",
  "area",
  "scatter",
  "histogram",
  "box",
  "heatmap",
  "waterfall",
  "kpi",
  "table",
];
