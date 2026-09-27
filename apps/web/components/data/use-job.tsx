"use client";
import * as React from "react";
import { waitForJob } from "@/lib/api/endpoints";
import type { Job, JobAccepted } from "@/lib/api/types";

/**
 * Runs an API call that returns `JobAccepted` and polls it to completion, exposing progress text.
 * Returns the finished job (throws with the job's error when it fails).
 */
export function useJobRunner() {
  const [progress, setProgress] = React.useState<{ pct: number; message: string } | null>(null);
  const ctrl = React.useRef<AbortController | null>(null);

  React.useEffect(() => () => ctrl.current?.abort(), []);

  const run = React.useCallback(async (start: () => Promise<JobAccepted>): Promise<Job> => {
    ctrl.current?.abort();
    ctrl.current = new AbortController();
    setProgress({ pct: 0, message: "Queued" });
    try {
      const accepted = await start();
      setProgress({ pct: accepted.job.progress ?? 0, message: accepted.job.message || "Queued" });
      return await waitForJob(
        accepted,
        (j) => setProgress({ pct: j.progress ?? 0, message: j.message || j.status }),
        ctrl.current.signal,
      );
    } finally {
      setProgress(null);
    }
  }, []);

  return { run, progress };
}

export function JobProgress({ progress }: { progress: { pct: number; message: string } | null }) {
  if (!progress) return null;
  const pct = Math.max(0, Math.min(1, progress.pct));
  return (
    <div className="flex flex-col gap-1" role="status" aria-live="polite">
      <div className="h-1 w-full overflow-hidden rounded-full bg-bg-muted">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-300"
          style={{ width: `${Math.max(pct * 100, 4)}%` }}
        />
      </div>
      <div className="text-xs text-fg-subtle">{progress.message}</div>
    </div>
  );
}
