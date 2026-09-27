"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ListPlus, RefreshCw, X } from "lucide-react";
import { artifacts as artifactsApi, explore, findings as findingsApi, metrics as metricsApi, queries as queriesApi, semantic } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { ChartConfig, Finding, QueryResult } from "@/lib/api/types";
import type { ReportBlockSpec, SourceItem, SummaryEntry } from "@/lib/api/resources/outputs";
import { useOptionalWorkspace, useWorkspace } from "@/components/providers/workspace";
import { Statement } from "@/components/analysis/statement";
import { EvidenceBadge } from "@/components/analysis/evidence";
import { FilterChips } from "@/components/analysis/filter-chips";
import { StatusBadge } from "@/components/analysis/status";
import { ChartView } from "@/components/charts/chart-view";
import { EChart } from "@/components/charts/echart";
import { Kpi } from "@/components/charts/kpi";
import { DataGrid } from "@/components/data-grid/data-grid";
import { CodeBlock } from "@/components/code/code-block";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";
import { suggestChart } from "@/lib/charts/suggest";
import { humanize, toNumber } from "@/lib/format";
import { Markdown } from "./markdown";
import { buildSummaryBlock, collectSources, entryText, monthLabel, snapshotFinding } from "./model";

const SECTION_META = [
  { key: "observations", type: "observation", title: "Observations", hint: "Measured facts." },
  { key: "supported_explanations", type: "supported_explanation", title: "Supported explanations", hint: "Backed by executed analytical tests." },
  { key: "hypotheses", type: "hypothesis", title: "Open hypotheses", hint: "Not confirmed. Do not present as fact." },
] as const;

/* ------------------------------------------------------------------ read views */

function FindingBlockView({ block }: { block: ReportBlockSpec }) {
  const ws = useOptionalWorkspace();
  const live = useQuery({
    queryKey: ws && block.finding_id ? qk.finding(ws.id, block.finding_id) : ["finding-none"],
    queryFn: () => findingsApi.get(ws!.id, block.finding_id!),
    enabled: !!ws && !!block.finding_id,
    retry: false,
  });
  const f = live.data ?? null;
  const snap = block.snapshot;
  const statement = f?.statement ?? snap?.statement;
  if (!statement) {
    return (
      <p className="text-sm text-fg-subtle italic">
        {live.isLoading ? "Loading finding…" : "The referenced finding is no longer available."}
      </p>
    );
  }
  const type = f?.statement_type ?? snap?.statement_type ?? "observation";
  const status = f?.status ?? snap?.status;
  const changed = f && snap && snap.statement !== f.statement;
  return (
    <div className="flex flex-col gap-1.5" data-testid="block-finding">
      <Statement type={type} size="lg">
        {statement}
      </Statement>
      <div className="flex flex-wrap items-center gap-1.5 pl-3">
        <EvidenceBadge strength={f?.evidence_strength ?? snap?.evidence_strength ?? "hypothesis_only"} reasons={f?.evidence_reasons ?? snap?.evidence_reasons ?? []} />
        {status ? <StatusBadge status={status} /> : null}
        {status && status !== "confirmed" ? (
          <span className="inline-flex items-center gap-1 text-2xs text-warning no-print">
            <AlertTriangle className="size-3" /> Not a confirmed finding
          </span>
        ) : null}
        {ws && block.finding_id ? (
          <Link href={ws.href(`/findings/${block.finding_id}`)} className="text-2xs text-accent hover:underline no-print">
            Evidence and lineage
          </Link>
        ) : null}
      </div>
      <FilterChips items={f?.filter_context ?? snap?.filter_context ?? []} size="xs" className="pl-3" />
      {changed ? (
        <p className="pl-3 text-2xs text-warning no-print">The finding has been edited since it was added; the current statement is shown.</p>
      ) : null}
    </div>
  );
}

