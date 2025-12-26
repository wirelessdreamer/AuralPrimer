import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Use globals to avoid issues with imported describe/it not being wired to the runner
    // in some Windows environments after moving between filesystems.
    globals: true,
    include: [
      "packages/*/tests/**/*.test.ts",
      "packages/*/tests/**/*.spec.ts",
      "visualizers/*/tests/**/*.test.ts",
      "visualizers/*/tests/**/*.spec.ts",
      "apps/*/tests/**/*.test.ts",
      "apps/*/tests/**/*.spec.ts"
    ]
  }
});
