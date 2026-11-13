// Appearance: light, dark, or the device's (auto), kept in this browser and set on <html> as
// data-theme. static/theme.js reads the same key before the first paint, so a reload never flashes.
import { PRODUCT } from "./product";

export type Theme = "auto" | "light" | "dark";
export const THEME_KEY = `${PRODUCT}.theme`;

export function themeNow(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "auto";
  } catch {
    return "auto";
  }
}

export function setTheme(t: Theme): void {
  try {
    if (t === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, t);
  } catch {
    /* storage off: this page only */
  }
  const html = document.documentElement;
  if (t === "auto") html.removeAttribute("data-theme");
  else html.setAttribute("data-theme", t);
}

/** the choice in words */
export function themeWords(t: Theme): string {
  const dark = typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
  return t === "auto" ? `Your device's (${dark ? "dark" : "light"} now)` : t === "dark" ? "Dark" : "Light";
}