function SummaryView({ block }: { block: ReportBlockSpec }) {
  const sections = SECTION_META.map((s) => ({ ...s, items: (block[s.key] ?? []) as SummaryEntry[] }));
  const empty = sections.every((s) => !s.items.length);
  if (empty)
    return <p className="text-sm text-fg-subtle italic">No confirmed findings yet. Confirm findings, then regenerate the summary.</p>;
  return (
    <div className="flex flex-col gap-3" data-testid="block-summary">
      {sections.map((s) =>
        s.items.length ? (
          <section key={s.key} aria-label={s.title} data-section={s.type}>
            <h4 className="mb-1 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{s.title}</h4>
            <ul className="flex flex-col gap-1.5">
              {s.items.map((it, i) => (
                <li key={i} className="flex flex-wrap items-start gap-2">
                  <Statement type={s.type} showBadge={false} className="min-w-0 flex-1">
                    {entryText(it)}
                  </Statement>
                  {typeof it !== "string" && it.evidence_strength ? (
                    <EvidenceBadge strength={it.evidence_strength} interactive={false} />
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null,
      )}
    </div>
  );
}

function SourcesView({ items }: { items: (SourceItem | string)[] }) {
  const ws = useOptionalWorkspace();
  if (!items.length) return <p className="text-sm text-fg-subtle italic">No sources listed.</p>;
  const route = (s: SourceItem) =>
    !s.ref_id || !ws
      ? null
      : s.kind === "finding"
        ? ws.href(`/findings/${s.ref_id}`)
        : s.kind === "investigation"
          ? ws.href(`/investigate/${s.ref_id}`)
          : s.kind === "metric"
            ? ws.href(`/metrics/${s.ref_id}`)
            : s.kind === "dataset"
              ? ws.href(`/data/${s.ref_id}`)
              : null;
  return (
    <ol className="flex list-decimal flex-col gap-0.5 pl-5 text-sm">
      {items.map((raw, i) => {
        const s: SourceItem = typeof raw === "string" ? { kind: "note", label: raw } : raw;
        const href = route(s);
        return (
          <li key={i}>
            <span className="text-2xs tracking-wide text-fg-subtle uppercase">{humanize(s.kind)}</span>{" "}
            {href ? (
              <Link href={href} className="hover:text-accent hover:underline">
                {s.label}
              </Link>
            ) : (
              s.label
            )}
            {s.version !== undefined && s.version !== null && s.version !== "" ? (
              <span className="ml-1 font-mono text-xs text-fg-subtle">v{String(s.version)}</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/** Read-only rendering of one block; used by the reading view, print and the editor preview. */
export function BlockView({ block }: { block: ReportBlockSpec }) {
  switch (block.type) {
    case "heading":
      return <h2 className="text-lg font-semibold tracking-tight">{block.text || block.title || "Untitled section"}</h2>;
    case "narrative":
      return block.markdown?.trim() ? <Markdown>{block.markdown}</Markdown> : <p className="text-sm text-fg-subtle italic">Empty narrative.</p>;
    case "methodology":
      return (
        <div>
          <h3 className="mb-1 text-sm font-semibold">{block.title || "Methodology"}</h3>
          {block.markdown?.trim() ? <Markdown>{block.markdown}</Markdown> : <p className="text-sm text-fg-subtle italic">No methodology written.</p>}
          {block.sql ? <CodeBlock code={block.sql} className="mt-2" maxHeight={220} /> : null}
        </div>
      );
    case "finding":
      return <FindingBlockView block={block} />;
    case "kpi":
      return (
        <div className="flex flex-wrap items-end gap-4" data-testid="block-kpi">
          <Kpi
            size="lg"
            label={`${block.label || block.title || block.metric_id || "KPI"}${block.period ? ` · ${monthLabel(block.period)}` : ""}`}
            value={block.value ?? null}
            baseline={block.baseline}
            pctChange={block.pct_change}
            format={block.format}
            comparisonLabel="vs prior period"
          />
          {block.sql ? (
            <details className="min-w-0 flex-1 text-xs no-print">
              <summary className="cursor-pointer text-fg-subtle">Query</summary>
              <CodeBlock code={block.sql} className="mt-1" maxHeight={200} />
            </details>
          ) : null}
        </div>
      );
    case "chart": {
      const title = block.title ?? undefined;
      if (block.result && block.result.columns?.length) {
        return (
          <figure data-testid="block-chart" data-capture-key={block.id} data-capture-title={title ?? "chart"}>
            {title ? <figcaption className="mb-1 text-sm font-semibold">{title}</figcaption> : null}
            <ChartView result={block.result} config={block.config ?? null} provenance={block.provenance ?? undefined} title={title} height={280} toolbar={false} />
          </figure>
        );
      }
      if (block.option) {
        return (
          <figure data-testid="block-chart" data-capture-key={block.id} data-capture-title={title ?? "chart"}>
            {title ? <figcaption className="mb-1 text-sm font-semibold">{title}</figcaption> : null}
            <div style={{ height: 280 }}>
              <EChart option={block.option as never} ariaLabel={`Chart: ${title ?? "report chart"}`} />
            </div>
            <FilterChips items={block.provenance?.filters ?? []} size="xs" className="mt-1" />
          </figure>
        );
      }
      return <p className="text-sm text-fg-subtle italic">Chart has no data.</p>;
    }
    case "table":
      return block.result ? (
        <figure data-testid="block-table">
          {block.title ? <figcaption className="mb-1 text-sm font-semibold">{block.title}</figcaption> : null}
          <DataGrid
            columns={block.result.columns}
            rows={block.result.rows}
            height={Math.min(360, 34 + block.result.rows.length * 26)}
            className="rounded border border-border"
          />
          <FilterChips items={block.provenance?.filters ?? []} size="xs" className="mt-1" />
        </figure>
      ) : (
        <p className="text-sm text-fg-subtle italic">Table has no data.</p>
      );
    case "sources":
      return (
        <div>
          <h3 className="mb-1 text-sm font-semibold">{block.title || "Sources"}</h3>
          <SourcesView items={block.items ?? []} />
        </div>
      );
    case "summary":
      return (
        <div>
          <h3 className="mb-2 text-sm font-semibold">{block.title || "Executive summary"}</h3>
          <SummaryView block={block} />
        </div>
      );
    case "open_questions": {
      const items = (block.items ?? []).map((x) => (typeof x === "string" ? x : x.label)).filter(Boolean);
      return (
        <div>
          <h3 className="mb-1 text-sm font-semibold">{block.title || "Open questions"}</h3>
          {items.length ? (
            <ul className="flex flex-col gap-1">
              {items.map((q, i) => (
                <li key={i}>
                  <Statement type="hypothesis" showBadge={false}>
                    {q}
                  </Statement>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-subtle italic">No open questions.</p>
          )}
        </div>
      );
    }
    default:
      return (
        <div>
          {block.title ? <h3 className="mb-1 text-sm font-semibold">{block.title}</h3> : null}
          {block.markdown ? <Markdown>{block.markdown}</Markdown> : block.text ? <p className="text-sm">{block.text}</p> : null}
          {block.items?.length ? <SourcesView items={block.items} /> : null}
          {block.result ? <DataGrid columns={block.result.columns} rows={block.result.rows} height={240} className="mt-1 rounded border border-border" /> : null}
        </div>
      );
  }
}

/* ------------------------------------------------------------------ editors */

function useFindingsList(enabled: boolean, status?: string) {
  const { id: ws } = useWorkspace();
  return useQuery({
    queryKey: qk.findings(ws, { status: status ?? null, for: "report" }),
    queryFn: () => findingsApi.list(ws, { status, limit: 200 }),
    enabled,
  });
}

function FindingEditor({ block, onChange }: { block: ReportBlockSpec; onChange: (p: Partial<ReportBlockSpec>) => void }) {
  const list = useFindingsList(true);
  return (
    <Field label="Finding" htmlFor={`${block.id}-finding`} hint="Confirmed findings are marked; unconfirmed findings are flagged in the report.">
      {list.isError ? (
        <InlineError error={list.error} />
      ) : (
        <NativeSelect
          id={`${block.id}-finding`}
          value={block.finding_id ?? ""}
          onChange={(e) => {
            const f = (list.data ?? []).find((x) => x.id === e.target.value);
            onChange(f ? { finding_id: f.id, snapshot: snapshotFinding(f) } : { finding_id: null, snapshot: null });
          }}
        >
          <option value="">{list.isLoading ? "Loading findings…" : list.data?.length ? "Choose a finding…" : "No findings yet"}</option>
          {(list.data ?? []).map((f) => (
            <option key={f.id} value={f.id}>
              {f.status === "confirmed" ? "✓ " : ""}
              {f.statement.length > 110 ? `${f.statement.slice(0, 110)}…` : f.statement}
            </option>
          ))}
        </NativeSelect>
      )}
    </Field>
  );
}

function firstNumeric(result: QueryResult): number | null {
  const row = result.rows[0] ?? [];
  for (let i = 0; i < result.columns.length; i++) {
    const n = toNumber(row[i]);
    if (n !== null) return n;
  }
  return null;
}

function KpiEditor({ block, onChange }: { block: ReportBlockSpec; onChange: (p: Partial<ReportBlockSpec>) => void }) {
  const { id: ws } = useWorkspace();
  const metrics = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  const [metricId, setMetricId] = React.useState(block.metric_id ?? "");
  const [period, setPeriod] = React.useState(block.period ?? "");
  const compute = useMutation({
    mutationFn: async () => {
      const res = (await semantic.resolvePeriod(ws, period)) as {
        window: { start: string; end: string; dimension?: string | null };
        previous_period?: { start: string; end: string } | null;
      };
      const q = (w: { start: string; end: string }) =>
        explore.metrics(ws, { metrics: [metricId], dimensions: [], filters: [], time: { start: w.start, end: w.end } });
      const cur = await q(res.window);
      const base = res.previous_period ? await q(res.previous_period) : null;
      const value = firstNumeric(cur.result);
      const baseline = base ? firstNumeric(base.result) : null;
      const m = metrics.data?.find((x) => x.id === metricId);
      return {
        metric_id: metricId,
        period,
        label: m?.label ?? metricId,
        format: m?.format ?? null,
        value,
        baseline,
        pct_change: value !== null && baseline ? (value - baseline) / Math.abs(baseline) : null,
        sql: cur.compiled?.sql ?? null,
      } satisfies Partial<ReportBlockSpec>;
    },
    onSuccess: (patch) => onChange(patch),
  });
  return (
    <div className="grid gap-2 sm:grid-cols-[1fr_140px_auto] sm:items-end">
      <Field label="Metric" htmlFor={`${block.id}-metric`}>
        <NativeSelect id={`${block.id}-metric`} value={metricId} onChange={(e) => setMetricId(e.target.value)}>
          <option value="">{metrics.isLoading ? "Loading…" : "Choose a metric…"}</option>
          {(metrics.data ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label || m.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Month" htmlFor={`${block.id}-period`}>
        <Input id={`${block.id}-period`} type="month" value={period} onChange={(e) => setPeriod(e.target.value)} />
      </Field>
      <Button variant="secondary" disabled={!metricId || !period || compute.isPending} onClick={() => compute.mutate()}>
        {compute.isPending ? <Spinner /> : <RefreshCw />} Compute
      </Button>
      {compute.error ? <InlineError error={compute.error} className="sm:col-span-3" /> : null}
    </div>
  );
}

function DataEditor({ block, onChange }: { block: ReportBlockSpec; onChange: (p: Partial<ReportBlockSpec>) => void }) {
  const { id: ws } = useWorkspace();
  const [source, setSource] = React.useState<"saved_query" | "artifact">(block.artifact_id ? "artifact" : "saved_query");
  const [ref, setRef] = React.useState("");
  const saved = useQuery({ queryKey: qk.savedQueries(ws), queryFn: () => queriesApi.saved(ws), enabled: source === "saved_query" });
  const arts = useQuery({
    queryKey: ["ws", ws, "artifacts", "for-reports"],
    queryFn: () => artifactsApi.list(ws, { limit: 100 }),
    enabled: source === "artifact",
  });
  const load = useMutation({
    mutationFn: async (): Promise<Partial<ReportBlockSpec>> => {
      if (source === "saved_query") {
        const q = saved.data?.find((x) => x.id === ref);
        if (!q) throw new Error("Choose a saved query");
        const run = await queriesApi.runSaved(ws, q.id, { limit: 500 });
        const result = run.result;
        const config: ChartConfig = (q.chart as ChartConfig | null) ?? suggestChart(result.columns, result.rows);
        return { title: block.title || q.name, result, config: block.type === "chart" ? config : null, sql: q.sql, provenance: { sql: q.sql, query_run_id: run.id } };
      }
      const a = await artifactsApi.get(ws, ref);
      const r = a.result as QueryResult | null | undefined;
      const result = r && Array.isArray((r as QueryResult).columns) ? (r as QueryResult) : null;
      return {
        title: block.title || a.title || "Artifact",
        artifact_id: a.id,
        result,
        option: result ? null : (a.chart_spec ?? null),
        config: result && block.type === "chart" ? suggestChart(result.columns, result.rows) : null,
        sql: a.sql ?? null,
        provenance: { sql: a.sql ?? null, artifact_id: a.id, filters: a.filter_context ?? [] },
      };
    },
    onSuccess: (patch) => onChange(patch),
  });
  const options =
    source === "saved_query"
      ? (saved.data ?? []).map((q) => ({ id: q.id, label: q.name }))
      : (arts.data ?? []).filter((a) => ["query", "chart", "metric_result", "dataframe"].includes(a.kind)).map((a) => ({ id: a.id, label: a.title || `${humanize(a.kind)} ${a.id.slice(0, 8)}` }));
  const loadingList = source === "saved_query" ? saved.isLoading : arts.isLoading;
  return (
    <div className="grid gap-2 sm:grid-cols-[160px_1fr_auto] sm:items-end">
      <Field label="Source" htmlFor={`${block.id}-src`}>
        <NativeSelect
          id={`${block.id}-src`}
          value={source}
          onChange={(e) => {
            setSource(e.target.value as "saved_query" | "artifact");
            setRef("");
          }}
        >
          <option value="saved_query">Saved query</option>
          <option value="artifact">Investigation artifact</option>
        </NativeSelect>
      </Field>
      <Field label={source === "saved_query" ? "Saved query" : "Artifact"} htmlFor={`${block.id}-ref`}>
        <NativeSelect id={`${block.id}-ref`} value={ref} onChange={(e) => setRef(e.target.value)}>
          <option value="">{loadingList ? "Loading…" : options.length ? "Choose…" : "Nothing available yet"}</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Button variant="secondary" disabled={!ref || load.isPending} onClick={() => load.mutate()}>
        {load.isPending ? <Spinner /> : <RefreshCw />} {block.result || block.option ? "Replace data" : "Load data"}
      </Button>
      {load.error ? <InlineError error={load.error} className="sm:col-span-3" /> : null}
      <Field label="Caption" htmlFor={`${block.id}-title`} className="sm:col-span-3">
        <Input id={`${block.id}-title`} value={block.title ?? ""} onChange={(e) => onChange({ title: e.target.value })} />
      </Field>
    </div>
  );
}

function ListEditor({
  block,
  onChange,
  placeholder,
}: {
  block: ReportBlockSpec;
  onChange: (p: Partial<ReportBlockSpec>) => void;
  placeholder: string;
}) {
  const text = (block.items ?? []).map((x) => (typeof x === "string" ? x : x.label)).join("\n");
  return (
    <Field label="One per line" htmlFor={`${block.id}-items`}>
      <Textarea
        id={`${block.id}-items`}
        rows={4}
        defaultValue={text}
        placeholder={placeholder}
        onBlur={(e) => onChange({ items: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })}
      />
    </Field>
  );
}

function SourcesEditor({
  block,
  allBlocks,
  onChange,
}: {
  block: ReportBlockSpec;
  allBlocks: ReportBlockSpec[];
  onChange: (p: Partial<ReportBlockSpec>) => void;
}) {
  const [label, setLabel] = React.useState("");
  const items = (block.items ?? []).map((x) => (typeof x === "string" ? { kind: "note", label: x } : x));
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        <Button
          size="xs"
          variant="secondary"
          onClick={() => {
            const found = collectSources(allBlocks.filter((b) => b.id !== block.id));
            const keys = new Set(items.map((i) => `${i.kind}:${i.ref_id ?? i.label}`));
            onChange({ items: [...items, ...found.filter((f) => !keys.has(`${f.kind}:${f.ref_id ?? f.label}`))] });
          }}
        >
          <ListPlus /> Collect from report
        </Button>
      </div>
      {items.length ? (
        <ul className="flex flex-col gap-0.5 text-sm">
          {items.map((s, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="w-20 shrink-0 text-2xs tracking-wide text-fg-subtle uppercase">{humanize(s.kind)}</span>
              <span className="min-w-0 flex-1 truncate">{s.label}</span>
              {s.version !== undefined && s.version !== null ? <span className="font-mono text-xs text-fg-subtle">v{String(s.version)}</span> : null}
              <Button variant="ghost" size="icon-xs" aria-label={`Remove source ${s.label}`} onClick={() => onChange({ items: items.filter((_, j) => j !== i) })}>
                <X />
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      <form
        className="flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (!label.trim()) return;
          onChange({ items: [...items, { kind: "reference", label: label.trim() }] });
          setLabel("");
        }}
      >
        <Input aria-label="Add a reference" placeholder="Add a reference (document, system, owner)…" value={label} onChange={(e) => setLabel(e.target.value)} />
        <Button type="submit" size="sm" variant="secondary" disabled={!label.trim()}>
          Add
        </Button>
      </form>
    </div>
  );
}

function SummaryEditor({
  block,
  allBlocks,
  onChange,
}: {
  block: ReportBlockSpec;
  allBlocks: ReportBlockSpec[];
  onChange: (p: Partial<ReportBlockSpec>) => void;
}) {
  const { id: ws } = useWorkspace();
  const referenced = allBlocks.filter((b) => b.type === "finding" && b.finding_id && !b.excluded).map((b) => b.finding_id!);
  const gen = useMutation({
    mutationFn: async () => {
      const confirmed: Finding[] = await findingsApi.list(ws, { status: "confirmed", limit: 500 });
      const scoped = referenced.length ? confirmed.filter((f) => referenced.includes(f.id)) : confirmed;
      return buildSummaryBlock(scoped, block.id);
    },
    onSuccess: (b) =>
      onChange({ observations: b.observations, supported_explanations: b.supported_explanations, hypotheses: b.hypotheses }),
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="xs" variant="secondary" onClick={() => gen.mutate()} disabled={gen.isPending}>
        {gen.isPending ? <Spinner /> : <RefreshCw />} Generate from confirmed findings
      </Button>
      <span className="text-xs text-fg-subtle">
        {referenced.length
          ? `Uses the ${referenced.length} finding${referenced.length === 1 ? "" : "s"} in this report that are confirmed.`
          : "Uses all confirmed findings in the workspace."}{" "}
        Drafts and rejected findings are never included.
      </span>
      <InlineError error={gen.error} />
    </div>
  );
}

/** Type-specific editor controls shown above the block preview in edit mode. */
export function BlockEditor({
  block,
  allBlocks,
  onChange,
}: {
  block: ReportBlockSpec;
  allBlocks: ReportBlockSpec[];
  onChange: (p: Partial<ReportBlockSpec>) => void;
}) {
  switch (block.type) {
    case "heading":
      return (
        <Input
          aria-label="Heading text"
          className="h-8 text-base font-semibold"
          value={block.text ?? ""}
          placeholder="Section heading"
          onChange={(e) => onChange({ text: e.target.value })}
        />
      );
    case "narrative":
    case "methodology":
      return (
        <Field
          label={block.type === "methodology" ? "Methodology (Markdown)" : "Narrative (Markdown)"}
          htmlFor={`${block.id}-md`}
          hint="Commentary is yours; numbers belong in finding and KPI blocks, which are backed by executed queries."
        >
          <Textarea
            id={`${block.id}-md`}
            rows={6}
            value={block.markdown ?? ""}
            onChange={(e) => onChange({ markdown: e.target.value })}
            className="font-mono text-xs"
          />
        </Field>
      );
    case "finding":
      return <FindingEditor block={block} onChange={onChange} />;
    case "kpi":
      return <KpiEditor block={block} onChange={onChange} />;
    case "chart":
    case "table":
      return <DataEditor block={block} onChange={onChange} />;
    case "sources":
      return <SourcesEditor block={block} allBlocks={allBlocks} onChange={onChange} />;
    case "summary":
      return <SummaryEditor block={block} allBlocks={allBlocks} onChange={onChange} />;
    case "open_questions":
      return <ListEditor block={block} onChange={onChange} placeholder="Why did Houston conversion fall in the Web channel?" />;
    default:
      return null;
  }
}

export function blockHasContent(b: ReportBlockSpec): boolean {
  switch (b.type) {
    case "heading":
      return !!b.text?.trim();
    case "narrative":
    case "methodology":
      return !!b.markdown?.trim();
    case "finding":
      return !!b.finding_id;
    case "kpi":
      return b.value !== undefined && b.value !== null;
    case "chart":
    case "table":
      return !!b.result || !!b.option;
    default:
      return true;
  }
}
