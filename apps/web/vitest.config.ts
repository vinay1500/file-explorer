import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: false,
    // `e2e/` is Playwright's. Vitest picking those files up produces a
    // confusing failure about a missing test runner rather than a missing
    // browser, which is the kind of error that wastes an afternoon.
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
