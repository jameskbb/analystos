"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { PackageOpen } from "lucide-react";
import { demo, isJobAccepted, waitForJob } from "@/lib/api/endpoints";
import type { DemoStatus } from "@/lib/api/types";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

/**
 * Loads the Summit Supply Co. demo (data, semantic model, metric trees, glossary, DQ rules).
 * Navigation happens exactly once: through `onLoaded` when the caller owns routing (the entry
 * page), otherwise by pushing to the demo workspace when it differs from the current one.
 */
export function useLoadDemo(workspaceId?: string | null, onLoaded?: (workspaceId: string | null) => void) {
  const qc = useQueryClient();
  const router = useRouter();
  const [progress, setProgress] = React.useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (): Promise<DemoStatus> => {
      setProgress("Starting");
      const res: unknown = await demo.load({ workspace_id: workspaceId ?? null });
      const returnedWs = (res as { workspace_id?: string | null } | null)?.workspace_id ?? null;
      if (isJobAccepted(res)) {
        const job = await waitForJob(res, (j) => setProgress(j.message || `${Math.round(j.progress * 100)}%`));
        const wsId =
          returnedWs ?? (job.result?.workspace_id as string | undefined) ?? job.resource_id ?? workspaceId ?? null;
        return { loaded: true, workspace_id: wsId };
      }
      return { loaded: true, workspace_id: returnedWs ?? workspaceId ?? null, ...(res as Partial<DemoStatus>) };
    },
    onSuccess: async (res) => {
      setProgress(null);
      toast.success("Summit Supply demo loaded", {
        description: "Datasets, semantic model, metric trees, glossary and quality rules are ready.",
      });
      // Navigate before refetching: a refetched workspace list must not trigger a second navigation.
      if (onLoaded) onLoaded(res.workspace_id ?? null);
      else if (res.workspace_id && res.workspace_id !== workspaceId) router.push(`/w/${res.workspace_id}`);
      // The session is unchanged by loading data; refetching it would only re-render the shell.
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "session" });
    },
    onError: (e: Error) => {
      setProgress(null);
      toast.error("Could not load the demo", { description: e.message });
    },
  });
  return { ...mutation, progress };
}

export function LoadDemoButton({
  workspaceId,
  label = "Load Summit Supply demo",
  onLoaded,
  ...props
}: { workspaceId?: string | null; label?: string; onLoaded?: (workspaceId: string | null) => void } & Omit<
  ButtonProps,
  "onClick"
>) {
  const { mutate, isPending, progress } = useLoadDemo(workspaceId, onLoaded);
  return (
    <Button variant="primary" onClick={() => mutate()} disabled={isPending} {...props}>
      {isPending ? <Spinner className="text-accent-fg" /> : <PackageOpen />}
      {isPending ? `Loading demo${progress ? ` · ${progress}` : "…"}` : label}
    </Button>
  );
}
