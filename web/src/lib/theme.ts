// Theme controller — a simple Light ↔ Dark toggle reflected on <html data-theme>,
// which the CSS tokens key off. First-time default follows the OS once; after that
// the user's explicit choice is remembered.

export type Theme = "light" | "dark";

const KEY = "qa_theme";

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function getTheme(): Theme {
  const v = localStorage.getItem(KEY);
  if (v === "light" || v === "dark") return v;
  return systemPrefersDark() ? "dark" : "light";
}

export function applyTheme(theme: Theme = getTheme()): void {
  document.documentElement.dataset.theme = theme;
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(KEY, theme);
  applyTheme(theme);
}
