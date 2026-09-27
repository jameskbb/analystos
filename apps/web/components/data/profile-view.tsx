"use client";
import * as React from "react";
import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import type { ColumnStats, DatasetProfile, ProfileFinding } from "@/lib/api/resources/data";
import { Badge } from "@/components/ui/badge";
import { Panel, SectionLabel } from "@/components/shell/page";
import { EmptyState } from "@/components/states/states";
import { formatCell, formatDateTime, formatDuration, formatInt, formatValue, humanize, toNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

const SEV_ORDER: Record<string, number> = { error: 0, warning: 1, info: 2 };
const SEV_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};
const SEV_CLASS: Record<string, string> = { error: "text-negative", warning: "text-warning", info: "text-fg-subtle" };

export function sortIssues(issues: ProfileFinding[]): ProfileFinding[] {
  return [...issues].sort(
    (a, b) => (SEV_ORDER[a.severity] ?? 3) - (SEV_ORDER[b.severity] ?? 3) || (b.count ?? 0) - (a.count ?? 0),
  );
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  const n = v > 1 ? v : v * 100;
  return `${n < 0.1 && n > 0 ? "<0.1" : n.toFixed(n < 10 ? 1 : 0)}%`;
}

function fmtStat(v: unknown): string {
  if (v === null || v === undefined) return "n/a";
  const n = toNumber(v);
  if (n !== null && typeof v !== "string") return formatValue(n);
  return formatCell(v);
}

