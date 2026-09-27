"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarRange, CheckCircle2, ListChecks, Send } from "lucide-react";
import { isJobAccepted, waitForJob } from "@/lib/api/endpoints";
import { reports, type ReportOut } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { EmptyState, InlineError, QueryState } from "@/components/states/states";
import { StatusBadge } from "@/components/analysis/status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { latestFullMonth, monthLabel, reviewState } from "@/components/outputs/model";
import { formatRelative } from "@/lib/format";

const CONTENTS = [
  "KPI overview for canonical metrics vs the prior month",
  "Largest changes, with positive and negative drivers",
  "Anomalies detected in the month",
  "Segment movements (region, customer segment, product category, channel)",
  "Saved findings from the period, with evidence strength",
  "Unresolved questions and open hypotheses",
];

export default function BusinessReviewPage() {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const [period, setPeriod] = React.useState(() => latestFullMonth(new Date()));
  const [title, setTitle] = React.useState("");
  const [progress, setProgress] = React.useState<string | null>(null);
  const existing = useQuery({
    queryKey: qk.reports(ws, { kind: "business_review" }),
    queryFn: () => reports.list(ws, { kind: "business_review" }),
  });
  const generate = useMutation({
    mutationFn: async (): Promise<string> => {
      setProgress("Computing");
      const res = await reports.businessReview(ws, {
        period,
        title: title.trim() || `Business review · ${monthLabel(period)}`,
      });
      if (isJobAccepted(res)) {
        const job = await waitForJob(res, (j) => setProgress(j.message || `${Math.round(j.progress * 100)}%`));
        const rid = (job.result?.report_id as string | undefined) ?? job.resource_id;
        if (!rid) throw new Error("The review was generated but no report id was returned.");
        return rid;
      }
      return (res as ReportOut).id;
    },
    onSuccess: (rid) => {
      setProgress(null);
      void qc.invalidateQueries({ queryKey: qk.reports(ws) });
      router.push(href(`/reports/${rid}`));
    },
    onError: () => setProgress(null),
  });
  const sameMonth = (existing.data ?? []).filter((r) => r.period === period);

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <Link href={href("/reports")} className="hover:text-fg">
            Reports
          </Link>
        }
        title="Business review"
        description="A monthly operating review computed from the semantic layer, then reviewed by an analyst before it is published."
      />
      <PageBody>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          <Panel title="Generate a review">
            <form
              className="flex flex-col gap-3 p-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (period) generate.mutate();
              }}
            >
              <Field label="Month" htmlFor="br-period" hint="Defaults to the latest complete month.">
                <Input id="br-period" type="month" required value={period} onChange={(e) => setPeriod(e.target.value)} className="w-44" />
              </Field>
              <Field label="Title" htmlFor="br-title">
                <Input id="br-title" value={title} placeholder={`Business review · ${monthLabel(period)}`} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <div>
                <div className="mb-1 text-xs font-medium text-fg-muted">Includes</div>
                <ul className="flex flex-col gap-0.5 text-sm text-fg-muted">
                  {CONTENTS.map((c) => (
                    <li key={c} className="flex gap-1.5">
                      <span className="mt-2 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden />
                      {c}
                    </li>
                  ))}
                </ul>
              </div>
              {sameMonth.length ? (
                <p className="text-xs text-warning">
                  {sameMonth.length === 1 ? "A review" : `${sameMonth.length} reviews`} for {monthLabel(period)} already exist; generating creates a new draft.
                </p>
              ) : null}
              <InlineError error={generate.error} />
              <div>
                <Button type="submit" variant="primary" size="md" disabled={!canEdit || !period || generate.isPending}>
                  {generate.isPending ? <Spinner className="text-accent-fg" /> : <CalendarRange />}
                  {generate.isPending ? `Generating${progress ? ` · ${progress}` : "…"}` : `Generate ${monthLabel(period)} review`}
                </Button>
              </div>
            </form>
          </Panel>
          <div className="flex flex-col gap-4">
            <Panel title="How publishing works">
              <ol className="grid gap-3 p-3 text-sm sm:grid-cols-3">
                <li className="flex gap-2">
                  <CalendarRange className="mt-0.5 size-4 shrink-0 text-fg-subtle" />
                  <span>
                    <b className="font-medium">1. Generate.</b> Every number comes from an executed, stored query.
                  </span>
                </li>
                <li className="flex gap-2">
                  <ListChecks className="mt-0.5 size-4 shrink-0 text-fg-subtle" />
                  <span>
                    <b className="font-medium">2. Review.</b> Submit for review, then mark each block reviewed or exclude it. Edits clear a block&apos;s review.
                  </span>
                </li>
                <li className="flex gap-2">
                  <Send className="mt-0.5 size-4 shrink-0 text-fg-subtle" />
                  <span>
                    <b className="font-medium">3. Publish.</b> Only when every included block is reviewed. Published versions are read-only.
                  </span>
                </li>
              </ol>
            </Panel>
            <Panel title="Previous reviews">
              <QueryState
                query={existing}
                compact
                empty={<EmptyState compact icon={CalendarRange} title="No business reviews yet" description="Generate the first one on the left." />}
              >
                {(list) => (
                  <ul className="divide-y divide-border">
                    {[...list]
                      .sort((a, b) => (b.period ?? "").localeCompare(a.period ?? ""))
                      .map((r) => {
                        const st = reviewState(r);
                        return (
                          <li key={r.id} className="flex items-center gap-3 px-3 py-2">
                            <div className="min-w-0 flex-1">
                              <Link href={href(`/reports/${r.id}`)} className="text-sm font-medium hover:text-accent hover:underline">
                                {r.title}
                              </Link>
                              <div className="text-xs text-fg-subtle">
                                {monthLabel(r.period)} · v{r.version_no} · updated {formatRelative(r.updated_at ?? r.created_at)}
                              </div>
                            </div>
                            {r.status !== "published" ? (
                              <span className="flex items-center gap-1 text-xs text-fg-subtle tabular">
                                <CheckCircle2 className="size-3" /> {st.reviewed}/{st.included}
                              </span>
                            ) : null}
                            <StatusBadge status={r.status} />
                          </li>
                        );
                      })}
                  </ul>
                )}
              </QueryState>
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
