"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CornerDownLeft, Terminal } from "lucide-react";
import type { CommandResult, FollowUp } from "@/lib/api/types";
import { investigations } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Kbd } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export const COMMAND_EXAMPLES = [
  "Break this down by region",
  "Compare this to last year",
  "Show me the customers responsible for most of the decline",
  "Exclude new stores",
  "Use gross revenue instead",
  "Show the SQL",
  "Save this as a finding",
  "Turn this into a dashboard",
  "Build me a report",
];

export type CommandEffect =
  | { kind: "select"; nodeId: string; refresh: boolean }
  | { kind: "sql"; artifactId?: string }
  | { kind: "navigate"; href: string; toast?: string }
  | { kind: "refresh" }
  | { kind: "link"; href: string; label: string; refresh: boolean }
  | { kind: "none" };

/**
 * Maps the API's `CommandResult` to a UI effect (pure, unit-tested). `currentId` is the open
 * investigation, so a branch into a different investigation navigates while an in-place change
 * (drill, node update, rerun) refreshes the tree.
 */
export function commandEffect(res: CommandResult, wsHref: (p: string) => string, currentId: string | null): CommandEffect {
  switch (res.action) {
    case "branched":
    case "created_investigation":
      if (res.investigation_id && res.investigation_id !== currentId)
        return { kind: "navigate", href: wsHref(`/investigate/${res.investigation_id}`) };
      return { kind: "refresh" };
    case "drilled":
    case "listed_contributors": {
      const first = res.items?.[0]?.node_id;
      return first ? { kind: "select", nodeId: first, refresh: true } : { kind: "refresh" };
    }
    case "node_updated":
    case "rerun_started":
      return { kind: "refresh" };
    case "saved_finding":
      return res.finding_id
        ? { kind: "link", href: wsHref(`/findings/${res.finding_id}`), label: "Open finding", refresh: true }
        : { kind: "refresh" };
    case "show_sql":
      return { kind: "sql", artifactId: res.sql?.find((s) => s.artifact_id)?.artifact_id ?? undefined };
    case "built_report":
      return res.report_id ? { kind: "navigate", href: wsHref(`/reports/${res.report_id}`) } : { kind: "none" };
    case "built_dashboard":
      return res.dashboard_id ? { kind: "navigate", href: wsHref(`/dashboards/${res.dashboard_id}`) } : { kind: "none" };
    default:
      return { kind: "none" };
  }
}

/**
 * Ask / command bar (spec §29-30): a command interface to the workspace, not a chat log.
 * The server parses the text deterministically and applies it; we render its short reply and act on it.
 */
export const CommandBar = React.forwardRef<
  HTMLInputElement,
  {
    investigationId: string;
    nodeId: string | null;
    onSelectNode: (id: string) => void;
    onShowSql: (artifactId?: string) => void;
    followUps?: FollowUp[];
  }
>(function CommandBar({ investigationId, nodeId, onSelectNode, onShowSql, followUps = [] }, ref) {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const [text, setText] = React.useState("");
  const [reply, setReply] = React.useState<{ ok: boolean; message: string; link?: { href: string; label: string } } | null>(
    null,
  );

  const run = useMutation({
    mutationFn: (t: string) => investigations.command(ws, investigationId, { text: t, node_id: nodeId }),
    onSuccess: async (res) => {
      setText("");
      const eff = commandEffect(res, href, investigationId);
      const ok = res.action !== "clarify";
      setReply({ ok, message: res.message, link: eff.kind === "link" ? { href: eff.href, label: eff.label } : undefined });
      const refresh = () => qc.invalidateQueries({ queryKey: qk.investigation(ws, investigationId) });
      switch (eff.kind) {
        case "select":
          await refresh();
          onSelectNode(eff.nodeId);
          break;
        case "sql":
          onShowSql(eff.artifactId);
          break;
        case "navigate":
          router.push(eff.href);
          break;
        case "link":
          void refresh();
          void qc.invalidateQueries({ queryKey: ["ws", ws, "findings"] });
          toast.success(res.message, { action: { label: eff.label, onClick: () => router.push(eff.href) } });
          break;
        case "refresh":
          void refresh();
          break;
        case "none":
          break;
      }
    },
    onError: (e: Error) => setReply({ ok: false, message: e.message }),
  });

  const submit = (t: string) => {
    const v = t.trim();
    if (v && !run.isPending) run.mutate(v);
  };

  return (
    <div className="flex flex-col gap-1">
      <form
        className="flex h-8 items-center gap-2 rounded border border-border-strong bg-bg px-2 focus-within:outline-2 focus-within:outline-ring"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <Terminal className="size-3.5 shrink-0 text-fg-subtle" aria-hidden />
        <input
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          list="aos-command-examples"
          placeholder="Ask or command: “break this down by region”, “compare to last year”, “show the SQL”…"
          aria-label="Investigation command"
          className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-faint"
        />
        <datalist id="aos-command-examples">
          {COMMAND_EXAMPLES.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        {run.isPending ? <Spinner /> : <Kbd className="hidden sm:inline-flex">/</Kbd>}
        <button type="submit" className="text-fg-subtle hover:text-fg" aria-label="Run command" disabled={run.isPending}>
          <CornerDownLeft className="size-3.5" />
        </button>
      </form>
      {reply ? (
        <p role="status" className={cn("px-1 text-xs", reply.ok ? "text-fg-muted" : "text-negative")}>
          {reply.message}
          {reply.link ? (
            <>
              {" "}
              <Link href={reply.link.href} className="text-accent underline-offset-2 hover:underline">
                {reply.link.label}
              </Link>
            </>
          ) : null}
        </p>
      ) : null}
      {followUps.length ? (
        <div className="flex flex-wrap gap-1" aria-label="Suggested follow-ups">
          {followUps.slice(0, 5).map((f, i) => (
            <button
              key={i}
              type="button"
              onClick={() => submit(f.command ?? f.label)}
              disabled={run.isPending}
              className="h-6 rounded-sm border border-border bg-bg-subtle px-2 text-xs text-fg-muted hover:border-border-strong hover:text-fg"
            >
              {f.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
});
