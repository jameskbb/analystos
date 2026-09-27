"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { workspaces } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { Workspace } from "@/lib/api/types";

export const LAST_WS_KEY = "aos-last-workspace";

interface WorkspaceCtx {
  id: string;
  workspace: Workspace | undefined;
  /** Builds an in-app href under the current workspace: href("/findings/abc") → /w/{ws}/findings/abc */
  href: (path?: string) => string;
  canEdit: boolean;
}

const Ctx = React.createContext<WorkspaceCtx | null>(null);

export function WorkspaceProvider({ id, children }: { id: string; children: React.ReactNode }) {
  const { data } = useQuery({ queryKey: qk.workspace(id), queryFn: () => workspaces.get(id) });
  React.useEffect(() => {
    try {
      localStorage.setItem(LAST_WS_KEY, id);
    } catch {
      /* ignore */
    }
  }, [id]);
  const value = React.useMemo<WorkspaceCtx>(
    () => ({
      id,
      workspace: data,
      href: (path = "") => `/w/${id}${path && !path.startsWith("/") ? "/" : ""}${path}`,
      canEdit: !data?.role || data.role !== "viewer",
    }),
    [id, data],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): WorkspaceCtx {
  const ctx = React.useContext(Ctx);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}

/** For components that may render outside a workspace (tests, login). */
export function useOptionalWorkspace(): WorkspaceCtx | null {
  return React.useContext(Ctx);
}
