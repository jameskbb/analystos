"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter, usePathname } from "next/navigation";
import { auth } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { setCsrfToken, setUnauthorizedHandler } from "@/lib/api/client";
import type { SessionInfo } from "@/lib/api/types";

export function useSession() {
  const query = useQuery({ queryKey: qk.session, queryFn: auth.session, staleTime: 60_000 });
  React.useEffect(() => {
    if (query.data?.csrf_token) setCsrfToken(query.data.csrf_token);
  }, [query.data?.csrf_token]);
  return query;
}

/** Redirects to /login when the API reports an unauthenticated password-mode session. */
export function useRequireSession(): { session: SessionInfo | undefined; isLoading: boolean; error: unknown } {
  const router = useRouter();
  const pathname = usePathname();
  const { data, isLoading, error } = useSession();

  React.useEffect(() => {
    setUnauthorizedHandler(() => {
      if (!window.location.pathname.startsWith("/login")) {
        router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [router]);

  React.useEffect(() => {
    if (data && !data.authenticated) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [data, pathname, router]);

  return { session: data, isLoading, error };
}
