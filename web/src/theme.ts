import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "canopy-theme";

function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

/**
 * Resolved theme plus a toggle. With no stored choice the app follows
 * prefers-color-scheme (demo.css supplies the tokens); toggling writes an
 * explicit data-theme override and persists it.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [stored, setStored] = useState<Theme | null>(storedTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media?.addEventListener) return;
    const onChange = () => setSystem(media.matches ? "dark" : "light");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (stored) document.documentElement.dataset.theme = stored;
    else delete document.documentElement.dataset.theme;
    try {
      if (stored) window.localStorage.setItem(STORAGE_KEY, stored);
    } catch {
      /* storage unavailable — theme still applies for the session */
    }
  }, [stored]);

  const theme = stored ?? system;
  return {
    theme,
    toggle: () => setStored(theme === "dark" ? "light" : "dark"),
  };
}
