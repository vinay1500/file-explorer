/**
 * Which theme the page opens in, and why it is not the operating system's.
 *
 * The first version set `color-scheme: light dark` and nothing else, so the
 * page took whatever the visitor's machine was set to and offered no way out.
 * For a portfolio demo that is the wrong default twice over: the first thing
 * an interviewer sees is decided by their laptop rather than by this project,
 * and a dark-mode machine got a dark screen with no affordance to change it.
 *
 * So: **it opens light for everybody, and the toggle is one click.** A choice
 * is remembered per browser; nothing is remembered server-side, because the
 * theme is not the session's business.
 */

export type Theme = "light" | "dark";

const KEY = "fe.theme";

export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    // Safari in private browsing throws on `localStorage`, and a theme
    // preference is not worth a blank page.
    return null;
  }
}

export function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // Not fatal: the toggle still works for this page view.
  }
}

/** Light unless this browser has been told otherwise. */
export function initialTheme(): Theme {
  return storedTheme() ?? "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}
