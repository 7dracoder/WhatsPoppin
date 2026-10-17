import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

export const THEMES = [
  { id: "soft-pixel-finance", name: "Soft Pixel Finance" },
  { id: "peachy-market", name: "Peachy Market" },
  { id: "candy-radar", name: "Candy Radar" },
  { id: "garden-signals", name: "Garden Signals" },
  { id: "pastel-observatory", name: "Pastel Observatory" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];
export type ThemeMode = "light" | "dark";

const STORAGE_KEY_THEME = "whatspoppin.theme";
const STORAGE_KEY_MODE = "whatspoppin.mode";
const DEFAULT_THEME: ThemeId = "soft-pixel-finance";

interface ThemeContextValue {
  theme: ThemeId;
  mode: ThemeMode;
  setTheme: (theme: ThemeId) => void;
  setMode: (mode: ThemeMode) => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY_THEME);
    return (THEMES.find((t) => t.id === stored)?.id ?? DEFAULT_THEME) as ThemeId;
  } catch {
    return DEFAULT_THEME;
  }
}

function readStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY_MODE);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // ignore
  }
  // Per design spec: mode (not theme) falls back to the OS preference.
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(readStoredTheme);
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-mode", mode);
  }, [theme, mode]);

  const setTheme = useCallback((next: ThemeId) => {
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY_THEME, next);
    } catch {
      // best-effort persistence only
    }
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY_MODE, next);
    } catch {
      // best-effort persistence only
    }
  }, []);

  const value = useMemo(() => ({ theme, mode, setTheme, setMode }), [theme, mode, setTheme, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
