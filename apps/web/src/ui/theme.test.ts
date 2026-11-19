import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, initialTheme, storeTheme, storedTheme } from "./theme";

/**
 * D-27, as a test rather than a sentence in a comment.
 *
 * The rule is that the page **opens light for everybody**. The version before
 * M8 set `color-scheme: light dark` and nothing else, which handed the demo's
 * appearance to the visitor's laptop: a dark-mode machine got a dark screen
 * and no control to change it, and the first thing an interviewer saw was
 * decided by their system settings rather than by this project.
 *
 * Nothing in the suite would have noticed that default changing back, which is
 * why this file exists.
 */

const KEY = "fe.theme";

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("initialTheme", () => {
  it("is light when this browser has no stored choice", () => {
    expect(initialTheme()).toBe("light");
  });

  it("is light even when the operating system asks for dark", () => {
    // The whole point of D-27: `prefers-color-scheme` is not consulted. If
    // this ever starts failing, the demo has gone back to opening in whatever
    // the visitor's machine happens to be set to.
    vi.stubGlobal("matchMedia", () => ({ matches: true, media: "(prefers-color-scheme: dark)" }));
    expect(initialTheme()).toBe("light");
  });

  it("honours a choice this browser has already made", () => {
    storeTheme("dark");
    expect(initialTheme()).toBe("dark");
  });

  it("ignores a stored value that is not a theme", () => {
    window.localStorage.setItem(KEY, "solarized");
    expect(storedTheme()).toBeNull();
    expect(initialTheme()).toBe("light");
  });
});

describe("storage that throws", () => {
  it("does not take the page down with it", () => {
    // Safari in private browsing throws on `localStorage`. A theme preference
    // is not worth a blank page.
    vi.stubGlobal("localStorage", {
      getItem() {
        throw new Error("denied");
      },
      setItem() {
        throw new Error("denied");
      },
    });
    expect(storedTheme()).toBeNull();
    expect(initialTheme()).toBe("light");
    expect(() => {
      storeTheme("dark");
    }).not.toThrow();
  });
});

describe("applyTheme", () => {
  it("stamps the root element, which is what the stylesheet keys off", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
