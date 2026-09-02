import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // JSX is transformed by esbuild's automatic runtime. @vitejs/plugin-react is
  // not used: it exists mainly for Fast Refresh, which tests do not need, and
  // its Babel tree conflicts with this project's other dependencies.
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: { "@": resolve(__dirname, ".") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
