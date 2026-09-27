"use client";
import { ErrorState } from "@/components/states/states";

export default function WorkspaceError({ error, reset }: { error: Error; reset: () => void }) {
  return <ErrorState error={error} title="This screen failed to render" onRetry={reset} className="h-full" />;
}