function Evidence({ ev }: { ev: Record<string, unknown> | undefined }) {
  if (!ev || !Object.keys(ev).length) return null;
  return (
    <dl className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-fg-subtle">
      {Object.entries(ev).slice(0, 8).map(([k, v]) => (
        <div key={k} className="flex gap-1">
          <dt>{humanize(k)}:</dt>
          <dd className="font-mono text-fg-muted">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function IssueList({ issues, onColumn }: { issues: ProfileFinding[]; onColumn?: (c: string) => void }) {
  if (!issues.length)
    return <p className="px-3 py-3 text-sm text-fg-subtle">No issues detected by profiling.</p>;
  return (
    <ul aria-label="Profile issues">
      {sortIssues(issues).map((i, idx) => {
        const Icon = SEV_ICON[i.severity] ?? Info;
        return (
          <li key={`${i.code}-${i.column}-${idx}`} className="flex gap-2 border-b border-border px-3 py-2 last:border-b-0">
            <Icon className={cn("mt-0.5 size-3.5 shrink-0", SEV_CLASS[i.severity])} aria-label={i.severity} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5 text-sm">
                <span className="font-medium">{humanize(i.code)}</span>
                {i.column ? (
                  onColumn ? (
                    <button type="button" className="font-mono text-xs text-accent hover:underline" onClick={() => onColumn(i.column!)}>
                      {i.column}
                    </button>
                  ) : (
                    <span className="font-mono text-xs">{i.column}</span>
                  )
                ) : null}
                {i.count != null ? <Badge tone="outline" className="tabular">{formatInt(i.count)} rows</Badge> : null}
              </div>
              <p className="text-sm text-fg-muted">{i.message}</p>
              <Evidence ev={i.evidence} />
              {i.sample_values?.length ? (
                <div className="mt-1 flex flex-wrap gap-1">
                  {i.sample_values.slice(0, 6).map((v, k) => (
                    <code key={k} className="rounded-sm bg-bg-muted px-1 font-mono text-2xs">
                      {v === null ? "NULL" : JSON.stringify(v)}
                    </code>
                  ))}
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Horizontal share bars for a column's most common values. */
export function TopValues({ col }: { col: ColumnStats }) {
  const tv = col.top_values ?? [];
  if (!tv.length) return <p className="text-xs text-fg-subtle">No frequency data.</p>;
  const rows = col.row_count ?? 0;
  const max = Math.max(...tv.map((t) => t.count), 1);
  return (
    <ul className="flex flex-col gap-1" aria-label={`Most common values of ${col.name}`}>
      {tv.slice(0, 12).map((t, i) => {
        const share = t.pct ?? (rows ? t.count / rows : null);
        return (
          <li key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(80px,2fr)_auto] items-center gap-2 text-xs">
            <span className="truncate font-mono" title={String(t.value)}>
              {t.value === null ? <span className="text-fg-faint italic">NULL</span> : String(t.value) === "" ? <span className="text-fg-faint italic">(empty)</span> : String(t.value)}
            </span>
            <span className="h-2 overflow-hidden rounded-sm bg-bg-muted" aria-hidden>
              <span className="block h-full rounded-sm bg-accent/70" style={{ width: `${(t.count / max) * 100}%` }} />
            </span>
            <span className="text-right text-fg-subtle tabular">
              {formatInt(t.count)} {share != null ? `· ${pct(share)}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Min/quartiles/max drawn on one axis, so skew and spread read at a glance. */
export function QuantileStrip({ col }: { col: ColumnStats }) {
  const q = col.quantiles ?? {};
  const get = (keys: string[]) => {
    for (const k of keys) {
      const v = toNumber(q[k]);
      if (v !== null) return v;
    }
    return null;
  };
  const min = toNumber(col.min);
  const max = toNumber(col.max);
  const q1 = get(["p25", "0.25", "q1", "25%"]);
  const med = toNumber(col.median) ?? get(["p50", "0.5", "50%"]);
  const q3 = get(["p75", "0.75", "q3", "75%"]);
  if (min === null || max === null || max === min) return null;
  const x = (v: number) => ((v - min) / (max - min)) * 100;
  return (
    <div>
      <svg viewBox="0 0 100 14" preserveAspectRatio="none" className="h-4 w-full" role="img" aria-label={`Distribution of ${col.name}: min ${min}, median ${med ?? "unknown"}, max ${max}`}>
        <line x1="0" x2="100" y1="7" y2="7" stroke="var(--border-strong)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {q1 !== null && q3 !== null ? (
          <rect x={x(q1)} y="2" width={Math.max(x(q3) - x(q1), 0.5)} height="10" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ) : null}
        {med !== null ? <line x1={x(med)} x2={x(med)} y1="1" y2="13" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" /> : null}
      </svg>
      <div className="flex justify-between text-2xs text-fg-subtle tabular">
        <span>{fmtStat(min)}</span>
        <span>median {fmtStat(med)}</span>
        <span>{fmtStat(max)}</span>
      </div>
    </div>
  );
}

function ColumnDetail({ col, issues }: { col: ColumnStats; issues: ProfileFinding[] }) {
  const stats: [string, React.ReactNode][] = [
    ["Storage type", <span key="t" className="font-mono">{col.type}</span>],
    ["Inferred type", col.inferred_type ?? "n/a"],
    ["Cardinality", humanize(col.cardinality ?? "")],
    ["Nulls", `${formatInt(col.null_count)} (${pct(col.null_pct)})`],
    ["Distinct", `${formatInt(col.distinct_count)}${col.distinct_pct != null ? ` (${pct(col.distinct_pct)})` : ""}`],
    ["Min", fmtStat(col.min)],
    ["Max", fmtStat(col.max)],
    ["Mean", fmtStat(col.mean)],
    ["Median", fmtStat(col.median)],
    ["Std dev", fmtStat(col.stddev)],
  ];
  if (col.min_length != null) stats.push(["Length", `${col.min_length}–${col.max_length}`]);
  if (col.zero_count != null) stats.push(["Zeros", formatInt(col.zero_count)]);
  if (col.negative_count != null) stats.push(["Negatives", formatInt(col.negative_count)]);
  const quantiles = Object.entries(col.quantiles ?? {}).filter(([, v]) => v !== null && v !== undefined);
  return (
    <div className="flex flex-col gap-4 p-3" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold">{col.name}</h3>
        {(col.semantic_roles ?? []).map((r) => (
          <Badge key={r} tone="accent">
            {humanize(r)}
          </Badge>
        ))}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-3 xl:grid-cols-4">
        {stats.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-fg-subtle">{k}</dt>
            <dd className="tabular">{v}</dd>
          </div>
        ))}
      </dl>
      <QuantileStrip col={col} />
      {quantiles.length ? (
        <div>
          <SectionLabel className="mb-1">Quantiles</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {quantiles.map(([k, v]) => (
              <span key={k} className="rounded-sm border border-border px-1.5 text-xs tabular">
                <span className="text-fg-subtle">{k}</span> {fmtStat(v)}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      <div>
        <SectionLabel className="mb-1">Most common values</SectionLabel>
        <TopValues col={col} />
      </div>
      {issues.length ? (
        <div>
          <SectionLabel className="mb-1">Issues in this column</SectionLabel>
          <div className="rounded border border-border">
            <IssueList issues={issues} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Data profile (spec §10): per-column statistics, semantic roles and issues with evidence. */
export function ProfileView({
  profile,
  initialColumn,
}: {
  profile: DatasetProfile;
  initialColumn?: string | null;
}) {
  const [selected, setSelected] = React.useState<string | null>(initialColumn ?? profile.columns[0]?.name ?? null);
  const issuesByCol = React.useMemo(() => {
    const m = new Map<string, ProfileFinding[]>();
    profile.issues.forEach((i) => {
      if (i.column) m.set(i.column, [...(m.get(i.column) ?? []), i]);
    });
    return m;
  }, [profile.issues]);
  const tableIssues = profile.issues;
  const col = profile.columns.find((c) => c.name === selected) ?? null;
  const sevCount = (s: string) => profile.issues.filter((i) => i.severity === s).length;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span>
          <span className="text-fg-subtle">Rows</span> <span className="font-medium tabular">{formatInt(profile.row_count)}</span>
        </span>
        <span>
          <span className="text-fg-subtle">Columns</span> <span className="font-medium tabular">{profile.column_count}</span>
        </span>
        <span>
          <span className="text-fg-subtle">Duplicate rows</span>{" "}
          <span className={cn("font-medium tabular", (profile.duplicate_row_count ?? 0) > 0 && "text-warning")}>
            {formatInt(profile.duplicate_row_count ?? 0)}
          </span>
        </span>
        <span>
          <span className="text-fg-subtle">Issues</span>{" "}
          <span className="font-medium">
            <span className="text-negative">{sevCount("error")} errors</span> ·{" "}
            <span className="text-warning">{sevCount("warning")} warnings</span> · {sevCount("info")} info
          </span>
        </span>
        {profile.profiled_at ? (
          <span className="text-fg-subtle">
            Profiled {formatDateTime(profile.profiled_at)}
            {profile.elapsed_ms ? ` in ${formatDuration(profile.elapsed_ms)}` : ""}
          </span>
        ) : null}
        {profile.sampled ? (
          <Badge tone="warning">Sampled: {formatInt(profile.sample_size ?? 0)} rows</Badge>
        ) : null}
      </div>

      <Panel title={`Issues (${tableIssues.length})`} description="Detected automatically; nothing is changed in the data.">
        <IssueList issues={tableIssues} onColumn={setSelected} />
      </Panel>

      <Panel title="Columns" bodyClassName="grid min-h-0 md:grid-cols-[minmax(260px,340px)_minmax(0,1fr)]">
        <ul className="max-h-[560px] overflow-auto border-b border-border scrollbar-thin md:border-r md:border-b-0" role="listbox" aria-label="Columns">
          {profile.columns.map((c) => {
            const nIssues = issuesByCol.get(c.name)?.length ?? 0;
            const nullShare = c.null_pct > 1 ? c.null_pct / 100 : c.null_pct;
            return (
              <li key={c.name} role="option" aria-selected={selected === c.name}>
                <button
                  type="button"
                  onClick={() => setSelected(c.name)}
                  className={cn(
                    "flex w-full flex-col gap-1 border-b border-border px-3 py-1.5 text-left hover:bg-bg-subtle",
                    selected === c.name && "bg-accent-soft hover:bg-accent-soft",
                  )}
                >
                  <span className="flex items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium">{c.name}</span>
                    {nIssues ? (
                      <span className="text-2xs text-warning" title={`${nIssues} issue(s)`}>
                        {nIssues} issue{nIssues === 1 ? "" : "s"}
                      </span>
                    ) : null}
                    <span className="font-mono text-2xs text-fg-faint">{c.inferred_type ?? c.type}</span>
                  </span>
                  <span className="flex items-center gap-2 text-2xs text-fg-subtle tabular">
                    <span className="h-1 w-14 overflow-hidden rounded-full bg-bg-muted" title={`${pct(c.null_pct)} null`}>
                      <span className="block h-full bg-warning" style={{ width: `${Math.min(nullShare * 100, 100)}%` }} />
                    </span>
                    {pct(c.null_pct)} null · {formatInt(c.distinct_count)} distinct
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        <div className="min-w-0">
          {col ? (
            <ColumnDetail col={col} issues={issuesByCol.get(col.name) ?? []} />
          ) : (
            <EmptyState compact title="Select a column" />
          )}
        </div>
      </Panel>
    </div>
  );
}
