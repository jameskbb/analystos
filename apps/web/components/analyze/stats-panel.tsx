"use client";
import * as React from "react";
import { Play } from "lucide-react";
import {
  analysis,
  type AnalysisOut,
  type ConfidenceInterval,
  type StatTest,
  type TestResult,
} from "@/lib/api/resources/analysis";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/label";
import { Input, NativeSelect } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { DefinitionList, Panel } from "@/components/shell/page";
import { InlineError } from "@/components/states/states";
import { formatInt, formatValue } from "@/lib/format";
import { formatP } from "./charts";
import { ResultMeta } from "./result-meta";
import { ColumnInput, TabularSourceForm, tabularSource, useTabularColumns, type TabularDraft, useAnalysisRun } from "./sources";

interface FieldSpec {
  key: string;
  label: string;
  kind: "column" | "numeric" | "text" | "select" | "bool";
  optional?: boolean;
  options?: string[];
  hint?: string;
}

/** Per-test inputs (API.md "stats-test"), and what each test assumes, shown before running. */
export const TESTS: Record<StatTest, { label: string; when: string; fields: FieldSpec[] }> = {
  t_test: {
    label: "Two-sample t-test (Welch)",
    when: "Compare the mean of a numeric column between two groups. Assumes independent observations; normality matters for small groups.",
    fields: [
      { key: "value_column", label: "Value column", kind: "numeric" },
      { key: "group_column", label: "Group column", kind: "column" },
      { key: "group_a", label: "Group A value", kind: "text" },
      { key: "group_b", label: "Group B value", kind: "text" },
      { key: "equal_var", label: "Assume equal variances (Student)", kind: "bool", optional: true },
    ],
  },
  chi_square: {
    label: "Chi-square test of independence",
    when: "Is one categorical column associated with another? Assumes independent counts and expected counts ≥ 5 in most cells.",
    fields: [
      { key: "row_column", label: "Row category", kind: "column" },
      { key: "column_column", label: "Column category", kind: "column" },
      { key: "count_column", label: "Count column (optional; else one row = one count)", kind: "numeric", optional: true },
    ],
  },
  proportion: {
    label: "Two-proportion z-test",
    when: "Compare a success rate between two groups. Assumes independent trials and enough successes and failures per group (≥ 10).",
    fields: [
      { key: "group_column", label: "Group column", kind: "column" },
      { key: "group_a", label: "Group A value", kind: "text" },
      { key: "group_b", label: "Group B value", kind: "text" },
      { key: "success_column", label: "Success column (0/1, or a count with trials)", kind: "column" },
      { key: "trials_column", label: "Trials column (optional)", kind: "numeric", optional: true },
    ],
  },
  mean_ci: {
    label: "Confidence interval for a mean (t)",
    when: "Range of plausible values for the mean. Assumes independent observations; skewed small samples widen the true uncertainty.",
    fields: [{ key: "value_column", label: "Value column", kind: "numeric" }],
  },
  bootstrap_ci: {
    label: "Bootstrap confidence interval",
    when: "Percentile bootstrap for a mean, median or sum. Makes no normality assumption but needs a representative sample.",
    fields: [
      { key: "value_column", label: "Value column", kind: "numeric" },
      { key: "stat", label: "Statistic", kind: "select", options: ["mean", "median", "sum"] },
    ],
  },
  proportion_ci: {
    label: "Confidence interval for a proportion (Wilson)",
    when: "Range of plausible values for a rate. Assumes independent trials.",
    fields: [
      { key: "success_column", label: "Success column", kind: "column" },
      { key: "trials_column", label: "Trials column (optional)", kind: "numeric", optional: true },
    ],
  },
};

export function isTestResult(r: unknown): r is TestResult {
  return !!r && typeof r === "object" && "p_value" in r && "test" in r;
}

export function StatsView({ out }: { out: AnalysisOut<TestResult | ConfidenceInterval> }) {
  const r = out.result;
  return (
    <ResultMeta out={out}>
      {isTestResult(r) ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {r.significant === null ? null : r.significant ? (
              <Badge tone="accent">Significant at α = {r.alpha}</Badge>
            ) : (
              <Badge tone="neutral">Not significant at α = {r.alpha}</Badge>
            )}
          </div>
          {r.interpretation ? <p className="text-sm">{r.interpretation}</p> : null}
          <DefinitionList
            items={[
              { label: "Statistic", value: r.statistic === null ? "n/a" : r.statistic.toFixed(3), mono: true },
              { label: "p-value", value: formatP(r.p_value), mono: true },
              ...(r.df !== null ? [{ label: "Degrees of freedom", value: r.df.toFixed(1), mono: true }] : []),
              ...(r.estimate !== null ? [{ label: "Estimate", value: formatValue(r.estimate, "number"), mono: true }] : []),
              ...(r.ci_low !== null && r.ci_high !== null
                ? [
                    {
                      label: `${Math.round(r.confidence * 100)}% CI`,
                      value: `${formatValue(r.ci_low, "number")} to ${formatValue(r.ci_high, "number")}`,
                      mono: true,
                    },
                  ]
                : []),
              ...(r.effect_size !== null
                ? [{ label: r.effect_size_name ?? "Effect size", value: r.effect_size.toFixed(3), mono: true }]
                : []),
              { label: "n", value: Object.entries(r.n).map(([k, v]) => `${k}: ${formatInt(v)}`).join(" · ") || "n/a" },
            ]}
          />
        </div>
      ) : (
        <DefinitionList
          items={[
            { label: "Estimate", value: formatValue(r.estimate, "number"), mono: true },
            {
              label: `${Math.round(r.confidence * 100)}% interval`,
              value: `${formatValue(r.low, "number")} to ${formatValue(r.high, "number")}`,
              mono: true,
            },
            { label: "Method", value: r.method },
            { label: "n", value: formatInt(r.n) },
          ]}
        />
      )}
    </ResultMeta>
  );
}

