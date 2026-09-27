"use client";
import * as React from "react";
import { Play } from "lucide-react";
import {
  analysis,
  type AnalysisOut,
  type CorrelationResult,
  type ImportanceResult,
  type RegressionResult,
} from "@/lib/api/resources/analysis";
import { useWorkspace } from "@/components/providers/workspace";
import { EChart, useThemeVersion } from "@/components/charts/echart";
import { readPalette } from "@/lib/charts/option";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/tabs";
import { Spinner } from "@/components/ui/spinner";
import { DefinitionList, Panel } from "@/components/shell/page";
import { InlineError } from "@/components/states/states";
import { formatInt } from "@/lib/format";
import { correlationOption, formatP } from "./charts";
import { ResultMeta } from "./result-meta";
import { ColumnInput, TabularSourceForm, tabularSource, useTabularColumns, type TabularDraft, useAnalysisRun } from "./sources";

type Mode = "correlation" | "ols" | "importance";

export function isRegression(r: unknown): r is RegressionResult {
  return !!r && typeof r === "object" && "coefficients" in r;
}

const splitList = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

export function CorrelationView({ out }: { out: AnalysisOut<CorrelationResult> }) {
  const r = out.result;
  const theme = useThemeVersion();
  const option = React.useMemo(
    () => correlationOption(r, readPalette()),
    // theme forces a rebuild with the new palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [r, theme],
  );
  const pairs = [...r.pairs].sort((a, b) => Math.abs(b.r ?? 0) - Math.abs(a.r ?? 0)).slice(0, 15);
  return (
    <ResultMeta out={out}>
      <EChart option={option} ariaLabel="Correlation matrix" className="h-80 w-full" />
      <Panel title="Strongest pairs" description={r.caveat}>
        <table className="w-full text-sm" aria-label="Correlation pairs">
          <thead className="text-left text-xs text-fg-subtle">
            <tr>
              <th className="px-3 py-1.5 font-medium">Pair</th>
              <th className="px-3 py-1.5 text-right font-medium">r</th>
              <th className="px-3 py-1.5 text-right font-medium">p</th>
              <th className="px-3 py-1.5 text-right font-medium">n</th>
              <th className="px-3 py-1.5 font-medium">Strength</th>
            </tr>
          </thead>
          <tbody>
            {pairs.map((p) => (
              <tr key={`${p.a}|${p.b}`} className="border-t border-border">
                <td className="px-3 py-1.5">
                  {p.a} × {p.b}
                </td>
                <td className="px-3 py-1.5 text-right tabular">{p.r === null ? "n/a" : p.r.toFixed(2)}</td>
                <td className="px-3 py-1.5 text-right tabular">{formatP(p.p_value)}</td>
                <td className="px-3 py-1.5 text-right tabular">{formatInt(p.n)}</td>
                <td className="px-3 py-1.5 text-fg-muted">{p.strength}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </ResultMeta>
  );
}

export function RegressionView({ out }: { out: AnalysisOut<RegressionResult | ImportanceResult> }) {
  const r = out.result;
  return (
    <ResultMeta out={out}>
      {isRegression(r) ? (
        <>
          <DefinitionList
            items={[
              { label: "Target", value: r.target, mono: true },
              { label: "R²", value: r.r_squared === null ? "n/a" : r.r_squared.toFixed(3), mono: true },
              { label: "Adjusted R²", value: r.adj_r_squared === null ? "n/a" : r.adj_r_squared.toFixed(3), mono: true },
              { label: "F-test p", value: formatP(r.f_pvalue), mono: true },
              { label: "n", value: formatInt(r.n) },
            ]}
          />
          {r.warnings.length ? (
            <ul className="list-disc pl-5 text-sm text-warning">
              {r.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          ) : null}
          <Panel title="Coefficients (OLS)" description={r.caveat}>
            <table className="w-full text-sm" aria-label="Coefficients">
              <thead className="text-left text-xs text-fg-subtle">
                <tr>
                  <th className="px-3 py-1.5 font-medium">Term</th>
                  <th className="px-3 py-1.5 text-right font-medium">Coefficient</th>
                  <th className="px-3 py-1.5 text-right font-medium">Std. error</th>
                  <th className="px-3 py-1.5 text-right font-medium">95% CI</th>
                  <th className="px-3 py-1.5 text-right font-medium">p</th>
                  <th className="px-3 py-1.5 text-right font-medium">VIF</th>
                </tr>
              </thead>
              <tbody>
                {r.coefficients.map((c) => (
                  <tr key={c.name} className="border-t border-border">
                    <td className="px-3 py-1.5 font-mono text-xs">{c.name}</td>
                    <td className="px-3 py-1.5 text-right tabular">{c.coef.toPrecision(4)}</td>
                    <td className="px-3 py-1.5 text-right tabular text-fg-muted">{c.std_err === null ? "n/a" : c.std_err.toPrecision(3)}</td>
                    <td className="px-3 py-1.5 text-right tabular text-fg-muted">
                      {c.ci_low === null || c.ci_high === null ? "n/a" : `${c.ci_low.toPrecision(3)} to ${c.ci_high.toPrecision(3)}`}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular">{formatP(c.p_value)}</td>
                    <td className="px-3 py-1.5 text-right tabular text-fg-muted">
                      {r.vif[c.name] === null || r.vif[c.name] === undefined ? "n/a" : Number(r.vif[c.name]).toFixed(1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </>
      ) : (
        <Panel title={`Permutation importance (${r.model}, ${r.task})`} description={r.caveat}>
          <table className="w-full text-sm" aria-label="Feature importance">
            <thead className="text-left text-xs text-fg-subtle">
              <tr>
                <th className="px-3 py-1.5 font-medium">Feature</th>
                <th className="px-3 py-1.5 text-right font-medium">Importance</th>
                <th className="px-3 py-1.5 text-right font-medium">± std</th>
              </tr>
            </thead>
            <tbody>
              {r.features.map((f) => (
                <tr key={f.name} className="border-t border-border">
                  <td className="px-3 py-1.5 font-mono text-xs">{f.name}</td>
                  <td className="px-3 py-1.5 text-right tabular">{f.importance_mean.toFixed(3)}</td>
                  <td className="px-3 py-1.5 text-right tabular text-fg-muted">{f.importance_std.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t border-border px-3 py-1.5 text-xs text-fg-subtle">
            Holdout {r.score_name}: {r.holdout_score === null ? "n/a" : r.holdout_score.toFixed(3)} · n = {formatInt(r.n)}
          </p>
        </Panel>
      )}
    </ResultMeta>
  );
}

export function CorrelationPanel() {
  const { id: ws } = useWorkspace();
  const [mode, setMode] = React.useState<Mode>("correlation");
  const [src, setSrc] = React.useState<TabularDraft>({ mode: "table", table: "", sql: "" });
  const cols = useTabularColumns(src);
  const [columns, setColumns] = React.useState("");
  const [method, setMethod] = React.useState<"pearson" | "spearman">("pearson");
  const [target, setTarget] = React.useState("");
  const [features, setFeatures] = React.useState("");
  const source = tabularSource(src);
  const corr = useAnalysisRun(() =>
      analysis.correlation(ws, { source, method, ...(splitList(columns).length ? { columns: splitList(columns) } : {}) }));
  const reg = useAnalysisRun(() =>
      analysis.regression(ws, { source, target, features: splitList(features), model: mode === "importance" ? "importance" : "ols" }));
  const active = mode === "correlation" ? corr : reg;
  const hasSource = src.mode === "table" ? !!src.table : !!src.sql.trim();
  const ready = hasSource && (mode === "correlation" || (!!target && splitList(features).length > 0));
  const numeric = cols.filter((c) => /int|dec|num|float|double|real/i.test(c.type)).map((c) => c.name);

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Correlation and drivers"
        description="Exploratory: associations between columns, never evidence of cause on their own."
      >
        <form
          className="grid grid-cols-1 gap-4 p-3 lg:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            active.mutate();
          }}
        >
          <div className="flex flex-col gap-3">
            <Segmented<Mode>
              aria-label="Analysis"
              value={mode}
              onChange={setMode}
              options={[
                { value: "correlation", label: "Correlation matrix" },
                { value: "ols", label: "Linear regression" },
                { value: "importance", label: "Feature importance" },
              ]}
            />
            <TabularSourceForm value={src} onChange={setSrc} />
          </div>
          <div className="flex flex-col gap-3">
            {mode === "correlation" ? (
              <>
                <Field label="Columns (optional)" htmlFor="co-cols" hint={numeric.length ? `Numeric: ${numeric.join(", ")}` : "Default: every numeric column (max 30)"}>
                  <Input id="co-cols" value={columns} placeholder="all numeric" onChange={(e) => setColumns(e.target.value)} />
                </Field>
                <Field label="Method">
                  <Segmented<"pearson" | "spearman">
                    aria-label="Correlation method"
                    value={method}
                    onChange={setMethod}
                    options={[
                      { value: "pearson", label: "Pearson (linear)" },
                      { value: "spearman", label: "Spearman (rank)" },
                    ]}
                  />
                </Field>
              </>
            ) : (
              <>
                <Field label="Target" htmlFor="rg-t">
                  <ColumnInput id="rg-t" columns={cols} numericOnly={mode === "ols"} value={target} onChange={setTarget} />
                </Field>
                <Field label="Features" htmlFor="rg-f" hint={cols.length ? `Available: ${cols.map((c) => c.name).join(", ")}` : "Comma-separated column names (1 to 20)"}>
                  <Input id="rg-f" value={features} placeholder="e.g. discount_rate, units" onChange={(e) => setFeatures(e.target.value)} />
                </Field>
              </>
            )}
            <div>
              <Button type="submit" variant="primary" disabled={!ready || active.isPending}>
                {active.isPending ? <Spinner /> : <Play />} Run
              </Button>
            </div>
            {active.error ? <InlineError error={active.error} /> : null}
          </div>
        </form>
      </Panel>
      {mode === "correlation" && corr.data ? (
        <Panel title="Result" bodyClassName="p-3">
          <CorrelationView key={corr.data.id} out={corr.data} />
        </Panel>
      ) : null}
      {mode !== "correlation" && reg.data ? (
        <Panel title="Result" bodyClassName="p-3">
          <RegressionView key={reg.data.id} out={reg.data} />
        </Panel>
      ) : null}
    </div>
  );
}
