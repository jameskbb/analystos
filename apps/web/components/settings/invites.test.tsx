import * as React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace }), usePathname: () => "/" }));

const api = {
  list: vi.fn(),
  create: vi.fn(),
  revoke: vi.fn(),
  preview: vi.fn(),
  accept: vi.fn(),
};
vi.mock("@/lib/api/endpoints", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/endpoints")>();
  return {
    ...actual,
    workspaces: { ...actual.workspaces, get: vi.fn(async () => ({ id: "ws1", name: "WS", role: "owner" })) },
    auth: { ...actual.auth, session: vi.fn(async () => ({ authenticated: false, auth_mode: "password", user: null, signup_allowed: false })) },
    invites: {
      list: (...a: unknown[]) => api.list(...a),
      create: (...a: unknown[]) => api.create(...a),
      revoke: (...a: unknown[]) => api.revoke(...a),
      preview: (...a: unknown[]) => api.preview(...a),
      accept: (...a: unknown[]) => api.accept(...a),
    },
  };
});

import { InvitesSection, inviteLink } from "./invites";
import AcceptInvitePage from "@/app/invite/[token]/page";
import { WorkspaceProvider } from "@/components/providers/workspace";

const pending = {
  id: "i1",
  email: "sam@summit.test",
  role: "editor",
  status: "pending",
  created_by: "u1",
  created_at: "2026-09-18T00:00:00Z",
  expires_at: "2026-09-25T00:00:00Z",
  accepted_at: null,
  revoked_at: null,
};

/** The page unwraps `params` with `use()`, which suspends; render inside an awaited act. */
async function renderPage(token: string) {
  const params = Promise.resolve({ token });
  await act(async () => {
    wrap(
      <React.Suspense fallback={null}>
        <AcceptInvitePage params={params} />
      </React.Suspense>,
    );
  });
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("invites (owner)", () => {
  beforeEach(() => Object.values(api).forEach((f) => f.mockReset()));

  it("builds the absolute link from accept_path", () => {
    expect(inviteLink("/invite/abc", "http://127.0.0.1:3000/")).toBe("http://127.0.0.1:3000/invite/abc");
  });

  it("creates an invite, shows its link once, lists and revokes invites", async () => {
    api.list.mockResolvedValue([pending]);
    api.create.mockResolvedValue({ token: "tok123", accept_path: "/invite/tok123", invite: pending });
    api.revoke.mockResolvedValue({ ok: true });
    wrap(
      <WorkspaceProvider id="ws1">
        <InvitesSection />
      </WorkspaceProvider>,
    );
    await userEvent.type(screen.getByLabelText("Email"), "sam@summit.test");
    await userEvent.selectOptions(screen.getByLabelText("Role"), "editor");
    await userEvent.click(screen.getByRole("button", { name: /Create invite/ }));
    await waitFor(() => expect(api.create).toHaveBeenCalledWith("ws1", { email: "sam@summit.test", role: "editor" }));
    expect(await screen.findByTestId("invite-link")).toHaveTextContent(/\/invite\/tok123$/);
    const table = await screen.findByRole("table", { name: "Invites" });
    expect(within(table).getByText("pending")).toBeInTheDocument();
    await userEvent.click(within(table).getByRole("button", { name: "Revoke invite for sam@summit.test" }));
    await waitFor(() => expect(api.revoke).toHaveBeenCalledWith("ws1", "i1"));
  });
});

describe("accept invite page", () => {
  beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset());
    replace.mockReset();
  });

  it("lets a new person set a password and opens the workspace", async () => {
    api.preview.mockResolvedValue({ workspace_name: "Summit Supply Co.", email: "sam@summit.test", role: "editor", expires_at: "2026-09-25T00:00:00Z", account_exists: false });
    api.accept.mockResolvedValue({ authenticated: true, auth_mode: "password", user: null, csrf_token: "c", signup_allowed: false, default_workspace_id: "ws9" });
    await renderPage("tok123");
    expect(await screen.findByRole("heading", { name: "Join Summit Supply Co." })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your name"), "Sam");
    await userEvent.type(screen.getByLabelText("Choose a password"), "a-long-password");
    await userEvent.type(screen.getByLabelText("Confirm password"), "a-long-password");
    await userEvent.click(screen.getByRole("button", { name: "Create account and join" }));
    await waitFor(() => expect(api.accept).toHaveBeenCalledWith({ token: "tok123", name: "Sam", password: "a-long-password" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/w/ws9"));
  });

  it("asks existing accounts to sign in first", async () => {
    api.preview.mockResolvedValue({ workspace_name: "Summit", email: "sam@summit.test", role: "viewer", expires_at: "2026-09-25T00:00:00Z", account_exists: true });
    await renderPage("tok123");
    expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=%2Finvite%2Ftok123");
  });

  it("explains invalid links", async () => {
    api.preview.mockRejectedValue(new Error("invite_invalid"));
    await renderPage("bad");
    expect(await screen.findByText("This invite link is not valid")).toBeInTheDocument();
  });
});
