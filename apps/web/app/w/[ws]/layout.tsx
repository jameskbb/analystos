"use client";
import * as React from "react";
import { use } from "react";
import { useRequireSession } from "@/components/providers/session";
import { WorkspaceProvider } from "@/components/providers/workspace";
import { AppShell } from "@/components/shell/app-shell";
import { ErrorState, LoadingState } from "@/components/states/states";

export default function WorkspaceLayout({ children, params }: { children: React.ReactNode; params: Promise<{ ws: string }> }) {
  const { ws } = use(params);
  const { session, isLoading, error } = useRequireSession();
  if (error) return <ErrorState error={error} onRetry={() => window.location.reload()} className="h-dvh" />;
  if (isLoading || !session?.authenticated) return <LoadingState variant="block" className="h-dvh" label="Loading workspace" />;
  return (
    <WorkspaceProvider id={ws}>
      <AppShell>{children}</AppShell>
    </WorkspaceProvider>
  );
}
