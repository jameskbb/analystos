"use client";
import { use } from "react";
import { Workstation } from "@/components/investigation/workstation";

export default function InvestigationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Workstation investigationId={id} />;
}
