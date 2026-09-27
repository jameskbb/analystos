"use client";
import { Suspense } from "react";
import { SettingsView } from "@/components/settings/settings-view";
import { LoadingState } from "@/components/states/states";

export default function SettingsPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" label="Loading settings" />}>
      <SettingsView />
    </Suspense>
  );
}
