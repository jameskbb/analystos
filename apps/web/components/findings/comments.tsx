"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { findings } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { useSession } from "@/components/providers/session";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { ErrorState, LoadingState } from "@/components/states/states";
import { formatRelative, formatDateTime } from "@/lib/format";

/** Comment thread on a finding (spec §66). */
export function FindingComments({ findingId, canEdit }: { findingId: string; canEdit: boolean }) {
  const { id: ws } = useWorkspace();
  const { data: session } = useSession();
  const qc = useQueryClient();
  const key = qk.findingPart(ws, findingId, "comments");
  const list = useQuery({ queryKey: key, queryFn: () => findings.comments(ws, findingId) });
  const [body, setBody] = React.useState("");
  const add = useMutation({
    mutationFn: () => findings.addComment(ws, findingId, body.trim()),
    onSuccess: () => {
      setBody("");
      void qc.invalidateQueries({ queryKey: key });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const del = useMutation({
    mutationFn: (id: string) => findings.deleteComment(ws, findingId, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <div className="flex flex-col gap-2">
      {list.isLoading ? (
        <LoadingState rows={2} />
      ) : list.error ? (
        <ErrorState error={list.error} compact onRetry={() => void list.refetch()} />
      ) : list.data?.length ? (
        <ul className="flex flex-col gap-2" aria-label="Comments">
          {list.data.map((c) => (
            <li key={c.id} className="group rounded border border-border px-2.5 py-1.5">
              <div className="flex items-center gap-2 text-2xs text-fg-subtle">
                <span className="font-medium text-fg-muted">{c.user_name ?? "Unknown"}</span>
                <time dateTime={c.created_at} title={formatDateTime(c.created_at)}>
                  {formatRelative(c.created_at)}
                </time>
                {canEdit && (!c.user_id || c.user_id === session?.user?.id) ? (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="ml-auto size-5 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    aria-label="Delete comment"
                    onClick={() => del.mutate(c.id)}
                  >
                    <Trash2 />
                  </Button>
                ) : null}
              </div>
              <p className="mt-0.5 text-sm whitespace-pre-wrap">{c.body}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-fg-subtle">No comments yet.</p>
      )}
      {canEdit ? (
        <form
          className="flex flex-col gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (body.trim()) add.mutate();
          }}
        >
          <Textarea
            rows={2}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Add a comment…"
            aria-label="New comment"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                if (body.trim()) add.mutate();
              }
            }}
          />
          <Button type="submit" size="xs" className="self-end" disabled={!body.trim() || add.isPending}>
            Comment
          </Button>
        </form>
      ) : null}
    </div>
  );
}
