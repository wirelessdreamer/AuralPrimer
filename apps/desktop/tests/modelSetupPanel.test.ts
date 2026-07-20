import { describe, it, expect, vi } from "vitest";
import {
  modelSetupStatus,
  modelSetupRowHtml,
  modelSetupHtml,
  canRunSetupDialog,
  setupDialogHtml,
  downloadResultMessage,
  initModelSetupPanel,
  type ModelSetupEntry,
} from "../src/modelSetupPanel";

function entry(over: Partial<ModelSetupEntry> = {}): ModelSetupEntry {
  return {
    id: "muscriptor",
    name: "MuScriptor",
    maker: "Kyutai × Mirelo",
    summary: "Whole-mix multi-instrument transcription.",
    license: "MIT / CC-BY-NC-4.0 (gated)",
    install_hint: "pip install muscriptor",
    license_accept_url: "https://huggingface.co/MuScriptor/muscriptor-large",
    docs_url: "https://github.com/muscriptor/muscriptor",
    requires_license_acceptance: true,
    package_installed: false,
    next_step: "install_package",
    ...over,
  };
}

describe("modelSetupStatus", () => {
  it("maps next_step to a label + tone", () => {
    expect(modelSetupStatus(entry({ next_step: "install_package" })).ok).toBe(false);
    expect(modelSetupStatus(entry({ next_step: "accept_license" })).ok).toBe(false);
    expect(modelSetupStatus(entry({ next_step: "ready" }))).toEqual({ label: "Ready", ok: true });
  });

  it("distinguishes missing weights from unverified license acceptance", () => {
    expect(
      modelSetupStatus(entry({ next_step: "accept_license", weights_present: false })).label,
    ).toContain("Weights");
    expect(
      modelSetupStatus(entry({ next_step: "accept_license", weights_present: null })).label,
    ).toContain("License");
  });
});

describe("canRunSetupDialog", () => {
  it("is true only when the engine ships but its weights are missing", () => {
    expect(canRunSetupDialog(entry({ package_installed: true, weights_present: false }))).toBe(true);
    expect(canRunSetupDialog(entry({ package_installed: true, weights_present: true }))).toBe(false);
    expect(canRunSetupDialog(entry({ package_installed: false, weights_present: false }))).toBe(
      false,
    );
  });
});

describe("setupDialogHtml", () => {
  it("walks accept-license -> check-access -> download and wires the accept URL", () => {
    const html = setupDialogHtml(entry({ package_installed: true, weights_present: false }));
    expect(html).toContain('data-ms-open="https://huggingface.co/MuScriptor/muscriptor-large"');
    expect(html).toContain('data-ms-check="muscriptor"');
    expect(html).toContain('data-ms-download="muscriptor"');
  });

  it("states the download size and keeps the token collapsed as a fallback", () => {
    const html = setupDialogHtml(entry({ package_installed: true, weights_present: false }));
    expect(html).toContain("5.5 GB");
    // The token input exists but is tucked inside a closed <details>, so the
    // happy path (already signed in) never asks the user to paste anything.
    expect(html).toContain("data-ms-token-row");
    expect(html).toMatch(/<details[^>]*data-ms-token-row(?![^>]*\bopen\b)/);
  });
});

describe("downloadResultMessage", () => {
  it("tells a signed-in user to accept the license, without asking for a token", () => {
    const msg = downloadResultMessage({
      ok: false,
      needs_license_acceptance: true,
      authenticated: true,
    });
    expect(msg.ok).toBe(false);
    expect(msg.text).toContain("accept the");
    expect(msg.needsToken).toBeFalsy();
    expect(msg.text).not.toContain("token");
  });

  it("only offers the token fallback when there is no credential at all", () => {
    const msg = downloadResultMessage({
      ok: false,
      needs_license_acceptance: true,
      authenticated: false,
    });
    expect(msg.needsToken).toBe(true);
    expect(msg.text).toContain("huggingface-cli login");
  });

  it("confirms a successful access check without claiming the weights are downloaded", () => {
    const msg = downloadResultMessage({ ok: true, check_only: true, authenticated: true });
    expect(msg.ok).toBe(true);
    expect(msg.text).toContain("no token needed");
    expect(msg.text).not.toContain("downloaded");
  });

  it("surfaces the raw error otherwise, and confirms success", () => {
    expect(downloadResultMessage({ ok: false, error: "connection reset" }).text).toBe(
      "connection reset",
    );
    expect(downloadResultMessage({ ok: true }).ok).toBe(true);
  });
});

describe("modelSetupRowHtml", () => {
  it("shows a copy-install button when the package is not installed", () => {
    const html = modelSetupRowHtml(entry({ package_installed: false }));
    expect(html).toContain('data-ms-copy="pip install muscriptor"');
  });

  it("offers the guided Set up dialog when only the gated weights are missing", () => {
    const html = modelSetupRowHtml(
      entry({ package_installed: true, weights_present: false, next_step: "accept_license" }),
    );
    expect(html).toContain('data-ms-setup="muscriptor"');
    expect(html).not.toContain("data-ms-copy"); // never tell a frozen sidecar to pip install
  });

  it("shows an Accept-license button linking the accept URL when gated", () => {
    const html = modelSetupRowHtml(
      entry({ package_installed: true, next_step: "accept_license" }),
    );
    expect(html).toContain(
      'data-ms-open="https://huggingface.co/MuScriptor/muscriptor-large"',
    );
    expect(html).toContain(">Accept license<");
    expect(html).not.toContain("data-ms-copy"); // installed -> no install button
  });

  it("omits the Accept button when acceptance is not required", () => {
    const html = modelSetupRowHtml(
      entry({ requires_license_acceptance: false, license_accept_url: null }),
    );
    expect(html).not.toContain(">Accept license<");
  });

  it("escapes untrusted fields", () => {
    const html = modelSetupRowHtml(entry({ name: "<script>x</script>" }));
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>x");
  });
});

