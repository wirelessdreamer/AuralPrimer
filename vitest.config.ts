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
    ],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary", "lcov"],
      include: [
        "packages/songpack/src/**/*.ts",
        "packages/core-music/src/**/*.ts",
        "apps/desktop/src/audioBackend.ts",
        "apps/desktop/src/transportController.ts",
        "apps/desktop/src/metronome.ts",
        "apps/desktop/src/lyricsGenerator.ts",
        "apps/desktop/src/hud.ts",
        "apps/desktop/src/plugins.ts",
        "apps/desktop/src/pluginsUi.ts",
        "apps/desktop/src/models/modelManager.ts"
      ],
      exclude: [
        "**/*.d.ts",
        "**/index.ts"
      ],
      thresholds: {
        lines: 85,
        functions: 85,
        statements: 85,
        branches: 75
      }
    }
  }
});
