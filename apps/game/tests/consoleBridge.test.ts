// @vitest-environment jsdom
/**
 * Execution tests for the console bridge. Exercises createConsoleBridge:
 * the browser-console mirroring (log/warn/error), the Tauri `frontend_log`
 * forwarding (and its no-op path when Tauri is absent), and the details
 * serialization branches (undefined / string / object / unserializable).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import { createConsoleBridge } from "../src/consoleBridge";

describe("consoleBridge", () => {
  let logSpy: ReturnType<typeof vi.spyOn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    invoke.mockReset();
    invoke.mockResolvedValue(undefined);
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    logSpy.mockRestore();
    warnSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it("log() prints tagged message to console.log and forwards to Tauri", () => {
    const bridge = createConsoleBridge({ haveTauri: () => true });
    bridge.log("gamestate", "hello");
    expect(logSpy).toHaveBeenCalledWith("[gamestate] hello");
    expect(invoke).toHaveBeenCalledWith("frontend_log", {
      level: "log",
      category: "gamestate",
      message: "hello",
      details: null,
    });
  });

  it("warn() routes to console.warn", () => {
    const bridge = createConsoleBridge({ haveTauri: () => true });
    bridge.warn("play", "careful");
    expect(warnSpy).toHaveBeenCalledWith("[play] careful");
    expect(logSpy).not.toHaveBeenCalled();
    expect(invoke).toHaveBeenCalledWith(
      "frontend_log",
      expect.objectContaining({ level: "warn", category: "play", message: "careful" }),
    );
  });

  it("error() routes to console.error", () => {
    const bridge = createConsoleBridge({ haveTauri: () => true });
    bridge.error("debugging", "boom");
    expect(errorSpy).toHaveBeenCalledWith("[debugging] boom");
    expect(invoke).toHaveBeenCalledWith(
      "frontend_log",
      expect.objectContaining({ level: "error" }),
    );
  });

  it("string details are passed straight through and also given to console", () => {
    const bridge = createConsoleBridge({ haveTauri: () => true });
    bridge.log("ingest", "msg", "the-detail");
    expect(logSpy).toHaveBeenCalledWith("[ingest] msg", "the-detail");
    expect(invoke).toHaveBeenCalledWith(
      "frontend_log",
      expect.objectContaining({ details: "the-detail" }),
    );
  });

  it("object details are JSON-serialized for the Tauri payload", () => {
    const bridge = createConsoleBridge({ haveTauri: () => true });
    const obj = { a: 1, b: [2, 3] };
    bridge.log("ingest", "msg", obj);
    expect(logSpy).toHaveBeenCalledWith("[ingest] msg", obj);
    expect(invoke).toHaveBeenCalledWith(
      "frontend_log",
      expect.objectContaining({ details: JSON.stringify(obj) }),
    );
  });

  it("unserializable (circular) details fall back to String()", () => {
    const bridge = createConsoleBridge({ haveTauri: () => true });
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    bridge.log("ingest", "msg", circular);
    const payload = invoke.mock.calls[0][1] as { details: string };
    expect(typeof payload.details).toBe("string");
    expect(payload.details).toBe(String(circular));
  });

  it("is a no-op on the Tauri side when haveTauri() is false (console still prints)", () => {
    const bridge = createConsoleBridge({ haveTauri: () => false });
    bridge.log("gamestate", "no-tauri");
    expect(logSpy).toHaveBeenCalledWith("[gamestate] no-tauri");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("swallows invoke rejections without throwing", async () => {
    invoke.mockRejectedValue(new Error("transport down"));
    const bridge = createConsoleBridge({ haveTauri: () => true });
    expect(() => bridge.error("debugging", "still-fine")).not.toThrow();
    // flush the rejected microtask; .catch() handler must absorb it
    await Promise.resolve();
    await Promise.resolve();
    expect(errorSpy).toHaveBeenCalledWith("[debugging] still-fine");
  });
});
