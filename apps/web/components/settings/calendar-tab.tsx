"use client";
import * as React from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { workspaces } from "@/lib/api/endpoints";
import type { CalendarResolution } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ErrorState, InlineError, LoadingState } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";
import { MONTHS, PREVIEW_EXPRESSIONS, describeResolution, fiscalYearLabel } from "./calendar-format";
import { RoleNotice, SettingsSection } from "./shared";
import { settingsKey, useWorkspaceSettings } from "./workspace-tab";

function ResolutionRow({ expr, data, error, loading }: { expr: string; data?: CalendarResolution; error?: unknown; loading?: boolean }) {
  const d = data ? describeResolution(data) : null;
  return (
    <tr className="border-b border-border last:border-b-0">
      <th scope="row" className="px-2 py-1.5 text-left font-mono text-xs font-normal whitespace-nowrap">
        {expr}
      </th>
      {loading ? (
        <td colSpan={3} className="px-2 py-1.5">
          <Spinner />
        </td>
      ) : error ? (
        <td colSpan={3} className="px-2 py-1.5 text-xs text-negative">
          {error instanceof Error ? error.message : String(error)}
        </td>
      ) : d ? (
        <>
          <td className="px-2 py-1.5 whitespace-nowrap tabular">{d.window}</td>
          <td className="px-2 py-1.5 whitespace-nowrap text-fg-muted tabular">{d.previous}</td>
          <td className="px-2 py-1.5 whitespace-nowrap text-fg-muted tabular">{d.yoy}</td>
        </>
      ) : null}
    </tr>
  );
}

export function CalendarTab() {
  const { id: ws, workspace } = useWorkspace();
  const qc = useQueryClient();
  const isOwner = workspace?.role === "owner";
  const settings = useWorkspaceSettings(ws);
  const [month, setMonth] = React.useState(1);
  const [weekStart, setWeekStart] = React.useState("monday");
  const [custom, setCustom] = React.useState("");
  const [customExpr, setCustomExpr] = React.useState("");

  React.useEffect(() => {
    if (settings.data) {
      setMonth(settings.data.calendar.fiscal_year_start_month);
      setWeekStart(settings.data.calendar.week_start);
    }
  }, [settings.data]);

  const saved = settings.data?.calendar;
  const version = saved ? `${saved.fiscal_year_start_month}-${saved.week_start}` : "none";
  const previews = useQueries({
    queries: PREVIEW_EXPRESSIONS.map((expr) => ({
      queryKey: ["ws", ws, "calendar-resolve", expr, version],
      queryFn: () => workspaces.resolvePeriod(ws, expr),
      enabled: !!saved,
      retry: false,
    })),
  });
  const customQuery = useQuery({
    queryKey: ["ws", ws, "calendar-resolve", customExpr, version],
    queryFn: () => workspaces.resolvePeriod(ws, customExpr),
    enabled: !!saved && customExpr.length > 0,
    retry: false,
  });

  const save = useMutation({
    mutationFn: () => workspaces.updateSettings(ws, { calendar: { fiscal_year_start_month: month, week_start: weekStart } }),
    onSuccess: (s) => {
      qc.setQueryData(settingsKey(ws), s);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "semantic"] });
      toast.success("Calendar saved", { description: "New investigations and period filters use it; past artifacts keep their stored windows." });
    },
  });

  if (settings.isLoading) return <LoadingState rows={4} />;
  if (settings.isError) return <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />;
  const dirty = !!saved && (saved.fiscal_year_start_month !== month || saved.week_start !== weekStart);

  return (
    <div>
      {!isOwner ? <RoleNotice /> : null}
      <SettingsSection
        title="Business calendar"
        description="Used to resolve “Q3”, “YTD”, “last month” and weeks in questions, filters and business reviews."
      >
        <form
          className="grid max-w-lg gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Fiscal year starts in" htmlFor="cal-month" hint={fiscalYearLabel(month)} className="sm:col-span-1">
            <NativeSelect id="cal-month" value={month} disabled={!isOwner} onChange={(e) => setMonth(Number(e.target.value))}>
              {MONTHS.map((m, i) => (
                <option key={m} value={i + 1}>
                  {m}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Weeks start on" htmlFor="cal-week">
            <NativeSelect id="cal-week" value={weekStart} disabled={!isOwner} onChange={(e) => setWeekStart(e.target.value)}>
              <option value="monday">Monday</option>
              <option value="sunday">Sunday</option>
            </NativeSelect>
          </Field>
          <InlineError error={save.error} className="sm:col-span-2" />
          {isOwner ? (
            <div className="sm:col-span-2">
              <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
                Save calendar
              </Button>
            </div>
          ) : null}
        </form>
      </SettingsSection>
      <SettingsSection
        title="Preview"
        description={
          dirty
            ? "The preview reflects the saved calendar. Save to see your changes."
            : "How period expressions resolve today with the saved calendar, with the comparison windows the engine uses."
        }
      >
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Period resolution preview</caption>
            <thead>
              <tr className="border-b border-border bg-bg-subtle text-left text-2xs tracking-wide text-fg-subtle uppercase">
                <th scope="col" className="px-2 py-1.5 font-medium">Expression</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Window</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Previous period</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Same period last year</th>
              </tr>
            </thead>
            <tbody>
              {PREVIEW_EXPRESSIONS.map((expr, i) => (
                <ResolutionRow key={expr} expr={expr} data={previews[i].data} error={previews[i].error} loading={previews[i].isLoading} />
              ))}
              {customExpr ? (
                <ResolutionRow expr={customExpr} data={customQuery.data} error={customQuery.error} loading={customQuery.isLoading} />
              ) : null}
            </tbody>
          </table>
        </div>
        <form
          className="mt-2 flex max-w-md items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setCustomExpr(custom.trim());
          }}
        >
          <Field label="Try an expression" htmlFor="cal-try" className="flex-1">
            <Input id="cal-try" value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="2026-08, fiscal Q2, rolling 90 days…" />
          </Field>
          <Button type="submit" disabled={!custom.trim()}>
            Resolve
          </Button>
        </form>
      </SettingsSection>
    </div>
  );
}
