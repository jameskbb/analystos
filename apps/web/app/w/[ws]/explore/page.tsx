"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BarChart3, Database, FileCode2, Grid3x3, Play, Plus, Save, Sigma, Table as TableIcon, Upload, X } from "lucide-react";
import {
  explore,
  queries,
  type ExploreAggFn,
  type MetricExploreResult,
  type TableExploreResult,
} from "@/lib/api/resources/workbench";
import { qk } from "@/lib/api/keys";
import type { ChartConfig, QueryResult } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageHeader } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { DataGrid } from "@/components/data-grid/data-grid";
import { ChartView } from "@/components/charts/chart-view";
import { CodeBlock } from "@/components/code/code-block";
import { FilterChips, normalizeFilterContext } from "@/components/analysis/filter-chips";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { Spinner } from "@/components/ui/spinner";
import { FilterEditor } from "@/components/workbench/explore/filter-editor";
import { ConfigSection, MultiPick } from "@/components/workbench/explore/multi-pick";
import { PivotTable } from "@/components/workbench/explore/pivot-table";
import {
  ROWS_FIELD,
  aggAlias,
  buildMetricQuery,
  buildPivotRequest,
  buildTableRequest,
  draftChips,
  type AggDraft,
  type MetricConfig,
  type PivotConfig,
  type TableConfig,
} from "@/components/workbench/explore/build";
import { SaveQueryDialog, type SaveQueryValues } from "@/components/workbench/save-query-dialog";
import { QueryErrorView } from "@/components/workbench/results-panel";
import { writeSqlDraft } from "@/components/workbench/sql-draft";
import { useElementHeight } from "@/components/workbench/use-media";
import { computePivot, type PivotAgg, type PivotValue } from "@/lib/pivot";
import { formatDuration, formatInt } from "@/lib/format";

