"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Play, Plus, Trash2, AlertTriangle, RefreshCw } from "lucide-react";
import type { AnalysisPlan, Investigation, PlanStepKind } from "@/lib/api/types";
import { dimensions as dimensionsApi, investigations, isJobAccepted, waitForJob } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { FilterChips } from "@/components/analysis/filter-chips";
import { Panel, SectionLabel } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { InlineError } from "@/components/states/states";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { addStep, moveStep, planProblems, removeStep, STEP_KINDS, toggleStep, updateStep } from "./plan";
import { candidateInfo, contextChips, periodCandidateLabel, windowLabel } from "./model";

const INTENT_LABEL: Record<string, string> = {
  why_change: "Explain a change",
  compare: "Compare",
  breakdown: "Break down",
  trend: "Trend",
  lookup: "Look up a value",
  forecast: "Forecast",
  anomaly: "Anomaly",
};

function Interpretation({ inv, onResolve, resolving }: { inv: Investigation; onResolve: (term: string, metricId: string) => void; resolving: boolean }) {
  const i = inv.interpretation;
  if (!i) return null;
  return (
    <Panel title="How AnalystOS read your question" description={inv.question}>
      <div className="flex flex-col gap-3 p-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="accent">{INTENT_LABEL[i.intent] ?? humanize(i.intent)}</Badge>
          {i.metric_ids.map((m) => (
            <Badge key={m} tone="outline">
              {i.resolved_terms && Object.entries(i.resolved_terms).find(([, v]) => v === m)?.[0]
                ? `“${Object.entries(i.resolved_terms).find(([, v]) => v === m)![0]}” → `
                : ""}
              {humanize(m)}
            </Badge>
          ))}
          {i.comparison_kind && i.comparison_kind !== "none" ? (
            <span className="text-xs text-fg-subtle">
              compared {i.comparison_kind === "yoy" ? "year over year" : i.comparison_kind === "pop" ? "with the previous period" : i.comparison_kind === "segment" ? "vs segment" : `with ${i.comparison_kind}`}
            </span>
          ) : null}
        </div>
        <FilterChips items={contextChips(i)} />
        {i.segment_comparison ? (
          <p className="text-xs text-fg-muted">
            Segment comparison: {humanize(i.segment_comparison.dimension)} = {i.segment_comparison.current} against{" "}
            {i.segment_comparison.baseline}, same period.
          </p>
        ) : null}
        {i.ranking ? <p className="text-xs text-fg-muted">Segments are ranked by {i.ranking} rate, not by share of the change.</p> : null}
        {i.premises?.length ? (
          <div className="rounded border border-border bg-bg-subtle px-3 py-2" role="group" aria-label="Premises">
            <div className="text-xs font-medium">Your question assumes</div>
            <ul className="mt-1 flex flex-col gap-0.5 text-sm">
              {i.premises.map((p, k) => (
                <li key={k}>
                  “{p.text}”{" "}
                  <span className="text-xs text-fg-subtle">
                    ({humanize(p.metric_id ?? p.term)} {p.expectation === "flat" ? `within ±${Math.round((p.tolerance ?? 0.02) * 100)}%` : p.expectation}); checked
                    against the data, not taken as given
                  </span>
                </li>
              ))}
            </ul>
            {i.period_defaulted ? (
              <p className="mt-1 text-xs text-fg-muted">
                No period was named, so AnalystOS chose {windowLabel(i.window)} vs {windowLabel(i.baseline)}
                {i.period_candidates?.length ? " from these candidates:" : "."}
              </p>
            ) : null}
            {i.period_defaulted && i.period_candidates?.length ? (
              <ul className="mt-0.5 list-disc pl-5 text-xs text-fg-subtle">
                {i.period_candidates.map((c, k) => (
                  <li key={k} className={c.holds ? "" : "text-fg-faint"}>
                    {periodCandidateLabel(c)}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : i.period_defaulted ? (
          <p className="text-xs text-fg-muted">No period was named; using {windowLabel(i.window)}.</p>
        ) : null}
        {i.ambiguous.length ? (
          <div className="flex flex-col gap-2" role="group" aria-label="Ambiguous terms">
            {i.ambiguous.map((a) => (
              <div key={a.term} className="rounded border border-warning/40 bg-warning-soft px-3 py-2">
                <div className="flex items-center gap-1.5 text-sm font-medium text-warning">
                  <AlertTriangle className="size-3.5" /> “{a.term}” matches several metrics. Choose one to continue.
                </div>
                {a.reason ? <p className="mt-0.5 text-xs text-fg-muted">{a.reason}</p> : null}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {a.candidates.map((c) => {
                    const info = candidateInfo(c);
                    return (
                      <Button key={info.metric_id} size="xs" disabled={resolving} onClick={() => onResolve(a.term, info.metric_id)} title={info.description || undefined}>
                        {info.label}
                        {a.llm_suggestion === info.metric_id ? <span className="text-2xs text-fg-subtle">(model suggestion)</span> : null}
                      </Button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {i.unresolved_filters?.length ? (
          <div className="text-xs text-warning">
            {i.unresolved_filters.map((u, k) => (
              <p key={k}>
                Not applied: “{u.text}” ({u.reason})
              </p>
            ))}
          </div>
        ) : null}
        {i.confidence_notes.length ? (
          <ul className="list-disc pl-4 text-xs text-fg-subtle">
            {i.confidence_notes.map((n, k) => (
              <li key={k}>{n}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </Panel>
  );
}

/**
 * Plan approval editor (spec §53): shows the interpretation and the analysis plan before an
 * expensive investigation runs. Steps can be toggled, edited, reordered, removed and added.
 */
export function PlanEditor({ investigation, canEdit }: { investigation: Investigation; canEdit: boolean }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const [plan, setPlan] = React.useState<AnalysisPlan | null>(investigation.plan ?? null);
  const [dirty, setDirty] = React.useState(false);
  const [newKind, setNewKind] = React.useState<PlanStepKind>("contribution");
  const [newDim, setNewDim] = React.useState("");
  const dims = useQuery({ queryKey: qk.dimensions(ws), queryFn: () => dimensionsApi.list(ws) });
  const catDims = (dims.data ?? []).filter((d) => d.type !== "time");

  React.useEffect(() => {
    if (!dirty) setPlan(investigation.plan ?? null);
  }, [investigation.plan, dirty]);

  const setInv = (inv: Investigation) => qc.setQueryData(qk.investigation(ws, inv.id), inv);

  const resolve = useMutation({
    mutationFn: ({ term, metricId }: { term: string; metricId: string }) =>
      investigations.disambiguate(ws, investigation.id, { [term]: metricId }),
    onSuccess: (inv) => {
      setDirty(false);
      setInv(inv);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const replan = useMutation({
    mutationFn: () => investigations.replan(ws, investigation.id),
    onSuccess: (inv) => {
      setDirty(false);
      setInv(inv);
    },
  });

  const run = useMutation({
    mutationFn: async () => {
      let inv = investigation;
      if (dirty && plan) inv = await investigations.updatePlan(ws, investigation.id, plan);
      const res = await investigations.run(ws, inv.id);
      if (isJobAccepted(res)) {
        await waitForJob(res);
        return investigations.get(ws, inv.id);
      }
      return res;
    },
    onSuccess: (inv) => {
      setDirty(false);
      setInv(inv);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "investigations"] });
    },
  });

  const edit = (p: AnalysisPlan) => {
    setPlan(p);
    setDirty(true);
  };

  const ambiguous = (investigation.interpretation?.ambiguous.length ?? 0) > 0;
  const problems = plan ? planProblems(plan) : ["No plan yet."];

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 p-4">
      <Interpretation inv={investigation} onResolve={(term, metricId) => resolve.mutate({ term, metricId })} resolving={resolve.isPending} />
      <InlineError error={resolve.error} />
      {ambiguous ? null : (
        <Panel
          title="Analysis plan"
          description={
            plan?.requires_approval
              ? "Review before running. This investigation will execute several queries."
              : "Lightweight plan. It runs immediately."
          }
          actions={
            <Button size="xs" variant="ghost" onClick={() => replan.mutate()} disabled={!canEdit || replan.isPending}>
              <RefreshCw /> Re-plan
            </Button>
          }
        >
          {!plan ? (
            <div className="p-4 text-sm text-fg-subtle">
              No plan has been generated yet.{" "}
              <Button size="xs" onClick={() => replan.mutate()} disabled={!canEdit || replan.isPending}>
                Generate plan
              </Button>
            </div>
          ) : (
            <div className="flex flex-col">
              {plan.approval_reasons?.length ? (
                <ul className="border-b border-border bg-bg-subtle px-3 py-2 text-xs text-fg-muted">
                  {plan.approval_reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : null}
              <ol className="flex flex-col" aria-label="Plan steps">
                {plan.steps.map((s, idx) => (
                  <li key={s.id} className={cn("flex gap-3 border-b border-border px-3 py-2.5", !s.enabled && "bg-bg-subtle")}>
                    <div className="flex flex-col items-center gap-1 pt-0.5">
                      <Checkbox
                        checked={s.enabled}
                        onCheckedChange={() => edit(toggleStep(plan, s.id))}
                        aria-label={`${s.enabled ? "Disable" : "Enable"} step ${s.title}`}
                        disabled={!canEdit}
                      />
                      <span className="font-mono text-2xs text-fg-faint">{idx + 1}</span>
                    </div>
                    <div className={cn("min-w-0 flex-1", !s.enabled && "opacity-60")}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Input
                          value={s.title}
                          onChange={(e) => edit(updateStep(plan, s.id, { title: e.target.value }))}
                          aria-label="Step title"
                          className="h-6 max-w-md border-transparent px-1 font-medium hover:border-border focus:border-border-strong"
                          disabled={!canEdit}
                        />
                        <Badge tone="neutral">{STEP_KINDS.find((k) => k.kind === s.kind)?.label ?? s.kind}</Badge>
                        {s.origin ? <Badge tone="outline">{humanize(s.origin)}</Badge> : null}
                        {s.estimated_queries ? <span className="text-2xs text-fg-subtle tabular">~{s.estimated_queries} queries</span> : null}
                      </div>
                      {s.rationale ? <p className="mt-0.5 px-1 text-xs text-fg-subtle">{s.rationale}</p> : null}
                      {s.kind === "contribution" || s.kind === "segment" ? (
                        <label className="mt-1.5 flex items-center gap-2 px-1 text-xs">
                          <span className="text-fg-subtle">Dimension</span>
                          <NativeSelect
                            className="h-6 w-48 text-xs"
                            value={String(s.params.dimension ?? "")}
                            onChange={(e) => edit(updateStep(plan, s.id, { params: { dimension: e.target.value } }))}
                            disabled={!canEdit}
                          >
                            <option value="">Choose…</option>
                            {catDims.map((d) => (
                              <option key={d.name} value={d.name}>
                                {d.label || humanize(d.name)}
                              </option>
                            ))}
                            {s.params.dimension && !catDims.some((d) => d.name === s.params.dimension) ? (
                              <option value={String(s.params.dimension)}>{String(s.params.dimension)}</option>
                            ) : null}
                          </NativeSelect>
                        </label>
                      ) : null}
                      {s.kind === "custom_sql" || s.kind === "python" ? (
                        <Textarea
                          className="mt-1.5 font-mono text-xs"
                          rows={4}
                          placeholder={s.kind === "custom_sql" ? "SELECT … (read-only)" : "# result = con.sql('…').df()"}
                          value={String((s.kind === "custom_sql" ? s.params.sql : s.params.code) ?? "")}
                          onChange={(e) =>
                            edit(updateStep(plan, s.id, { params: s.kind === "custom_sql" ? { sql: e.target.value } : { code: e.target.value } }))
                          }
                          aria-label={s.kind === "custom_sql" ? "Step SQL" : "Step Python"}
                          disabled={!canEdit}
                        />
                      ) : null}
                    </div>
                    <div className="flex shrink-0 items-start gap-0.5">
                      <Button size="icon-xs" variant="ghost" aria-label="Move step up" onClick={() => edit(moveStep(plan, s.id, -1))} disabled={!canEdit || idx === 0}>
                        <ArrowUp />
                      </Button>
                      <Button size="icon-xs" variant="ghost" aria-label="Move step down" onClick={() => edit(moveStep(plan, s.id, 1))} disabled={!canEdit || idx === plan.steps.length - 1}>
                        <ArrowDown />
                      </Button>
                      <Button size="icon-xs" variant="danger-ghost" aria-label={`Remove step ${s.title}`} onClick={() => edit(removeStep(plan, s.id))} disabled={!canEdit}>
                        <Trash2 />
                      </Button>
                    </div>
                  </li>
                ))}
              </ol>
              {canEdit ? (
                <div className="flex flex-wrap items-end gap-2 px-3 py-2.5">
                  <label className="flex flex-col gap-1 text-xs text-fg-subtle">
                    Add step
                    <NativeSelect className="w-40" value={newKind} onChange={(e) => setNewKind(e.target.value as PlanStepKind)}>
                      {STEP_KINDS.map((k) => (
                        <option key={k.kind} value={k.kind}>
                          {k.label}
                        </option>
                      ))}
                    </NativeSelect>
                  </label>
                  {newKind === "contribution" || newKind === "segment" ? (
                    <label className="flex flex-col gap-1 text-xs text-fg-subtle">
                      Dimension
                      <NativeSelect className="w-44" value={newDim} onChange={(e) => setNewDim(e.target.value)}>
                        <option value="">Choose…</option>
                        {catDims.map((d) => (
                          <option key={d.name} value={d.name}>
                            {d.label || humanize(d.name)}
                          </option>
                        ))}
                      </NativeSelect>
                    </label>
                  ) : null}
                  <Button
                    onClick={() => {
                      edit(addStep(plan, newKind, newDim && (newKind === "contribution" || newKind === "segment") ? { dimension: newDim } : {}));
                      setNewDim("");
                    }}
                  >
                    <Plus /> Add
                  </Button>
                  <p className="basis-full text-2xs text-fg-subtle">{STEP_KINDS.find((k) => k.kind === newKind)?.description}</p>
                </div>
              ) : null}
            </div>
          )}
        </Panel>
      )}
      {plan?.notes?.length ? (
        <div>
          <SectionLabel className="mb-1">Planner notes</SectionLabel>
          <ul className="list-disc pl-4 text-xs text-fg-subtle">
            {plan.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {!ambiguous ? (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {problems.length && plan ? <span className="text-xs text-warning">{problems.join(" ")}</span> : null}
          {dirty ? <span className="text-xs text-fg-subtle">Edited plan will be saved when you run it.</span> : null}
          <Button variant="primary" size="md" onClick={() => run.mutate()} disabled={!canEdit || !plan || problems.length > 0 || run.isPending}>
            <Play /> {run.isPending ? "Running investigation…" : `Run ${plan ? plan.steps.filter((s) => s.enabled).length : 0} steps`}
          </Button>
        </div>
      ) : null}
      <InlineError error={run.error ?? replan.error} />
    </div>
  );
}
