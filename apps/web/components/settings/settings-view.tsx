"use client";
import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Building2, Users, KeyRound, Cpu, CalendarDays, UserRound } from "lucide-react";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useWorkspace } from "@/components/providers/workspace";
import { WorkspaceTab } from "./workspace-tab";
import { MembersTab } from "./members-tab";
import { TokensTab } from "./tokens-tab";
import { AiTab } from "./ai-tab";
import { CalendarTab } from "./calendar-tab";
import { AccountTab } from "./account-tab";

export const SETTINGS_TABS = [
  { id: "workspace", label: "Workspace", icon: Building2, render: () => <WorkspaceTab /> },
  { id: "members", label: "Members", icon: Users, render: () => <MembersTab /> },
  { id: "tokens", label: "API tokens", icon: KeyRound, render: () => <TokensTab /> },
  { id: "ai", label: "AI provider", icon: Cpu, render: () => <AiTab /> },
  { id: "calendar", label: "Calendar", icon: CalendarDays, render: () => <CalendarTab /> },
  { id: "account", label: "Account", icon: UserRound, render: () => <AccountTab /> },
] as const;

type TabId = (typeof SETTINGS_TABS)[number]["id"];

export function SettingsView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { workspace } = useWorkspace();
  const requested = params.get("tab");
  const tab: TabId = SETTINGS_TABS.some((t) => t.id === requested) ? (requested as TabId) : "workspace";
  const setTab = (t: string) => {
    const usp = new URLSearchParams(params.toString());
    usp.set("tab", t);
    router.replace(`${pathname}?${usp.toString()}`, { scroll: false });
  };
  return (
    <Page>
      <PageHeader title="Settings" description={workspace ? `${workspace.name} · your role: ${workspace.role ?? "member"}` : undefined} />
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="px-3 sm:px-4" aria-label="Settings sections">
          {SETTINGS_TABS.map((t) => (
            <TabsTrigger key={t.id} value={t.id}>
              <t.icon aria-hidden /> {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <PageBody>
          <div className="mx-auto max-w-5xl">
            {SETTINGS_TABS.map((t) => (
              <TabsContent key={t.id} value={t.id}>
                {tab === t.id ? t.render() : null}
              </TabsContent>
            ))}
          </div>
        </PageBody>
      </Tabs>
    </Page>
  );
}
