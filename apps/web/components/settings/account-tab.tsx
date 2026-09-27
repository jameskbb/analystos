"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { auth } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { setCsrfToken } from "@/lib/api/client";
import { useSession } from "@/components/providers/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError, LoadingState } from "@/components/states/states";
import { DefinitionList } from "@/components/shell/page";
import { formatDateTime } from "@/lib/format";
import { SettingsSection } from "./shared";

export const MIN_PASSWORD = 10;

function CreateAccount() {
  const qc = useQueryClient();
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const signup = useMutation({
    mutationFn: () => auth.signup(email.trim(), name.trim(), password),
    onSuccess: (s) => {
      setCsrfToken(s.csrf_token);
      qc.setQueryData(qk.session, s);
      toast.success("Account created", { description: "Your workspaces now belong to this account." });
    },
  });
  return (
    <form
      className="flex max-w-sm flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        signup.mutate();
      }}
    >
      <Field label="Name" htmlFor="acc-name">
        <Input id="acc-name" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Email" htmlFor="acc-email">
        <Input id="acc-email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password" htmlFor="acc-pass" hint={`At least ${MIN_PASSWORD} characters.`}>
        <Input
          id="acc-pass"
          type="password"
          autoComplete="new-password"
          minLength={MIN_PASSWORD}
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Field>
      <InlineError error={signup.error} />
      <div>
        <Button type="submit" variant="primary" disabled={signup.isPending || password.length < MIN_PASSWORD}>
          Create account
        </Button>
      </div>
    </form>
  );
}

function Profile() {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const user = session?.user;
  const [name, setName] = React.useState(user?.name ?? "");
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  React.useEffect(() => setName(user?.name ?? ""), [user?.name]);

  const saveName = useMutation({
    mutationFn: () => auth.updateMe({ name: name.trim() }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.session });
      toast.success("Name updated");
    },
  });
  const changePassword = useMutation({
    mutationFn: () => auth.updateMe({ current_password: current, new_password: next, revoke_tokens: revokeTokens }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success(revokeTokens ? "Password changed; other sessions and API tokens revoked" : "Password changed; other sessions signed out");
    },
  });
  const [revokeTokens, setRevokeTokens] = React.useState(false);
  const mismatch = confirm.length > 0 && next !== confirm;

  return (
    <>
      <SettingsSection title="Profile">
        <form
          className="flex max-w-sm flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) saveName.mutate();
          }}
        >
          <Field label="Name" htmlFor="me-name">
            <Input id="me-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <InlineError error={saveName.error} />
          <div>
            <Button type="submit" variant="primary" disabled={!name.trim() || name.trim() === user?.name || saveName.isPending}>
              Save
            </Button>
          </div>
        </form>
        <DefinitionList
          className="mt-4 max-w-sm text-xs"
          items={[
            { label: "Email", value: user?.email ?? "n/a" },
            { label: "Last sign-in", value: formatDateTime(user?.last_login_at) },
            { label: "Member since", value: formatDateTime(user?.created_at) },
          ]}
        />
      </SettingsSection>
      <SettingsSection title="Password" description="Changing your password keeps this session signed in and signs out your other sessions.">
        <form
          className="flex max-w-sm flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!mismatch) changePassword.mutate();
          }}
        >
          <Field label="Current password" htmlFor="pw-current">
            <Input id="pw-current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
          <Field label="New password" htmlFor="pw-new" hint={`At least ${MIN_PASSWORD} characters.`}>
            <Input id="pw-new" type="password" autoComplete="new-password" minLength={MIN_PASSWORD} required value={next} onChange={(e) => setNext(e.target.value)} />
          </Field>
          <Field label="Confirm new password" htmlFor="pw-confirm" error={mismatch ? "Passwords do not match" : undefined}>
            <Input id="pw-confirm" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={revokeTokens} onChange={(e) => setRevokeTokens(e.target.checked)} />
            Also revoke my API tokens
          </label>
          <InlineError error={changePassword.error} />
          <div>
            <Button type="submit" variant="primary" disabled={!current || next.length < MIN_PASSWORD || mismatch || changePassword.isPending}>
              Change password
            </Button>
          </div>
        </form>
      </SettingsSection>
    </>
  );
}

export function AccountTab() {
  const { data: session, isLoading } = useSession();
  if (isLoading || !session) return <LoadingState rows={3} />;
  const local = session.auth_mode === "local" || session.user?.is_local;
  if (local) {
    return (
      <SettingsSection
        title="Local mode"
        description="AnalystOS is running as a single local analyst with no password, which suits a personal machine."
      >
        {session.signup_allowed ? (
          <>
            <p className="mb-3 max-w-lg text-sm text-fg-muted">
              Create an account to require sign-in and invite colleagues. Your existing workspaces move to the new
              account.
            </p>
            <CreateAccount />
          </>
        ) : (
          <p className="max-w-lg text-sm text-fg-muted">
            The server is configured for local mode only (<code className="font-mono text-xs">AUTH_MODE=local</code>).
            Set <code className="font-mono text-xs">AUTH_MODE=auto</code> or <code className="font-mono text-xs">password</code> to enable accounts.
          </p>
        )}
      </SettingsSection>
    );
  }
  return <Profile />;
}
