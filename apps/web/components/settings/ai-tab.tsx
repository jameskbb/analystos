"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, Info, PlugZap, XCircle } from "lucide-react";
import { ai } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { AISettings, AIUsageBreakdown } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Segmented } from "@/components/ui/tabs";
import { EmptyState, ErrorState, InlineError, LoadingState } from "@/components/states/states";
import { Kpi } from "@/components/charts/kpi";
import { formatDateTime, formatDuration, formatInt, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RoleNotice, SettingsSection } from "./shared";

export function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "n/a";
  if (n > 0 && n < 0.01) return "<$0.01";
  return `$${n.toFixed(n >= 100 ? 0 : 2)}`;
}

function SettingsForm({ settings, isOwner }: { settings: AISettings; isOwner: boolean }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const [form, setForm] = React.useState<AISettings>(settings);
  const [apiKey, setApiKey] = React.useState("");
  React.useEffect(() => setForm(settings), [settings]);
  const set = (patch: Partial<AISettings>) => setForm((f) => ({ ...f, ...patch }));

  const save = useMutation({
    mutationFn: (clearKey?: boolean) =>
      ai.updateSettings(ws, {
        enabled: form.enabled,
        provider: form.provider,
        model_large: form.model_large,
        model_default: form.model_default,
        model_small: form.model_small,
        allow_result_samples: form.allow_result_samples,
        monthly_budget_usd: form.monthly_budget_usd ?? null,
        tool_loop: !!form.tool_loop,
        ...(clearKey ? { api_key: "" } : apiKey ? { api_key: apiKey } : {}),
      }),
    onSuccess: (s) => {
      qc.setQueryData(qk.aiSettings(ws), s);
      setApiKey("");
      toast.success("AI settings saved");
    },
  });
  const test = useMutation({ mutationFn: () => ai.test(ws) });
  const disabled = !isOwner;
  const keyStatus =
    form.key_source === "environment"
      ? "Using the server's ANTHROPIC_API_KEY environment variable"
      : form.has_api_key
        ? "A workspace key is stored (encrypted). Enter a new one to replace it."
        : "No key stored";

  return (
    <form
      className="flex max-w-xl flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(false);
      }}
    >
      <label className="flex items-start gap-2 text-sm">
        <Switch checked={form.enabled} disabled={disabled} onCheckedChange={(v) => set({ enabled: v })} aria-label="Enable AI assistance" className="mt-0.5" />
        <span>
          Enable AI assistance
          <span className="block text-xs text-fg-subtle">
            Disambiguation, SQL drafting with a bounded repair loop, extra plan steps and summary wording that is kept only
            when every number matches an executed result. Off means the deterministic engine only.
          </span>
        </span>
      </label>
      <label className="flex items-start gap-2 text-sm">
        <Switch
          checked={!!form.tool_loop}
          disabled={disabled || !form.enabled}
          onCheckedChange={(v) => set({ tool_loop: v })}
          aria-label="Allow the tool loop"
          className="mt-0.5"
        />
        <span>
          Allow the tool loop
          <span className="block text-xs text-fg-subtle">
            During an investigation the model may call a bounded number of read-only tools (inspect schema and metrics, run
            validated SQL). Tool results come from execution, never from the model.
          </span>
        </span>
      </label>
      {form.month_spend_usd !== undefined && form.month_spend_usd !== null ? (
        <p className={form.over_budget ? "text-xs text-negative" : "text-xs text-fg-subtle"} role={form.over_budget ? "alert" : undefined}>
          Estimated spend this month: ${form.month_spend_usd.toFixed(2)}
          {form.monthly_budget_usd ? ` of $${form.monthly_budget_usd}` : ""}
          {form.over_budget ? ". The budget is reached, so AI is paused until next month." : ""}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Provider" htmlFor="ai-provider">
          <NativeSelect id="ai-provider" value={form.provider} disabled={disabled} onChange={(e) => set({ provider: e.target.value })}>
            <option value="anthropic">Anthropic</option>
            <option value="none">None</option>
          </NativeSelect>
        </Field>
        <Field label="Monthly budget (USD)" htmlFor="ai-budget" hint="Calls stop when the estimated monthly cost reaches this.">
          <Input
            id="ai-budget"
            type="number"
            min={0}
            step="1"
            disabled={disabled}
            value={form.monthly_budget_usd ?? ""}
            placeholder="No limit"
            onChange={(e) => set({ monthly_budget_usd: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </Field>
      </div>
      <Field label="API key" htmlFor="ai-key" hint={keyStatus}>
        <div className="flex gap-2">
          <Input
            id="ai-key"
            type="password"
            autoComplete="off"
            disabled={disabled}
            value={apiKey}
            placeholder={form.has_api_key ? "••••••••••••  (stored)" : "sk-ant-…"}
            onChange={(e) => setApiKey(e.target.value)}
          />
          {form.has_api_key && form.key_source !== "environment" && isOwner ? (
            <Button variant="danger-ghost" onClick={() => save.mutate(true)} disabled={save.isPending}>
              Remove key
            </Button>
          ) : null}
        </div>
      </Field>
      <fieldset className="grid gap-3 sm:grid-cols-3">
        <legend className="mb-1 text-xs font-medium text-fg-muted">Model tiers</legend>
        <Field label="Large (planning, synthesis)" htmlFor="ai-large">
          <Input id="ai-large" disabled={disabled} value={form.model_large ?? ""} onChange={(e) => set({ model_large: e.target.value })} className="font-mono text-xs" />
        </Field>
        <Field label="Default" htmlFor="ai-default">
          <Input id="ai-default" disabled={disabled} value={form.model_default ?? ""} onChange={(e) => set({ model_default: e.target.value })} className="font-mono text-xs" />
        </Field>
        <Field label="Small (classification)" htmlFor="ai-small">
          <Input id="ai-small" disabled={disabled} value={form.model_small ?? ""} onChange={(e) => set({ model_small: e.target.value })} className="font-mono text-xs" />
        </Field>
      </fieldset>
      <label className="flex items-start gap-2 rounded border border-border p-2.5 text-sm">
        <Switch
          checked={!!form.allow_result_samples}
          disabled={disabled}
          onCheckedChange={(v) => set({ allow_result_samples: v })}
          aria-label="Allow sending result samples"
          className="mt-0.5"
        />
        <span>
          Allow sending small result samples
          <span className="block text-xs text-fg-subtle">
            Off (recommended): only schemas, metric definitions and aggregate numbers leave this server. On: capped
            query-result samples may be included. Full datasets are never sent, and data is always passed as
            labelled, untrusted content that cannot change instructions.
          </span>
        </span>
      </label>
      <InlineError error={save.error} />
      <div className="flex flex-wrap items-center gap-2">
        {isOwner ? (
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save AI settings
          </Button>
        ) : null}
        <Button onClick={() => test.mutate()} disabled={test.isPending || !settings.enabled}>
          <PlugZap /> {test.isPending ? "Testing…" : "Test connection"}
        </Button>
        {!settings.enabled ? <span className="text-xs text-fg-subtle">Enable and save to test.</span> : null}
      </div>
      {test.data ? (
        <div
          role="status"
          className={cn(
            "flex items-start gap-2 rounded border px-2.5 py-1.5 text-sm",
            test.data.ok ? "border-positive/30 bg-positive-soft text-positive" : "border-negative/30 bg-negative-soft text-negative",
          )}
        >
          {test.data.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" /> : <XCircle className="mt-0.5 size-3.5 shrink-0" />}
          <span>
            {test.data.message}
            {test.data.model ? ` · ${test.data.model}` : ""}
            {test.data.latency_ms ? ` · ${formatDuration(test.data.latency_ms)}` : ""}
          </span>
        </div>
      ) : null}
      <InlineError error={test.error} />
    </form>
  );
}

function BreakdownTable({ rows, label }: { rows: AIUsageBreakdown[]; label: "model" | "task" }) {
  if (!rows.length) return <p className="text-xs text-fg-subtle">No calls.</p>;
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Usage by {label}</caption>
      <thead>
        <tr className="border-b border-border text-left text-2xs tracking-wide text-fg-subtle uppercase">
          <th scope="col" className="py-1 pr-2 font-medium">{label === "model" ? "Model" : "Task"}</th>
          <th scope="col" className="py-1 pr-2 text-right font-medium">Calls</th>
          <th scope="col" className="py-1 pr-2 text-right font-medium">Tokens in / out</th>
          <th scope="col" className="py-1 text-right font-medium">Est. cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r[label] ?? i}`} className="border-b border-border last:border-b-0">
            <td className={cn("py-1 pr-2", label === "model" && "font-mono text-xs")}>{label === "task" ? humanize(r.task) : r.model}</td>
            <td className="py-1 pr-2 text-right tabular">{formatInt(r.calls)}</td>
            <td className="py-1 pr-2 text-right text-fg-muted tabular">
              {r.tokens_in !== undefined ? `${formatInt(r.tokens_in)} / ${formatInt(r.tokens_out ?? 0)}` : "n/a"}
            </td>
            <td className="py-1 text-right tabular">{formatUsd(r.est_cost_usd)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Usage() {
  const { id: ws } = useWorkspace();
  const [days, setDays] = React.useState<"7" | "30" | "90">("30");
  const usage = useQuery({ queryKey: qk.aiUsage(ws, Number(days)), queryFn: () => ai.usage(ws, { days: Number(days) }) });
  return (
    <SettingsSection
      title="Usage & cost"
      description="Every model call is logged with provider, model, task, tokens, latency and estimated cost."
      actions={
        <Segmented
          aria-label="Usage window"
          value={days}
          onChange={setDays}
          options={[
            { value: "7", label: "7d" },
            { value: "30", label: "30d" },
            { value: "90", label: "90d" },
          ]}
        />
      }
    >
      {usage.isLoading ? (
        <LoadingState rows={3} />
      ) : usage.isError ? (
        <ErrorState compact error={usage.error} onRetry={() => void usage.refetch()} />
      ) : usage.data ? (
        usage.data.totals.calls === 0 ? (
          <EmptyState compact title={`No AI calls in the last ${days} days`} description="Investigations, SQL and summaries ran on the deterministic engine only." />
        ) : (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 rounded-md border border-border p-3 sm:grid-cols-4">
              <Kpi label="Calls" value={usage.data.totals.calls} format="integer" size="sm" />
              <Kpi label="Tokens in" value={usage.data.totals.tokens_in} format="integer" size="sm" />
              <Kpi label="Tokens out" value={usage.data.totals.tokens_out} format="integer" size="sm" />
              <div className="flex flex-col gap-0.5">
                <div className="text-xs text-fg-subtle">Est. cost</div>
                <div className="text-lg font-semibold tabular">{formatUsd(usage.data.totals.est_cost_usd)}</div>
              </div>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
              <div>
                <h3 className="mb-1 text-xs font-semibold">By model</h3>
                <BreakdownTable rows={usage.data.by_model} label="model" />
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold">By task</h3>
                <BreakdownTable rows={usage.data.by_task} label="task" />
              </div>
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold">Recent calls</h3>
              <div className="max-h-80 overflow-auto rounded-md border border-border scrollbar-thin">
                <table className="w-full text-xs">
                  <caption className="sr-only">Recent AI calls</caption>
                  <thead className="sticky top-0 bg-bg-subtle">
                    <tr className="border-b border-border text-left text-2xs tracking-wide text-fg-subtle uppercase">
                      <th scope="col" className="px-2 py-1 font-medium">Time</th>
                      <th scope="col" className="px-2 py-1 font-medium">Task</th>
                      <th scope="col" className="px-2 py-1 font-medium">Model</th>
                      <th scope="col" className="px-2 py-1 text-right font-medium">In</th>
                      <th scope="col" className="px-2 py-1 text-right font-medium">Out</th>
                      <th scope="col" className="px-2 py-1 text-right font-medium">Latency</th>
                      <th scope="col" className="px-2 py-1 text-right font-medium">Cost</th>
                      <th scope="col" className="px-2 py-1 font-medium">Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.data.items.map((r, i) => (
                      <tr key={r.id ?? i} className="border-b border-border last:border-b-0">
                        <td className="px-2 py-1 whitespace-nowrap text-fg-muted">{formatDateTime(r.created_at)}</td>
                        <td className="px-2 py-1">{humanize(r.task)}</td>
                        <td className="px-2 py-1 font-mono">{r.model}</td>
                        <td className="px-2 py-1 text-right tabular">{formatInt(r.tokens_in)}</td>
                        <td className="px-2 py-1 text-right tabular">{formatInt(r.tokens_out)}</td>
                        <td className="px-2 py-1 text-right tabular">{formatDuration(r.latency_ms)}</td>
                        <td className="px-2 py-1 text-right tabular">{formatUsd(r.est_cost_usd)}</td>
                        <td className="px-2 py-1">
                          {r.success === false ? <Badge tone="negative">Failed</Badge> : <Badge tone="positive">OK</Badge>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )
      ) : null}
    </SettingsSection>
  );
}

export function AiTab() {
  const { id: ws, workspace } = useWorkspace();
  const isOwner = workspace?.role === "owner";
  const settings = useQuery({ queryKey: qk.aiSettings(ws), queryFn: () => ai.settings(ws) });
  return (
    <div>
      <div className="mb-4 flex max-w-3xl items-start gap-2 rounded-md border border-border bg-bg-subtle px-3 py-2 text-sm text-fg-muted">
        <Info className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
        <p>
          AnalystOS works fully without an AI provider: question interpretation, planning, SQL, decomposition,
          evidence and summaries are all computed by the deterministic engine. When enabled, the model only helps
          choose between known candidates and word summaries, and a verifier rejects any number that is not in an
          executed query result.
        </p>
      </div>
      {!isOwner ? <RoleNotice /> : null}
      <SettingsSection
        title="AI provider"
        description="Anthropic is supported first; the provider interface allows others."
        actions={
          settings.data ? (
            <Badge tone={settings.data.enabled && (settings.data.has_api_key || settings.data.key_source === "environment") ? "positive" : "neutral"}>
              {settings.data.enabled ? (settings.data.has_api_key || settings.data.key_source === "environment" ? "Active" : "Enabled, no key") : "Off"}
            </Badge>
          ) : null
        }
      >
        {settings.isLoading ? (
          <LoadingState rows={4} />
        ) : settings.isError ? (
          <ErrorState compact error={settings.error} onRetry={() => void settings.refetch()} />
        ) : settings.data ? (
          <>
            {settings.data.server_ai_enabled === false ? (
              <p className="mb-3 max-w-xl rounded border border-warning/40 bg-warning-soft px-2.5 py-1.5 text-xs text-warning">
                AI is disabled for this server by its operator; workspace settings are kept but no calls are made.
              </p>
            ) : null}
            <SettingsForm settings={settings.data} isOwner={isOwner} />
          </>
        ) : null}
      </SettingsSection>
      <Usage />
    </div>
  );
}
