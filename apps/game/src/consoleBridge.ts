/**
 * Console bridge — mirrors frontend log calls to the Rust side via the
 * `frontend_log` Tauri command so a portable user's logs are captured in
 * the same place as the backend's. In browser-only dev mode, falls back
 * to the standard browser console silently (no Tauri = no mirror).
 *
 * Pure-utility module: zero DOM, single optional dependency (`invoke`) which
 * we import directly. Extracted from main.ts as Phase 2.I.
 */

import { invoke } from "@tauri-apps/api/core";

export type ConsoleLogCategory = "gamestate" | "play" | "debugging" | "ingest";
export type ConsoleLogLevel = "log" | "warn" | "error";

export type ConsoleBridgeDeps = {
  /** Whether the Tauri runtime is present. When false, bridgeConsoleLog is a no-op. */
  haveTauri: () => boolean;
};

function serializeConsoleDetails(details: unknown): string | undefined {
  if (typeof details === "undefined") return undefined;
  if (typeof details === "string") return details;
  try {
    return JSON.stringify(details);
  } catch {
    return String(details);
  }
}

export type ConsoleBridge = {
  log: (category: ConsoleLogCategory, message: string, details?: unknown) => void;
  warn: (category: ConsoleLogCategory, message: string, details?: unknown) => void;
  error: (category: ConsoleLogCategory, message: string, details?: unknown) => void;
};

export function createConsoleBridge(deps: ConsoleBridgeDeps): ConsoleBridge {
  function bridge(level: ConsoleLogLevel, category: ConsoleLogCategory, message: string, details?: unknown): void {
    if (!deps.haveTauri()) return;
    const detailsText = serializeConsoleDetails(details);
    void invoke("frontend_log", {
      level,
      category,
      message,
      details: detailsText ?? null,
    }).catch(() => {
      // avoid recursive logging loops on transport failures
    });
  }

  function emit(level: ConsoleLogLevel, category: ConsoleLogCategory, message: string, details?: unknown): void {
    const tag = `[${category}] ${message}`;
    const fn: (...args: unknown[]) => void =
      level === "warn" ? console.warn : level === "error" ? console.error : console.log;
    if (typeof details === "undefined") fn(tag);
    else fn(tag, details);
    bridge(level, category, message, details);
  }

  return {
    log: (category, message, details) => emit("log", category, message, details),
    warn: (category, message, details) => emit("warn", category, message, details),
    error: (category, message, details) => emit("error", category, message, details),
  };
}