describe("modelSetupHtml", () => {
  it("renders an empty-state message with no entries", () => {
    expect(modelSetupHtml([])).toContain("No optional external models");
  });
});

describe("initModelSetupPanel", () => {
  // Minimal fake element: records innerHTML + captures the click handler.
  function fakeContainer() {
    return {
      innerHTML: "",
      handler: null as ((ev: unknown) => void) | null,
      addEventListener(_type: string, h: (ev: unknown) => void) {
        this.handler = h;
      },
    };
  }
  function clickEvent(attrs: Record<string, string>) {
    const btn = { getAttribute: (k: string) => attrs[k] ?? null, closest: () => btn };
    return { target: { closest: () => btn } };
  }

  it("fetches and renders entries", async () => {
    const container = fakeContainer();
    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => [entry()],
      openUrl: async () => {},
      copyText: async () => {},
      storage: null,
    });
    expect(container.innerHTML).toContain("MuScriptor");
  });

  it("opens the URL on an Accept/Docs click and re-fetches on Re-check", async () => {
    const container = fakeContainer();
    const openUrl = vi.fn(async () => {});
    const fetchEntries = vi.fn(async () => [entry()]);
    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries,
      openUrl,
      copyText: async () => {},
      storage: null,
    });
    expect(fetchEntries).toHaveBeenCalledTimes(1);

    container.handler?.(clickEvent({ "data-ms-open": "https://example.com/accept" }));
    expect(openUrl).toHaveBeenCalledWith("https://example.com/accept");

    container.handler?.(clickEvent({ "data-ms-recheck": "1" }));
    await Promise.resolve();
    expect(fetchEntries).toHaveBeenCalledTimes(2);
  });

  it("copies the install hint on a copy click", async () => {
    const container = fakeContainer();
    const copyText = vi.fn(async () => {});
    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => [entry()],
      openUrl: async () => {},
      copyText,
    });
    container.handler?.(clickEvent({ "data-ms-copy": "pip install muscriptor" }));
    expect(copyText).toHaveBeenCalledWith("pip install muscriptor");
  });

  it("paints the cached snapshot before the (slow) sidecar answers", async () => {
    // A cold sidecar start costs ~40s; the panel must not sit blank until then.
    const container = fakeContainer();
    const store = new Map<string, string>([
      ["auralstudio.modelSetup.snapshot.v1", JSON.stringify([entry({ name: "CachedEngine" })])],
    ]);
    const storage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    } as unknown as Storage;

    let release: (() => void) | null = null;
    const pending = new Promise<void>((resolve) => (release = resolve));
    const init = initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => {
        await pending;
        return [entry({ name: "FreshEngine" })];
      },
      openUrl: async () => {},
      copyText: async () => {},
      storage,
    });

    await Promise.resolve();
    expect(container.innerHTML).toContain("CachedEngine");
    expect(container.innerHTML).not.toContain("Checking model setup");

    release?.();
    await init;
    expect(container.innerHTML).toContain("FreshEngine");
    expect(store.get("auralstudio.modelSetup.snapshot.v1")).toContain("FreshEngine");
  });

  it("does not leave a permanent 're-checking' note after a failed refresh", async () => {
    const container = fakeContainer();
    const store = new Map<string, string>([
      ["auralstudio.modelSetup.snapshot.v1", JSON.stringify([entry({ name: "CachedEngine" })])],
    ]);
    const storage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    } as unknown as Storage;

    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => {
        throw new Error("sidecar unavailable");
      },
      openUrl: async () => {},
      copyText: async () => {},
      storage,
    });
    expect(container.innerHTML).not.toContain("re-checking");
    expect(container.innerHTML).toContain("could not reach the sidecar");
  });

  it("coalesces concurrent refreshes into one sidecar run", async () => {
    const container = fakeContainer();
    let release: (() => void) | null = null;
    const pending = new Promise<void>((resolve) => (release = resolve));
    const fetchEntries = vi.fn(async () => {
      await pending;
      return [entry()];
    });

    const init = initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries,
      openUrl: async () => {},
      copyText: async () => {},
      storage: null,
    });
    // Hammer Re-check while the first run is still outstanding.
    container.handler?.(clickEvent({ "data-ms-recheck": "1" }));
    container.handler?.(clickEvent({ "data-ms-recheck": "1" }));
    expect(fetchEntries).toHaveBeenCalledTimes(1);

    release?.();
    await init;
    expect(fetchEntries).toHaveBeenCalledTimes(1);
  });

  it("keeps showing the cached rows if the refresh fails", async () => {
    const container = fakeContainer();
    const store = new Map<string, string>([
      ["auralstudio.modelSetup.snapshot.v1", JSON.stringify([entry({ name: "CachedEngine" })])],
    ]);
    const storage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    } as unknown as Storage;

    await initModelSetupPanel(container as unknown as HTMLElement, {
      fetchEntries: async () => {
        throw new Error("sidecar unavailable");
      },
      openUrl: async () => {},
      copyText: async () => {},
      storage,
    });
    expect(container.innerHTML).toContain("CachedEngine");
  });
});