export function StatsPanel() {
  const { id: ws } = useWorkspace();
  const [test, setTest] = React.useState<StatTest>("t_test");
  const [src, setSrc] = React.useState<TabularDraft>({ mode: "table", table: "", sql: "" });
  const cols = useTabularColumns(src);
  const [vals, setVals] = React.useState<Record<string, string | boolean>>({ stat: "mean" });
  const [alpha, setAlpha] = React.useState(0.05);
  const [confidence, setConfidence] = React.useState(0.95);
  const spec = TESTS[test];
  const run = useAnalysisRun(() => {
    const extra: Record<string, unknown> = {};
    for (const f of spec.fields) {
      const v = vals[f.key];
      if (v === undefined || v === "") continue;
      extra[f.key] = v;
    }
    return analysis.statsTest(ws, { test, source: tabularSource(src), alpha, confidence, ...extra });
  });
  const ready =
    (src.mode === "table" ? !!src.table : !!src.sql.trim()) &&
    spec.fields.every((f) => f.optional || f.kind === "bool" || (vals[f.key] !== undefined && vals[f.key] !== ""));

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Statistical test" description="Pick a test that matches the question. Assumptions are checked on the data and reported with the result.">
        <form
          className="grid grid-cols-1 gap-4 p-3 lg:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            run.mutate();
          }}
        >
          <div className="flex flex-col gap-3">
            <Field label="Test" htmlFor="st-test">
              <NativeSelect id="st-test" value={test} onChange={(e) => setTest(e.target.value as StatTest)}>
                {(Object.keys(TESTS) as StatTest[]).map((t) => (
                  <option key={t} value={t}>
                    {TESTS[t].label}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <p className="rounded border border-border bg-bg-subtle px-2 py-1.5 text-xs text-fg-muted" data-testid="test-assumptions">
              {spec.when}
            </p>
            <TabularSourceForm value={src} onChange={setSrc} />
          </div>
          <div className="flex flex-col gap-3">
            {spec.fields.map((f) => {
              const id = `st-${f.key}`;
              if (f.kind === "bool")
                return (
                  <label key={f.key} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={vals[f.key] === true} onChange={(e) => setVals({ ...vals, [f.key]: e.target.checked })} />
                    {f.label}
                  </label>
                );
              return (
                <Field key={f.key} label={f.label} htmlFor={id}>
                  {f.kind === "select" ? (
                    <NativeSelect id={id} value={String(vals[f.key] ?? "")} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })}>
                      {(f.options ?? []).map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </NativeSelect>
                  ) : f.kind === "text" ? (
                    <Input id={id} value={String(vals[f.key] ?? "")} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
                  ) : (
                    <ColumnInput
                      id={id}
                      columns={cols}
                      numericOnly={f.kind === "numeric"}
                      value={String(vals[f.key] ?? "")}
                      onChange={(v) => setVals({ ...vals, [f.key]: v })}
                    />
                  )}
                </Field>
              );
            })}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Significance level α" htmlFor="st-alpha">
                <Input id="st-alpha" type="number" min={0.001} max={0.2} step={0.01} value={alpha} onChange={(e) => setAlpha(Number(e.target.value) || 0.05)} />
              </Field>
              <Field label="Confidence" htmlFor="st-conf">
                <Input id="st-conf" type="number" min={0.5} max={0.999} step={0.01} value={confidence} onChange={(e) => setConfidence(Number(e.target.value) || 0.95)} />
              </Field>
            </div>
            <div>
              <Button type="submit" variant="primary" disabled={!ready || run.isPending}>
                {run.isPending ? <Spinner /> : <Play />} Run test
              </Button>
            </div>
            {run.error ? <InlineError error={run.error} /> : null}
          </div>
        </form>
      </Panel>
      {run.data ? (
        <Panel title="Result" bodyClassName="p-3">
          <StatsView key={run.data.id} out={run.data} />
        </Panel>
      ) : null}
    </div>
  );
}
