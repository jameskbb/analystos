"use client";
import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitMerge, Plus, Radar, ShieldAlert } from "lucide-react";
import { relationships } from "@/lib/api/resources/data";
import type { Relationship } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader, Panel } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/tabs";
import { EmptyState, ErrorState, LoadingState, InlineError } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { RelationshipList, isManyToMany } from "@/components/data/relationships";
import { AddRelationshipDialog } from "@/components/data/add-relationship-dialog";
import { JobProgress, useJobRunner } from "@/components/data/use-job";

type Filter = "suggested" | "approved" | "rejected" | "all";

export default function RelationshipsPage() {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const [filter, setFilter] = React.useState<Filter>("suggested");
  const [adding, setAdding] = React.useState(false);
  const [discoverError, setDiscoverError] = React.useState<unknown>(null);
  const [discovering, setDiscovering] = React.useState(false);
  const { run, progress } = useJobRunner();
  const all = useQuery({ queryKey: ["ws", ws, "relationships", "all"], queryFn: () => relationships.list(ws) });

  const discover = async () => {
    setDiscoverError(null);
    setDiscovering(true);
    try {
      const job = await run(() => relationships.discover(ws));
      await qc.invalidateQueries({ queryKey: ["ws", ws, "relationships"] });
      const n = Number(job.result?.new_suggestions ?? NaN);
      toast.success(Number.isFinite(n) ? `Discovery finished: ${n} new suggestion${n === 1 ? "" : "s"}` : "Discovery finished");
      setFilter("suggested");
    } catch (e) {
      setDiscoverError(e);
    } finally {
      setDiscovering(false);
    }
  };

  const items = all.data ?? [];
  const counts = {
    suggested: items.filter((r) => r.status === "suggested").length,
    approved: items.filter((r) => r.status === "approved").length,
    rejected: items.filter((r) => r.status === "rejected").length,
  };
  const shown: Relationship[] = filter === "all" ? items : items.filter((r) => r.status === filter);
  const m2m = items.filter((r) => r.status !== "rejected" && isManyToMany(r));

  return (
    <Page>
      <PageHeader
        breadcrumb={
          <>
            <Link href={href("/data")} className="hover:text-fg">Data</Link>
            <span>/</span>
            <span>Relationships</span>
          </>
        }
        title="Relationships"
        description="How tables join. Suggestions come from names, types, uniqueness and value overlap; only relationships you approve are ever used in joins."
        actions={
          canEdit ? (
            <>
              <Button onClick={() => setAdding(true)}>
                <Plus /> Add manually
              </Button>
              <Button variant="primary" onClick={() => void discover()} disabled={discovering}>
                {discovering ? <Spinner className="text-accent-fg" /> : <Radar />} Discover
              </Button>
            </>
          ) : null
        }
      />
      <PageBody className="flex flex-col gap-3">
        <JobProgress progress={progress} />
        <InlineError error={discoverError} />
        {m2m.length ? (
          <div role="alert" className="flex items-start gap-2 rounded border border-negative/30 bg-negative-soft px-3 py-2 text-sm text-negative">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            <div>
              <span className="font-medium">{m2m.length} many-to-many relationship{m2m.length === 1 ? "" : "s"}</span> detected.
              Joining across them duplicates rows; AnalystOS pre-aggregates to the metric grain or refuses such queries.
            </div>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Segmented<Filter>
            aria-label="Relationship status"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "suggested", label: `Suggested (${counts.suggested})` },
              { value: "approved", label: `Approved (${counts.approved})` },
              { value: "rejected", label: `Rejected (${counts.rejected})` },
              { value: "all", label: `All (${items.length})` },
            ]}
          />
          {filter === "suggested" && counts.suggested ? (
            <span className="text-xs text-fg-subtle">Review each suggestion: approve with the right cardinality, or reject.</span>
          ) : null}
        </div>
        <Panel>
          {all.isLoading ? (
            <LoadingState rows={5} className="px-3" />
          ) : all.error ? (
            <ErrorState error={all.error} onRetry={() => void all.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={GitMerge}
              title="No relationships yet"
              description="Run discovery to get suggestions from your datasets, add one manually, or load the demo workspace."
              action={
                canEdit ? (
                  <Button variant="primary" onClick={() => void discover()} disabled={discovering}>
                    <Radar /> Discover relationships
                  </Button>
                ) : undefined
              }
              secondary={<LoadDemoButton workspaceId={ws} variant="secondary" />}
            />
          ) : (
            <RelationshipList
              items={shown}
              empty={
                <EmptyState
                  compact
                  icon={GitMerge}
                  title={filter === "suggested" ? "Nothing waiting for review" : `No ${filter} relationships`}
                  description={filter === "suggested" ? "All suggestions have been approved or rejected." : undefined}
                />
              }
            />
          )}
        </Panel>
      </PageBody>
      <AddRelationshipDialog open={adding} onOpenChange={setAdding} />
    </Page>
  );
}
