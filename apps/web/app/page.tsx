"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { workspaces } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useRequireSession } from "@/components/providers/session";
import { LAST_WS_KEY } from "@/components/providers/workspace";
import { ErrorState, LoadingState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { LoadDemoButton } from "@/components/shell/use-load-demo";

/** Entry: send the user to their last (or default) workspace, or offer to create one. */
export default function EntryPage() {
  const router = useRouter();
  const { session, isLoading, error } = useRequireSession();
  const list = useQuery({ queryKey: qk.workspaces, queryFn: workspaces.list, enabled: !!session?.authenticated });
  const [name, setName] = React.useState("");
  // Exactly one navigation leaves this page: whichever of "existing workspace", "demo loaded" or
  // "workspace created" happens first. Later triggers (e.g. the refetched workspace list after the
  // demo load) are ignored, so the page never races two router transitions.
  const navigated = React.useRef(false);
  const go = React.useCallback(
    (wsId: string) => {
      if (navigated.current) return;
      navigated.current = true;
      try {
        localStorage.setItem(LAST_WS_KEY, wsId);
      } catch {
        /* ignore */
      }
      router.replace(`/w/${wsId}`);
    },
    [router],
  );
  const create = useMutation({
    mutationFn: () => workspaces.create({ name: name.trim() || "My workspace" }),
    onSuccess: (ws) => go(ws.id),
  });

  React.useEffect(() => {
    if (!list.data?.length || navigated.current) return;
    let last: string | null = null;
    try {
      last = localStorage.getItem(LAST_WS_KEY);
    } catch {
      /* ignore */
    }
    const target =
      list.data.find((w) => w.id === last)?.id ??
      list.data.find((w) => w.id === session?.default_workspace_id)?.id ??
      list.data[0].id;
    go(target);
  }, [list.data, session?.default_workspace_id, go]);

  if (error) return <ErrorState error={error} onRetry={() => window.location.reload()} className="h-dvh" />;
  if (list.error) return <ErrorState error={list.error} onRetry={() => void list.refetch()} className="h-dvh" />;
  if (isLoading || !session?.authenticated || list.isLoading || (list.data && list.data.length > 0))
    return <LoadingState variant="block" label="Opening AnalystOS" className="h-dvh" />;

  return (
    <div className="flex h-dvh items-center justify-center bg-bg-subtle px-4">
      <div className="w-full max-w-sm rounded-md border border-border bg-bg p-5">
        <h1 className="text-lg font-semibold">Welcome to AnalystOS</h1>
        <p className="mt-1 text-sm text-fg-subtle">
          Start with the Summit Supply Co. demo (a building-materials distributor with planted analytical stories), or
          create an empty workspace and upload your own data.
        </p>
        <div className="mt-4 flex flex-col gap-2">
          <LoadDemoButton className="w-full" size="md" onLoaded={(wsId) => (wsId ? go(wsId) : void list.refetch())} />
          <div className="my-2 flex items-center gap-2 text-xs text-fg-faint">
            <span className="h-px flex-1 bg-border" /> or <span className="h-px flex-1 bg-border" />
          </div>
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <Field label="Workspace name" htmlFor="new-ws">
              <Input id="new-ws" value={name} onChange={(e) => setName(e.target.value)} placeholder="Retail Operations" />
            </Field>
            <Button type="submit" size="md" disabled={create.isPending}>
              Create empty workspace
            </Button>
            {create.error ? <p className="text-xs text-negative">{create.error.message}</p> : null}
          </form>
        </div>
      </div>
    </div>
  );
}
