"use client";
import * as React from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { analysis } from "@/lib/api/resources/analysis";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { ForecastPanel } from "@/components/analyze/forecast-panel";
import { AnomalyPanel } from "@/components/analyze/anomaly-panel";
import { SegmentsPanel } from "@/components/analyze/segments-panel";
import { StatsPanel } from "@/components/analyze/stats-panel";
import { CorrelationPanel } from "@/components/analyze/correlation-panel";
import { AnalysisView } from "@/components/analyze/analysis-view";
import { labelTone } from "@/components/analyze/result-meta";
import { defaultSeriesWindow } from "@/components/analyze/charts";
import type { SeriesDraft } from "@/components/analyze/sources";
import type { Grain } from "@/lib/api/resources/analysis";
import { formatRelative, humanize } from "@/lib/format";

const TABS = ["forecast", "anomalies", "segments", "tests", "correlation"] as const;
type Tab = (typeof TABS)[number];

function inclusive(endExclusive: string): string {
  const d = new Date(`${endExclusive}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

function RecentAnalyses({ onOpen }: { onOpen: (id: string) => void }) {
  const { id: ws } = useWorkspace();
  const q = useQuery({ queryKey: qk.analyses(ws), queryFn: () => analysis.list(ws, { limit: 20 }) });
  if (q.isLoading) return <LoadingState label="Loading analyses" />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data?.length)
    return <EmptyState compact icon={History} title="No analyses yet" description="Every run is saved here with its SQL, parameters and versions." />;
  return (
    <ul className="divide-y divide-border" aria-label="Recent analyses">
      {q.data.map((a) => (
        <li key={a.id}>
          <button type="button" onClick={() => onOpen(a.id)} className="flex w-full flex-col gap-0.5 px-3 py-2 text-left hover:bg-bg-subtle">
            <span className="flex items-center gap-1.5">
              <Badge tone={labelTone(a.label, a.exploratory)}>{humanize(a.kind)}</Badge>
              <span className="truncate text-sm">{a.title}</span>
            </span>
            <span className="truncate text-xs text-fg-subtle">
              {a.summary} · {formatRelative(a.created_at)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function StoredAnalysis({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { id: ws } = useWorkspace();
  const q = useQuery({ queryKey: ["ws", ws, "analysis", id], queryFn: () => analysis.get(ws, id as string), enabled: !!id });
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent width="w-[min(760px,100vw)]" className="overflow-auto">
        <SheetHeader>
          <SheetTitle>{q.data?.title ?? "Analysis"}</SheetTitle>
        </SheetHeader>
        <div className="p-4">
          {q.isLoading ? <LoadingState label="Loading analysis" /> : null}
          {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : null}
          {q.data ? <AnalysisView out={q.data} /> : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function AnalyzeScreen() {
  const { href } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as Tab) : "forecast";
  // Weekly forecasts leave enough history to backtest (monthly needs 26+ points for a seasonal
  // model); anomaly detection is most useful per day.
  const initialGrain: Grain = tab === "anomalies" ? "day" : "week";
  const [draft, setDraftState] = React.useState<SeriesDraft>(() => {
    const w = defaultSeriesWindow(new Date(), initialGrain);
    return {
      metricId: params.get("metric") ?? "",
      start: w.start,
      end: inclusive(w.end),
      grain: initialGrain,
      filterDimension: "",
      filterValues: "",
    };
  });
  // Until the analyst edits the dates, the window follows the grain so the last bucket is complete.
  const datesTouched = React.useRef(false);
  const setDraft = (next: SeriesDraft) => {
    if (next.start !== draft.start || next.end !== draft.end) datesTouched.current = true;
    if (next.grain !== draft.grain && !datesTouched.current) {
      const w = defaultSeriesWindow(new Date(), next.grain);
      next = { ...next, start: w.start, end: inclusive(w.end) };
    }
    setDraftState(next);
  };
  const [openId, setOpenId] = React.useState<string | null>(params.get("id"));
  const setTab = (t: string) => {
    const next = new URLSearchParams(params.toString());
    next.set("tab", t);
    router.replace(href(`/analyze?${next.toString()}`));
    if (t === "anomalies" && draft.grain !== "day") setDraft({ ...draft, grain: "day" });
    if (t === "forecast" && draft.grain === "day") setDraft({ ...draft, grain: "week" });
  };
  const segmentsWindow = React.useMemo(() => {
    const w = defaultSeriesWindow(new Date(), "quarter");
    const start = new Date(`${w.end}T00:00:00Z`);
    start.setUTCMonth(start.getUTCMonth() - 3);
    return { start: start.toISOString().slice(0, 10), end: inclusive(w.end) };
  }, []);

  return (
    <Page>
      <PageHeader
        title="Analyze"
        description="Forecasts, anomalies, segments, statistical tests and correlations on governed metrics and workspace tables. Every run is saved as an artifact with its SQL and lineage."
      />
      <PageBody>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <Tabs value={tab} onValueChange={setTab} className="min-w-0">
            <TabsList aria-label="Analysis type">
              <TabsTrigger value="forecast">Forecast</TabsTrigger>
              <TabsTrigger value="anomalies">Anomalies</TabsTrigger>
              <TabsTrigger value="segments">Segments</TabsTrigger>
              <TabsTrigger value="tests">Statistical tests</TabsTrigger>
              <TabsTrigger value="correlation">Correlation</TabsTrigger>
            </TabsList>
            <TabsContent value="forecast" className="mt-3">
              <ForecastPanel draft={draft} onDraft={setDraft} />
            </TabsContent>
            <TabsContent value="anomalies" className="mt-3">
              <AnomalyPanel draft={draft} onDraft={setDraft} />
            </TabsContent>
            <TabsContent value="segments" className="mt-3">
              <SegmentsPanel defaultWindow={segmentsWindow} />
            </TabsContent>
            <TabsContent value="tests" className="mt-3">
              <StatsPanel />
            </TabsContent>
            <TabsContent value="correlation" className="mt-3">
              <CorrelationPanel />
            </TabsContent>
          </Tabs>
          <Panel title="Recent analyses" description="Saved artifacts; open one to see its result and SQL" className="self-start">
            <RecentAnalyses onOpen={setOpenId} />
          </Panel>
        </div>
      </PageBody>
      <StoredAnalysis id={openId} onClose={() => setOpenId(null)} />
    </Page>
  );
}

export default function AnalyzePage() {
  return (
    <Suspense fallback={<LoadingState variant="block" label="Loading" />}>
      <AnalyzeScreen />
    </Suspense>
  );
}
