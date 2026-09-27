"use client";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { ApiError } from "@/lib/api/client";
import { ThemeProvider, useTheme } from "./theme";
import { TooltipProvider } from "@/components/ui/tooltip";

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => {
          if (err instanceof ApiError && err.status >= 400 && err.status < 500) return false;
          return count < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

function ThemedToaster() {
  const { resolved } = useTheme();
  return (
    <Toaster
      theme={resolved}
      position="bottom-right"
      toastOptions={{
        className: "!rounded-md !border !border-border-strong !bg-bg !text-fg !text-sm !shadow-pop",
      }}
    />
  );
}

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(makeClient);
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <TooltipProvider delayDuration={300}>
          {children}
          <ThemedToaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
