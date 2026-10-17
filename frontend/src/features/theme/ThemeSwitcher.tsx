import { Moon, Sun } from "@phosphor-icons/react";
import { THEMES } from "./ThemeProvider";
import { useTheme } from "./useTheme";

/** The visible 5-dot theme switcher + light/dark toggle, per frontend-design-spec.md. */
export function ThemeSwitcher() {
  const { theme, mode, setTheme, setMode } = useTheme();

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1.5" role="radiogroup" aria-label="Theme">
        {THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={theme === t.id}
            title={t.name}
            onClick={() => setTheme(t.id)}
            className="h-5 w-5 rounded-full border-2 transition"
            style={{
              borderColor: theme === t.id ? "var(--text-primary)" : "transparent",
            }}
          >
            <span
              className="block h-full w-full rounded-full"
              data-theme={t.id}
              style={{ background: "var(--accent)" }}
            />
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={() => setMode(mode === "light" ? "dark" : "light")}
        className="rounded-full p-1.5 text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
        aria-label={mode === "light" ? "Switch to dark mode" : "Switch to light mode"}
      >
        {mode === "light" ? <Moon size={16} weight="bold" /> : <Sun size={16} weight="bold" />}
      </button>
    </div>
  );
}
