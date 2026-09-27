"use client";
import * as React from "react";
import Link from "next/link";
import { Database, Upload, Sigma, MessageSquareText, ShieldCheck, ScanSearch } from "lucide-react";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const HOW_IT_WORKS = [
  { icon: Database, title: "Connect", body: "Upload CSV, Excel, Parquet or JSON, or connect a database read-only." },
  { icon: ScanSearch, title: "Profile", body: "Every column is profiled; relationships and quality issues are surfaced for review." },
  { icon: Sigma, title: "Define metrics", body: "A semantic layer of versioned metrics, dimensions and driver trees." },
  { icon: MessageSquareText, title: "Ask", body: "Questions become an analysis plan, then real SQL tests arranged as a tree." },
  { icon: ShieldCheck, title: "Evidence", body: "Every finding links to its query, filters, metric version and source data." },
] as const;

/** Empty-workspace onboarding: demo, upload, connect, and how the product works. */
export function Onboarding({ workspaceId, href, className }: { workspaceId: string; href: (p: string) => string; className?: string }) {
  return (
    <section aria-labelledby="onboarding-title" className={cn("rounded-md border border-border bg-bg", className)}>
      <div className="flex flex-col gap-4 p-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-xl">
          <h2 id="onboarding-title" className="text-base font-semibold">
            This workspace has no data yet
          </h2>
          <p className="mt-1 text-sm text-fg-muted">
            Load the Summit Supply Co. demo, a building-materials distributor with two years of orders and five planted
            analytical stories (an 11.8% August revenue decline, margin compression, a conversion drop, an inventory
            build-up and a forecast miss), or bring your own data.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <LoadDemoButton workspaceId={workspaceId} size="md" />
          <Button asChild variant="secondary" size="md">
            <Link href={href("/data?upload=1")}>
              <Upload /> Upload a file
            </Link>
          </Button>
          <Button asChild variant="secondary" size="md">
            <Link href={href("/data?connect=1")}>
              <Database /> Connect a database
            </Link>
          </Button>
        </div>
      </div>
      <ol className="grid grid-cols-1 border-t border-border sm:grid-cols-2 lg:grid-cols-5" aria-label="How AnalystOS works">
        {HOW_IT_WORKS.map((s, i) => (
          <li
            key={s.title}
            className="flex gap-2.5 border-b border-border p-4 last:border-b-0 sm:[&:nth-child(odd)]:border-r lg:border-r lg:border-b-0 lg:last:border-r-0"
          >
            <span className="flex size-5 shrink-0 items-center justify-center rounded-sm border border-border-strong font-mono text-2xs text-fg-subtle">
              {i + 1}
            </span>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <s.icon className="size-3.5 text-fg-subtle" aria-hidden />
                {s.title}
              </div>
              <p className="mt-0.5 text-xs text-fg-subtle">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
