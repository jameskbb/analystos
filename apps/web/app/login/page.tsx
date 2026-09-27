"use client";
import * as React from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { auth } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { ApiError, setCsrfToken } from "@/lib/api/client";
import { useSession } from "@/components/providers/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError, LoadingState } from "@/components/states/states";

function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/";
}

/** The first account on a remote server needs the bootstrap token (or a local session). */
function tokenRequired(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    (err.code === "signup_requires_local_access" || err.code === "bootstrap_token_required" || /bootstrap/i.test(err.message))
  );
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const qc = useQueryClient();
  const { data: session, isLoading } = useSession();
  const [mode, setMode] = React.useState<"login" | "signup">("login");
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [setupToken, setSetupToken] = React.useState("");
  const [showToken, setShowToken] = React.useState(false);

  React.useEffect(() => {
    if (session?.authenticated) router.replace(next);
  }, [session?.authenticated, next, router]);

  const submit = useMutation({
    mutationFn: () =>
      mode === "login" ? auth.login(email, password) : auth.signup(email, name, password, setupToken.trim() || undefined),
    onSuccess: (s) => {
      setCsrfToken(s.csrf_token);
      qc.setQueryData(qk.session, s);
      router.replace(next);
    },
  });

  if (isLoading || session?.authenticated) return <LoadingState variant="block" className="h-dvh" label="Checking session" />;

  const signupAllowed = session?.signup_allowed ?? false;
  return (
    <div className="flex h-dvh items-center justify-center bg-bg-subtle px-4">
      <form
        className="w-full max-w-xs rounded-md border border-border bg-bg p-5"
        onSubmit={(e) => {
          e.preventDefault();
          submit.mutate();
        }}
      >
        <div className="mb-4 flex items-center gap-2">
          <svg viewBox="0 0 16 16" className="size-5" aria-hidden>
            <rect x="0.5" y="0.5" width="15" height="15" rx="3" fill="var(--fg)" />
            <path d="M4 4.5h3M5.5 4.5v7M5.5 8h4M5.5 11.5h6" stroke="var(--bg)" strokeWidth="1.4" strokeLinecap="round" fill="none" />
          </svg>
          <h1 className="text-base font-semibold">{mode === "login" ? "Sign in to AnalystOS" : "Create your account"}</h1>
        </div>
        {session?.local_mode_denied ? (
          <p className="mb-3 rounded border border-border bg-bg-subtle px-2 py-1.5 text-xs text-fg-muted" role="note">
            This server runs in single-user local mode, which only works from the machine it runs on. Sign in with an
            account instead.
          </p>
        ) : null}
        <div className="flex flex-col gap-3">
          {mode === "signup" ? (
            <Field label="Name" htmlFor="name">
              <Input id="name" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
          ) : null}
          <Field label="Email" htmlFor="email">
            <Input id="email" type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Password" htmlFor="password" hint={mode === "signup" ? "At least 10 characters." : undefined}>
            <Input
              id="password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          {mode === "signup" && (showToken || session?.bootstrap_token_required || tokenRequired(submit.error)) ? (
            <Field label="Setup token" htmlFor="setup-token" hint="Set by the administrator (AOS_BOOTSTRAP_TOKEN) to create the first account remotely.">
              <Input id="setup-token" autoComplete="off" value={setupToken} onChange={(e) => setSetupToken(e.target.value)} />
            </Field>
          ) : null}
          <InlineError error={submit.error} />
          <Button type="submit" variant="primary" size="md" disabled={submit.isPending}>
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
          {mode === "signup" && !showToken && !session?.bootstrap_token_required ? (
            <button type="button" className="text-left text-xs text-fg-subtle hover:underline" onClick={() => setShowToken(true)}>
              Have a setup token?
            </button>
          ) : null}
          {signupAllowed ? (
            <button
              type="button"
              className="text-xs text-accent hover:underline"
              onClick={() => setMode(mode === "login" ? "signup" : "login")}
            >
              {mode === "login" ? "Need an account? Sign up" : "Have an account? Sign in"}
            </button>
          ) : null}
        </div>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" className="h-dvh" />}>
      <LoginForm />
    </Suspense>
  );
}