const AGG_FNS: { value: ExploreAggFn; label: string }[] = [
  { value: "sum", label: "Sum" },
  { value: "count", label: "Count" },
  { value: "count_distinct", label: "Count distinct" },
  { value: "avg", label: "Average" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
  { value: "median", label: "Median" },
];
const PIVOT_AGGS: { value: PivotAgg; label: string }[] = [
  { value: "sum", label: "Sum" },
  { value: "count", label: "Count" },
  { value: "avg", label: "Average" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
  { value: "count_distinct", label: "Count distinct" },
];
const GRAINS = ["", "day", "week", "month", "quarter", "year"];

const sel = "h-6 min-w-0 rounded border border-border-strong bg-bg px-1 text-xs";
const inp = "h-6 min-w-0 rounded border border-border-strong bg-bg px-1.5 text-xs";

type Source = "table" | "metrics";
type Mode = "table" | "pivot";

function Explorer() {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const search = useSearchParams();
  const qc = useQueryClient();
  const schemaQ = useQuery({ queryKey: qk.schema(ws), queryFn: () => queries.schema(ws) });

  const [source, setSource] = React.useState<Source>(search.get("metric") ? "metrics" : "table");
  const [mode, setMode] = React.useState<Mode>("table");
  const [view, setView] = React.useState<"grid" | "chart">("grid");
  const [chart, setChart] = React.useState<ChartConfig | null>(null);

  const [tcfg, setTcfg] = React.useState<TableConfig>({
    table: search.get("table") ?? "",
    filters: [],
    groupBy: [],
    aggregates: [],
    calculated: [],
    sort: null,
    limit: 1000,
  });
  const [pcfg, setPcfg] = React.useState<PivotConfig>({
    rows: [],
    cols: [],
    values: [],
    subtotals: true,
    grandTotals: true,
    sortBy: "label",
    sortDir: "asc",
  });
  const [mcfg, setMcfg] = React.useState<MetricConfig>({
    metrics: search.get("metric") ? [search.get("metric") as string] : [],
    dimensions: [],
    filters: [],
    timeDimension: "",
    start: "",
    end: "",
    limit: 1000,
  });
  const [saveOpen, setSaveOpen] = React.useState(false);
  // Rows/columns/values the current pivot result was fetched for; totals and sorting apply live.
  const [pivotShape, setPivotShape] = React.useState<Pick<PivotConfig, "rows" | "cols" | "values"> | null>(null);

  const tables = schemaQ.data?.tables ?? [];
  const table = tables.find((t) => t.table === tcfg.table);
  const calcNames = tcfg.calculated.filter((c) => c.name.trim() && c.expr.trim()).map((c) => c.name.trim());
  const fieldNames = [...(table?.columns.map((c) => c.name) ?? []), ...calcNames];
  const types: Record<string, string | undefined> = Object.fromEntries((table?.columns ?? []).map((c) => [c.name, c.type]));

  const tableRun = useMutation<TableExploreResult, Error, void>({
    mutationFn: () => explore.table(ws, buildTableRequest(tcfg, types)),
  });
  const pivotRun = useMutation<TableExploreResult, Error, void>({
    mutationFn: () => explore.table(ws, buildPivotRequest(tcfg.table, tcfg.filters, tcfg.calculated, pcfg, types)),
  });
  const metricRun = useMutation<MetricExploreResult, Error, void>({
    mutationFn: () => explore.metrics(ws, buildMetricQuery(mcfg)),
  });

  const canRun =
    source === "metrics"
      ? mcfg.metrics.length > 0
      : !!tcfg.table && (mode === "table" || (pcfg.values.length > 0 && pcfg.rows.length + pcfg.cols.length > 0));

  const run = React.useCallback(() => {
    if (!canRun) return;
    setChart(null);
    if (source === "metrics") metricRun.mutate();
    else if (mode === "pivot") {
      setPivotShape({ rows: pcfg.rows, cols: pcfg.cols, values: pcfg.values });
      pivotRun.mutate();
    }
    else tableRun.mutate();
  }, [canRun, source, mode, metricRun, pivotRun, tableRun, pcfg]);

  // Auto-run a preview when a table is picked.
  const lastTable = React.useRef("");
  React.useEffect(() => {
    if (source === "table" && mode === "table" && tcfg.table && tcfg.table !== lastTable.current && table) {
      lastTable.current = tcfg.table;
      tableRun.mutate();
    }
  }, [source, mode, tcfg.table, table, tableRun]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        run();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  const active = source === "metrics" ? metricRun : mode === "pivot" ? pivotRun : tableRun;
  const sql = source === "metrics" ? metricRun.data?.compiled.sql : active.data && "sql" in active.data ? (active.data as TableExploreResult).sql : undefined;
  const result: QueryResult | undefined = active.data?.result;
  const filterContext = active.data?.filter_context ?? draftChips(source === "metrics" ? mcfg.filters : tcfg.filters);

  const pivot = React.useMemo(() => {
    if (source !== "table" || mode !== "pivot" || !pivotRun.data || !pivotShape) return null;
    const r = pivotRun.data.result;
    return computePivot(
      { mode: "partial", columns: r.columns.map((c) => c.name), rows: r.rows },
      {
        rows: pivotShape.rows,
        cols: pivotShape.cols,
        values: pivotShape.values,
        subtotals: pcfg.subtotals,
        grandTotals: pcfg.grandTotals,
        sort: { by: pcfg.sortBy, direction: pcfg.sortDir, valueIndex: 0 },
      },
    );
  }, [source, mode, pivotRun.data, pivotShape, pcfg.subtotals, pcfg.grandTotals, pcfg.sortBy, pcfg.sortDir]);

  const saveMutation = useMutation({
    mutationFn: (v: SaveQueryValues) =>
      queries.save(ws, { name: v.name, description: v.description, tags: v.tags, sql: sql ?? "", parameters: [] }),
    onSuccess: (q) => {
      setSaveOpen(false);
      void qc.invalidateQueries({ queryKey: qk.savedQueries(ws) });
      toast.success(`Saved “${q.name}”`, {
        action: { label: "Open", onClick: () => router.push(href(`/sql?saved=${q.id}`)) },
      });
    },
  });

  const openInSql = () => {
    if (!sql) return;
    writeSqlDraft(ws, sql);
    router.push(href("/sql"));
  };

  const [bodyRef, bodyHeight] = useElementHeight<HTMLDivElement>();

  if (schemaQ.isLoading) return <LoadingState variant="block" label="Loading tables" />;
  if (schemaQ.isError) return <ErrorState error={schemaQ.error} onRetry={() => void schemaQ.refetch()} />;
  if (!tables.length && !(schemaQ.data?.metrics.length ?? 0))
    return (
      <Page>
        <PageHeader title="Explore" description="Filter, group, aggregate, pivot and chart any table without writing SQL." />
        <EmptyState
          icon={Database}
          title="No data to explore yet"
          description="Load the Summit Supply demo or upload a CSV, Excel, Parquet or JSON file."
          action={<LoadDemoButton workspaceId={ws} />}
          secondary={
            <Button asChild>
              <Link href={href("/data?upload=1")}>
                <Upload /> Upload a file
              </Link>
            </Button>
          }
        />
      </Page>
    );

  const setT = (patch: Partial<TableConfig>) => setTcfg((c) => ({ ...c, ...patch }));
  const setP = (patch: Partial<PivotConfig>) => setPcfg((c) => ({ ...c, ...patch }));
  const setM = (patch: Partial<MetricConfig>) => setMcfg((c) => ({ ...c, ...patch }));
  const sortOptions = [...tcfg.groupBy, ...tcfg.aggregates.map(aggAlias), ...(tcfg.groupBy.length || tcfg.aggregates.length ? [] : fieldNames)];

  const config = (
    <div className="flex flex-col text-sm">
      <div className="flex flex-col gap-2 border-b border-border px-3 py-2.5">
        <Segmented
          aria-label="Source"
          value={source}
          onChange={(v) => {
            setSource(v);
            if (v === "metrics") setMode("table");
          }}
          options={[
            { value: "table", label: (<><Database /> Table</>) },
            { value: "metrics", label: (<><Sigma /> Metrics</>) },
          ]}
        />
        {source === "table" ? (
          <Segmented
            aria-label="Mode"
            value={mode}
            onChange={setMode}
            options={[
              { value: "table", label: (<><TableIcon /> Aggregate</>) },
              { value: "pivot", label: (<><Grid3x3 /> Pivot</>) },
            ]}
          />
        ) : null}
      </div>

      {source === "table" ? (
        <>
          <ConfigSection title="Table">
            <select
              aria-label="Table"
              value={tcfg.table}
              onChange={(e) => {
                setT({ table: e.target.value, filters: [], groupBy: [], aggregates: [], calculated: [], sort: null });
                setP({ rows: [], cols: [], values: [] });
              }}
              className={`${sel} w-full`}
            >
              <option value="">Choose a table…</option>
              {tables.map((t) => (
                <option key={t.table} value={t.table}>
                  {t.table}
                  {t.row_count !== null && t.row_count !== undefined ? ` (${formatInt(t.row_count)} rows)` : ""}
                </option>
              ))}
            </select>
            {table ? (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-xs text-fg-subtle">{table.columns.length} fields</summary>
                <ul className="mt-1 max-h-40 overflow-auto text-xs scrollbar-thin">
                  {table.columns.map((c) => (
                    <li key={c.name} className="flex justify-between gap-2 py-0.5">
                      <span className="truncate font-mono">{c.name}</span>
                      <span className="shrink-0 font-mono text-2xs text-fg-faint">{c.type}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </ConfigSection>
          {table ? (
            <>
              <ConfigSection title="Filters">
                <FilterEditor filters={tcfg.filters} onChange={(filters) => setT({ filters })} fields={fieldNames.map((n) => ({ name: n, type: types[n] }))} />
              </ConfigSection>
              <ConfigSection title="Calculated fields" hint="Scalar SQL expressions over this table's columns, e.g. net_amount - unit_cost * qty">
                <div className="flex flex-col gap-1">
                  {tcfg.calculated.map((c, i) => (
                    <div key={i} className="flex items-center gap-1">
                      <input
                        aria-label={`Calculated field ${i + 1} name`}
                        value={c.name}
                        placeholder="name"
                        onChange={(e) => setT({ calculated: tcfg.calculated.map((x, j) => (j === i ? { ...x, name: e.target.value.replace(/\s+/g, "_") } : x)) })}
                        className={`${inp} w-24 font-mono`}
                      />
                      <input
                        aria-label={`Calculated field ${i + 1} expression`}
                        value={c.expr}
                        placeholder="expression"
                        onChange={(e) => setT({ calculated: tcfg.calculated.map((x, j) => (j === i ? { ...x, expr: e.target.value } : x)) })}
                        className={`${inp} flex-1 font-mono`}
                      />
                      <Button variant="ghost" size="icon-xs" aria-label={`Remove calculated field ${i + 1}`} onClick={() => setT({ calculated: tcfg.calculated.filter((_, j) => j !== i) })}>
                        <X />
                      </Button>
                    </div>
                  ))}
                  <Button size="xs" variant="ghost" className="self-start" onClick={() => setT({ calculated: [...tcfg.calculated, { name: "", expr: "" }] })}>
                    <Plus /> Add calculated field
                  </Button>
                </div>
              </ConfigSection>

              {mode === "table" ? (
                <>
                  <ConfigSection title="Group by">
                    <MultiPick label="Group by" value={tcfg.groupBy} onChange={(groupBy) => setT({ groupBy })} options={fieldNames} placeholder="Add grouping field…" />
                  </ConfigSection>
                  <ConfigSection title="Aggregates" hint={tcfg.groupBy.length && !tcfg.aggregates.length ? "Row count is used when no aggregate is set." : undefined}>
                    <div className="flex flex-col gap-1">
                      {tcfg.aggregates.map((a, i) => (
                        <div key={i} className="flex items-center gap-1">
                          <select
                            aria-label={`Aggregate ${i + 1} function`}
                            value={a.fn}
                            onChange={(e) => setT({ aggregates: tcfg.aggregates.map((x, j) => (j === i ? { ...x, fn: e.target.value as ExploreAggFn } : x)) })}
                            className={`${sel} w-28`}
                          >
                            {AGG_FNS.map((f) => (
                              <option key={f.value} value={f.value}>
                                {f.label}
                              </option>
                            ))}
                          </select>
                          <select
                            aria-label={`Aggregate ${i + 1} field`}
                            value={a.column}
                            onChange={(e) => setT({ aggregates: tcfg.aggregates.map((x, j) => (j === i ? { ...x, column: e.target.value } : x)) })}
                            className={`${sel} flex-1`}
                          >
                            <option value="">{a.fn === "count" ? "All rows" : "Choose field…"}</option>
                            {fieldNames.map((n) => (
                              <option key={n} value={n}>
                                {n}
                              </option>
                            ))}
                          </select>
                          <Button variant="ghost" size="icon-xs" aria-label={`Remove aggregate ${i + 1}`} onClick={() => setT({ aggregates: tcfg.aggregates.filter((_, j) => j !== i) })}>
                            <X />
                          </Button>
                        </div>
                      ))}
                      <Button size="xs" variant="ghost" className="self-start" onClick={() => setT({ aggregates: [...tcfg.aggregates, { fn: "sum", column: "" } as AggDraft] })}>
                        <Plus /> Add aggregate
                      </Button>
                    </div>
                  </ConfigSection>
                  <ConfigSection title="Sort and limit">
                    <div className="flex items-center gap-1">
                      <select
                        aria-label="Sort field"
                        value={tcfg.sort?.field ?? ""}
                        onChange={(e) => setT({ sort: e.target.value ? { field: e.target.value, desc: tcfg.sort?.desc ?? true } : null })}
                        className={`${sel} flex-1`}
                      >
                        <option value="">No sort</option>
                        {sortOptions.map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </select>
                      <select
                        aria-label="Sort direction"
                        value={tcfg.sort?.desc ? "desc" : "asc"}
                        disabled={!tcfg.sort}
                        onChange={(e) => tcfg.sort && setT({ sort: { ...tcfg.sort, desc: e.target.value === "desc" } })}
                        className={`${sel} w-20`}
                      >
                        <option value="desc">Desc</option>
                        <option value="asc">Asc</option>
                      </select>
                      <select aria-label="Row limit" value={tcfg.limit} onChange={(e) => setT({ limit: Number(e.target.value) })} className={`${sel} w-20`}>
                        {[100, 1000, 10000, 50000].map((n) => (
                          <option key={n} value={n}>
                            {n.toLocaleString()}
                          </option>
                        ))}
                      </select>
                    </div>
                  </ConfigSection>
                </>
              ) : (
                <>
                  <ConfigSection title="Rows">
                    <MultiPick label="Pivot rows" value={pcfg.rows} onChange={(rows) => setP({ rows })} options={fieldNames.filter((f) => !pcfg.cols.includes(f))} placeholder="Add row field…" />
                  </ConfigSection>
                  <ConfigSection title="Columns">
                    <MultiPick label="Pivot columns" value={pcfg.cols} onChange={(cols) => setP({ cols })} options={fieldNames.filter((f) => !pcfg.rows.includes(f))} placeholder="Add column field…" />
                  </ConfigSection>
                  <ConfigSection title="Values">
                    <div className="flex flex-col gap-1">
                      {pcfg.values.map((v, i) => {
                        const setV = (patch: Partial<PivotValue>) => setP({ values: pcfg.values.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
                        return (
                          <div key={i} className="flex flex-col gap-1 rounded border border-border p-1.5">
                            <div className="flex items-center gap-1">
                              <select aria-label={`Value ${i + 1} aggregation`} value={v.agg} onChange={(e) => setV({ agg: e.target.value as PivotAgg })} className={`${sel} w-28`}>
                                {PIVOT_AGGS.map((a) => (
                                  <option key={a.value} value={a.value}>
                                    {a.label}
                                  </option>
                                ))}
                              </select>
                              <select
                                aria-label={`Value ${i + 1} field`}
                                value={v.field}
                                onChange={(e) => setV({ field: e.target.value, label: e.target.value === ROWS_FIELD ? "Rows" : undefined })}
                                className={`${sel} flex-1`}
                              >
                                <option value={ROWS_FIELD}>All rows</option>
                                {fieldNames.map((n) => (
                                  <option key={n} value={n}>
                                    {n}
                                  </option>
                                ))}
                              </select>
                              <Button variant="ghost" size="icon-xs" aria-label={`Remove value ${i + 1}`} onClick={() => setP({ values: pcfg.values.filter((_, j) => j !== i) })}>
                                <X />
                              </Button>
                            </div>
                            <select aria-label={`Value ${i + 1} format`} value={v.format ?? ""} onChange={(e) => setV({ format: e.target.value || null })} className={sel}>
                              <option value="">Format: auto</option>
                              <option value="currency">Currency</option>
                              <option value="integer">Integer</option>
                              <option value="number">Number</option>
                              <option value="percent">Percent</option>
                            </select>
                          </div>
                        );
                      })}
                      <Button
                        size="xs"
                        variant="ghost"
                        className="self-start"
                        onClick={() => setP({ values: [...pcfg.values, { field: ROWS_FIELD, agg: "count", label: "Rows" }] })}
                      >
                        <Plus /> Add value
                      </Button>
                    </div>
                  </ConfigSection>
                  <ConfigSection title="Totals and sorting">
                    <div className="flex flex-col gap-1.5">
                      <label className="flex items-center justify-between gap-2 text-xs">
                        Subtotals
                        <Switch checked={pcfg.subtotals} onCheckedChange={(subtotals) => setP({ subtotals })} aria-label="Subtotals" />
                      </label>
                      <label className="flex items-center justify-between gap-2 text-xs">
                        Grand totals
                        <Switch checked={pcfg.grandTotals} onCheckedChange={(grandTotals) => setP({ grandTotals })} aria-label="Grand totals" />
                      </label>
                      <div className="flex items-center gap-1">
                        <select aria-label="Sort rows by" value={pcfg.sortBy} onChange={(e) => setP({ sortBy: e.target.value as "label" | "value" })} className={`${sel} flex-1`}>
                          <option value="label">Sort by label</option>
                          <option value="value">Sort by first value</option>
                        </select>
                        <select aria-label="Pivot sort direction" value={pcfg.sortDir} onChange={(e) => setP({ sortDir: e.target.value as "asc" | "desc" })} className={`${sel} w-20`}>
                          <option value="asc">Asc</option>
                          <option value="desc">Desc</option>
                        </select>
                      </div>
                    </div>
                  </ConfigSection>
                </>
              )}
            </>
          ) : null}
        </>
      ) : (
        <>
          <ConfigSection title="Metrics" hint="Semantic metrics compile to grain-safe SQL; definitions come from the semantic layer.">
            <MultiPick label="Metrics" value={mcfg.metrics} onChange={(metrics) => setM({ metrics })} options={schemaQ.data?.metrics ?? []} placeholder="Add metric…" />
          </ConfigSection>
          <ConfigSection title="Dimensions">
            <div className="flex flex-col gap-1">
              {mcfg.dimensions.map((d, i) => (
                <div key={i} className="flex items-center gap-1">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">{d.name}</span>
                  <select
                    aria-label={`Time grain for ${d.name}`}
                    value={d.grain}
                    onChange={(e) => setM({ dimensions: mcfg.dimensions.map((x, j) => (j === i ? { ...x, grain: e.target.value } : x)) })}
                    className={`${sel} w-24`}
                  >
                    {GRAINS.map((g) => (
                      <option key={g} value={g}>
                        {g ? `by ${g}` : "no grain"}
                      </option>
                    ))}
                  </select>
                  <Button variant="ghost" size="icon-xs" aria-label={`Remove ${d.name}`} onClick={() => setM({ dimensions: mcfg.dimensions.filter((_, j) => j !== i) })}>
                    <X />
                  </Button>
                </div>
              ))}
              <select
                aria-label="Add dimension"
                value=""
                onChange={(e) => e.target.value && setM({ dimensions: [...mcfg.dimensions, { name: e.target.value, grain: "" }] })}
                className={sel}
              >
                <option value="">Add dimension…</option>
                {(schemaQ.data?.dimensions ?? [])
                  .filter((d) => !mcfg.dimensions.some((x) => x.name === d))
                  .map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
              </select>
              <p className="text-2xs text-fg-faint">Time grains apply to time dimensions only.</p>
            </div>
          </ConfigSection>
          <ConfigSection title="Filters">
            <FilterEditor
              filters={mcfg.filters}
              onChange={(filters) => setM({ filters })}
              fields={(schemaQ.data?.dimensions ?? []).map((d) => ({ name: d }))}
              fieldLabel="Dimension"
            />
          </ConfigSection>
          <ConfigSection title="Date window" hint="Leave empty for all time.">
            <div className="flex flex-col gap-1">
              <select aria-label="Time dimension" value={mcfg.timeDimension} onChange={(e) => setM({ timeDimension: e.target.value })} className={sel}>
                <option value="">Metric&apos;s default time dimension</option>
                {(schemaQ.data?.dimensions ?? []).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <div className="flex items-center gap-1">
                <input type="date" aria-label="Start date" value={mcfg.start} onChange={(e) => setM({ start: e.target.value })} className={`${inp} flex-1`} />
                <span className="text-xs text-fg-subtle">to</span>
                <input type="date" aria-label="End date" value={mcfg.end} onChange={(e) => setM({ end: e.target.value })} className={`${inp} flex-1`} />
              </div>
            </div>
          </ConfigSection>
        </>
      )}
      <div className="sticky bottom-0 flex items-center gap-2 border-t border-border bg-bg px-3 py-2">
        <Button variant="primary" size="sm" onClick={run} disabled={!canRun || active.isPending} className="flex-1">
          {active.isPending ? <Spinner className="text-accent-fg" /> : <Play />} Run
        </Button>
        <span className="text-2xs text-fg-faint">⌘↵</span>
      </div>
    </div>
  );

  const resultArea = active.isPending ? (
    <LoadingState variant="block" label="Running" />
  ) : active.error ? (
    <QueryErrorView error={active.error} />
  ) : !result ? (
    <EmptyState
      icon={source === "metrics" ? Sigma : TableIcon}
      title={
        source === "metrics"
          ? "Pick one or more metrics"
          : !tcfg.table
            ? "Choose a table to explore"
            : mode === "pivot"
              ? "Add rows or columns and at least one value"
              : "Configure and run"
      }
      description={mode === "pivot" ? "Pivot subtotals are computed exactly from server-side partial aggregates." : "Results, charts and the generated SQL appear here."}
    />
  ) : mode === "pivot" && source === "table" && pivot ? (
    <div className="flex h-full min-h-0 flex-col">
      {result.truncated ? (
        <div className="shrink-0 border-b border-border bg-warning-soft px-3 py-1 text-xs text-warning" role="status">
          The grouped result was truncated at {formatInt(result.rows.length)} groups; totals cover only the groups shown. Add filters or fewer fields.
        </div>
      ) : null}
      {pivotShape &&
      (pivotShape.rows.join() !== pcfg.rows.join() || pivotShape.cols.join() !== pcfg.cols.join() || JSON.stringify(pivotShape.values) !== JSON.stringify(pcfg.values)) ? (
        <div className="shrink-0 border-b border-border bg-bg-subtle px-3 py-1 text-xs text-fg-subtle" role="status">
          Fields changed since the last run. Press Run to refresh the pivot.
        </div>
      ) : null}
      <PivotTable
        pivot={pivot}
        rowFields={pivotShape?.rows ?? []}
        colFields={pivotShape?.cols ?? []}
        values={pivotShape?.values ?? []}
        className="min-h-0 flex-1 p-2"
      />
    </div>
  ) : (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-2">
        <Segmented
          aria-label="Result view"
          value={view}
          onChange={setView}
          options={[
            { value: "grid", label: (<><TableIcon /> Table</>) },
            { value: "chart", label: (<><BarChart3 /> Chart</>) },
          ]}
        />
        <span className="text-xs text-fg-subtle tabular">
          {formatInt(result.row_count)} rows · {formatDuration(result.elapsed_ms)}
        </span>
        {result.truncated ? <span className="text-xs text-warning">Truncated</span> : null}
        {source === "metrics" && metricRun.data?.compiled.warnings?.length ? (
          <span className="truncate text-xs text-warning" title={metricRun.data.compiled.warnings.join("\n")}>
            {metricRun.data.compiled.warnings[0]}
          </span>
        ) : null}
      </div>
      <div ref={bodyRef} className="relative min-h-0 flex-1">
        {view === "grid" ? (
          <DataGrid columns={result.columns} rows={result.rows} className="absolute inset-0" />
        ) : (
          <div className="absolute inset-0 overflow-auto p-2">
            <ChartView
              result={result}
              config={chart ?? undefined}
              onConfigChange={setChart}
              height={Math.max(bodyHeight - 80, 200)}
              provenance={{
                sql: sql ?? null,
                dataset: source === "table" ? tcfg.table : null,
                metric: source === "metrics" ? mcfg.metrics.join(", ") : null,
                metric_version:
                  source === "metrics" && metricRun.data?.compiled.metric_versions
                    ? Object.entries(metricRun.data.compiled.metric_versions).map(([k, v]) => `${k} v${v}`).join(", ")
                    : null,
                dimensions: source === "metrics" ? buildMetricQuery(mcfg).dimensions : tcfg.groupBy,
                filters: normalizeFilterContext(filterContext),
                sort: tcfg.sort && source === "table" ? `${tcfg.sort.field} ${tcfg.sort.desc ? "desc" : "asc"}` : null,
              }}
              showFilters={false}
            />
          </div>
        )}
      </div>
    </div>
  );

  return (
    <Page>
      <PageHeader
        title="Explore"
        description="Filter, group, aggregate, pivot and chart without SQL. The generated SQL is always shown."
        actions={
          <>
            <Button size="sm" onClick={openInSql} disabled={!sql}>
              <FileCode2 /> Open in SQL editor
            </Button>
            <Button size="sm" onClick={() => setSaveOpen(true)} disabled={!sql || !canEdit}>
              <Save /> Save as query
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="max-h-[45vh] shrink-0 overflow-y-auto border-b border-border bg-bg-subtle scrollbar-thin md:max-h-none md:w-72 md:border-r md:border-b-0" aria-label="Explorer configuration">
          {config}
        </aside>
        <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Explorer result">
          <div className="shrink-0 border-b border-border px-3 py-1.5">
            <FilterChips items={filterContext} />
          </div>
          <div className="min-h-[240px] flex-1">{resultArea}</div>
          {sql ? (
            <details className="shrink-0 border-t border-border">
              <summary className="cursor-pointer px-3 py-1.5 text-xs text-fg-subtle select-none">Generated SQL (read-only)</summary>
              <CodeBlock code={sql} maxHeight={200} className="mx-3 mb-2" />
            </details>
          ) : null}
        </section>
      </div>
      <SaveQueryDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        initial={{
          name: source === "metrics" ? `${mcfg.metrics.join(", ")} explore` : `${tcfg.table} ${mode === "pivot" ? "pivot" : "explore"}`,
          description: "Saved from the Data Explorer.",
          tags: ["explore"],
        }}
        existing={false}
        pending={saveMutation.isPending}
        error={saveMutation.error}
        onSave={(v) => saveMutation.mutate(v)}
      />
    </Page>
  );
}

export default function ExplorePage() {
  return (
    <Suspense fallback={<LoadingState variant="block" label="Loading explorer" />}>
      <Explorer />
    </Suspense>
  );
}
