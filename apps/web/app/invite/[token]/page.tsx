"use client";
import * as React from "react";
import { use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { invites } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { setCsrfToken } from "@/lib/api/client";
import { useSession } from "@/components/providers/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ErrorState, InlineError, LoadingState } from "@/components/states/states";
import { formatDate } from "@/lib/format";

const MIN_PASSWORD = 10;

/**
 * Accept a workspace invite. New people choose a name and password (the account, membership and a
 * session are created in one step); people who already have an account sign in as the invited
 * email first and accept with that session.
 */
export default function AcceptInvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const qc = useQueryClient();
  const { data: session } = useSession();
  const preview = useQuery({ queryKey: ["invite", token], queryFn: () => invites.preview(token), retry: false });
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");

  const accept = useMutation({
    mutationFn: () =>
      preview.data?.account_exists ? invites.accept({ token }) : invites.accept({ token, name: name.trim(), password }),
    onSuccess: (s) => {
      setCsrfToken(s.csrf_token);
      qc.setQueryData(qk.session, s);
      void qc.invalidateQueries({ queryKey: qk.workspaces });
      router.replace(s.default_workspace_id ? `/w/${s.default_workspace_id}` : "/");
    },
  });

  if (preview.isLoading) return <LoadingState variant="block" className="h-dvh" label="Checking invite" />;
  if (preview.error)
    return (
      <ErrorState
        className="h-dvh"
        title="This invite link is not valid"
        error={new Error("It may have been used already, revoked or expired. Ask the workspace owner for a new invite.")}
      />
    );
  const inv = preview.data!;
  const signedInAs = session?.authenticated ? session.user?.email : null;
  const mismatch = confirm.length > 0 && confirm !== password;
  const next = encodeURIComponent(`/invite/${token}`);

  return (
    <div className="flex h-dvh items-center justify-center bg-bg-subtle px-4">
      <div className="w-full max-w-sm rounded-md border border-border bg-bg p-5">
        <h1 className="text-base font-semibold">Join {inv.workspace_name}</h1>
        <p className="mt-1 text-sm text-fg-muted">
          You are invited as <span className="font-medium">{inv.role}</span> with <span className="font-medium">{inv.email}</span>. The
          link works once and expires {formatDate(inv.expires_at)}.
        </p>
        {inv.account_exists ? (
          <div className="mt-4 flex flex-col gap-3">
            {signedInAs && signedInAs.toLowerCase() === inv.email.toLowerCase() ? (
              <Button variant="primary" size="md" disabled={accept.isPending} onClick={() => accept.mutate()}>
                Accept and open the workspace
              </Button>
            ) : (
              <>
                <p className="text-sm">
                  An account with this email exists. Sign in as {inv.email}
                  {signedInAs ? ` (you are signed in as ${signedInAs})` : ""}, then come back to this link to accept.
                </p>
                <Button asChild variant="primary" size="md">
                  <Link href={`/login?next=${next}`}>Sign in</Link>
                </Button>
              </>
            )}
            <InlineError error={accept.error} />
          </div>
        ) : (
          <form
            className="mt-4 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!mismatch) accept.mutate();
            }}
          >
            <Field label="Email" htmlFor="inv-email">
              <Input id="inv-email" value={inv.email} readOnly disabled />
            </Field>
            <Field label="Your name" htmlFor="inv-name">
              <Input id="inv-name" autoComplete="name" required autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Choose a password" htmlFor="inv-password" hint={`At least ${MIN_PASSWORD} characters.`}>
              <Input
                id="inv-password"
                type="password"
                autoComplete="new-password"
                minLength={MIN_PASSWORD}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <Field label="Confirm password" htmlFor="inv-confirm" error={mismatch ? "Passwords do not match" : undefined}>
              <Input id="inv-confirm" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
            <InlineError error={accept.error} />
            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={!name.trim() || password.length < MIN_PASSWORD || mismatch || accept.isPending}
            >
              Create account and join
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
