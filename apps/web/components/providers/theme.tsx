"use client";
import * as React from "react";

export type ThemePref = "light" | "dark" | "system";
const STORAGE_KEY = "aos-theme";

interface ThemeCtx {
  pref: ThemePref;
  resolved: "light" | "dark";
  setPref: (p: ThemePref) => void;
  toggle: () => void;
}

const Ctx = React.createContext<ThemeCtx | null>(null);

/** Inline script placed in <head> so the theme is applied before first paint (no flash). */
export const themeInitScript = `(function(){try{var p=localStorage.getItem('${STORAGE_KEY}')||'system';var d=p==='dark'||(p==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.dataset.theme=d?'dark':'light';}catch(e){document.documentElement.dataset.theme='light';}})();`;

function systemDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [pref, setPrefState] = React.useState<ThemePref>("system");
  const [sys, setSys] = React.useState<"light" | "dark">("light");

  React.useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as ThemePref | null;
      if (stored === "light" || stored === "dark" || stored === "system") setPrefState(stored);
    } catch {
      /* storage unavailable: keep system default */
    }
    setSys(systemDark() ? "dark" : "light");
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSys(mq.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const resolved = pref === "system" ? sys : pref;

  React.useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    window.dispatchEvent(new CustomEvent("aos-theme-change", { detail: resolved }));
  }, [resolved]);

  const setPref = React.useCallback((p: ThemePref) => {
    setPrefState(p);
    try {
      localStorage.setItem(STORAGE_KEY, p);
    } catch {
      /* ignore */
    }
  }, []);

  const value = React.useMemo<ThemeCtx>(
    () => ({ pref, resolved, setPref, toggle: () => setPref(resolved === "dark" ? "light" : "dark") }),
    [pref, resolved, setPref],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = React.useContext(Ctx);
  if (!ctx) {
    return { pref: "light", resolved: "light", setPref: () => {}, toggle: () => {} };
  }
  return ctx;
}
