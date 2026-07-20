/**
 * "Model setup" panel — renders the sidecar's external-model setup descriptors
 * (`ingest_model_setup` → `model-setup`) and walks the user through whatever an
 * optional, license-gated engine still needs.
 *
 * Two things this panel has to get right:
 *
 * 1. **It must render instantly.** A cold sidecar start costs ~40 s, so the
 *    last-known snapshot is cached and painted immediately, then refreshed in
 *    the background and swapped in place. Without that the panel just sits on
 *    "Checking…" long enough that users conclude it is broken.
 * 2. **It must not tell users to `pip install`.** The sidecar is a frozen
 *    binary — a pip install into some other interpreter is invisible to it.
 *    Engines whose code we're licensed to ship are bundled; what's left is the
 *    gated *weights*, which is what the setup dialog handles: accept the
 *    license, supply a token, download, verify.
 *
 * The pure render/status helpers are exported and Tauri-free so they can be
 * unit-tested; the I/O (sidecar invoke, URL open) is injectable into
 * {@link initModelSetupPanel}.
 */

export type ModelSetupEntry = {
  id: string;
  name: string;
  maker: string;
  summary: string;
  license: string;
  install_hint: string;
  license_accept_url: string | null;
  docs_url: string | null;
  requires_license_acceptance: boolean;
  package_installed: boolean;
  /** null when the engine exposes no weights probe. */
  weights_present?: boolean | null;
  next_step: "install_package" | "accept_license" | "ready";
};

type ModelSetupSnapshot = { external_models: ModelSetupEntry[] };
type SidecarCapture = { ok?: boolean; payload?: ModelSetupSnapshot };

/** Result of the sidecar's `muscriptor-download`. */
export type WeightsDownloadResult = {
  ok: boolean;
  error?: string;
  needs_license_acceptance?: boolean;
};

const CACHE_KEY = "auralstudio.modelSetup.snapshot.v1";

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

/** Human status for an entry, plus the badge tone (ready = ok). */
export function modelSetupStatus(entry: ModelSetupEntry): { label: string; ok: boolean } {
  switch (entry.next_step) {
    case "install_package":
      return { label: "Engine not available", ok: false };
    case "accept_license":
      return entry.weights_present === false
        ? { label: "Weights not downloaded", ok: false }
        : { label: "License acceptance needed", ok: false };
    case "ready":
      return { label: "Ready", ok: true };
    default:
      return { label: entry.next_step, ok: false };
  }
}

/**
 * Whether this entry's remaining work is "download the gated weights" — i.e.
 * the engine ships with the app and the setup dialog can finish the job.
 */
export function canRunSetupDialog(entry: ModelSetupEntry): boolean {
  return entry.package_installed && entry.weights_present === false;
}

/** Render one model row. Buttons carry data-* the init handler dispatches on. */
export function modelSetupRowHtml(entry: ModelSetupEntry): string {
  const status = modelSetupStatus(entry);
  const badgeCls = status.ok
    ? "importAuditStatus importAuditStatus--found"
    : "importAuditStatus importAuditStatus--missing";
  const setupBtn = canRunSetupDialog(entry)
    ? `<button type="button" class="msBtn msBtn--primary" data-ms-setup="${esc(entry.id)}">Set up…</button>`
    : "";
  const acceptBtn =
    !setupBtn && entry.requires_license_acceptance && entry.license_accept_url
      ? `<button type="button" class="msBtn" data-ms-open="${esc(entry.license_accept_url)}">Accept license</button>`
      : "";
  const docsBtn = entry.docs_url
    ? `<button type="button" class="msBtn" data-ms-open="${esc(entry.docs_url)}">Docs</button>`
    : "";
  const installBtn = !entry.package_installed
    ? `<button type="button" class="msBtn" data-ms-copy="${esc(entry.install_hint)}" title="Copy">${esc(entry.install_hint)}</button>`
    : "";
  return `
    <div class="msRow" data-ms-id="${esc(entry.id)}">
      <div class="msHead">
        <span class="msName">${esc(entry.name)}</span>
        <span class="${badgeCls}">${esc(status.label)}</span>
      </div>
      <div class="meta">${esc(entry.summary)}</div>
      <div class="meta">by ${esc(entry.maker)} · ${esc(entry.license)}</div>
      <div class="msActions">${setupBtn}${installBtn}${acceptBtn}${docsBtn}
        <button type="button" class="msBtn" data-ms-recheck="1">Re-check</button>
      </div>
    </div>`;
}

export function modelSetupHtml(entries: ModelSetupEntry[], stale = false): string {
  if (!entries.length) {
    return `<div class="meta">No optional external models to set up.</div>`;
  }
  const note = stale ? `<div class="meta">Last known status — re-checking…</div>` : "";
  return note + entries.map(modelSetupRowHtml).join("\n");
}

/**
 * The setup dialog's body: the ordered steps to get gated weights onto disk.
 * Rendered into a `<dialog>` so it reads as a guided flow rather than a row of
 * buttons the user has to sequence themselves.
 */
export function setupDialogHtml(entry: ModelSetupEntry): string {
  const acceptUrl = entry.license_accept_url ?? "";
  return `
    <form method="dialog" class="msDialogForm">
      <h3>Set up ${esc(entry.name)}</h3>
      <div class="meta">${esc(entry.license)}</div>
      <p class="meta">The engine ships with AuralStudio. Its weights are licensed
      separately and have to be downloaded once, after you accept the license.</p>

      <ol class="msSteps">
        <li>
          <strong>Accept the license</strong>
          <div class="meta">Opens the model page in your browser. Sign in and accept
          the terms — this can't be done for you.</div>
          <button type="button" class="msBtn" data-ms-open="${esc(acceptUrl)}">Open model page</button>
        </li>
        <li>
          <strong>Paste an access token</strong>
          <div class="meta">A read token from your account's access-token settings.
          It is passed to the downloader for this run only and is not stored.</div>
          <input type="password" class="msToken" data-ms-token placeholder="hf_…" autocomplete="off" />
        </li>
        <li>
          <strong>Download the weights</strong>
          <div class="meta">Several hundred MB — this runs in the background and can
          take a few minutes.</div>
          <button type="button" class="msBtn msBtn--primary" data-ms-download="${esc(entry.id)}">Download weights</button>
        </li>
      </ol>

      <div class="msDialogStatus" data-ms-status role="status"></div>
      <div class="msDialogActions">
        <button type="submit" class="msBtn">Close</button>
      </div>
    </form>`;
}

/** Message to show for a download outcome. */
export function downloadResultMessage(result: WeightsDownloadResult): {
  text: string;
  ok: boolean;
} {
  if (result.ok) {
    return { text: "Weights downloaded. This engine is ready to use.", ok: true };
  }
  if (result.needs_license_acceptance) {
    return {
      text:
        "The download was refused. Accept the license on the model page (step 1) " +
        "with the same account your token belongs to, then try again.",
      ok: false,
    };
  }
  return { text: result.error?.trim() || "Download failed.", ok: false };
}

// --- I/O (Tauri) --- //

async function getInvoke() {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke;
}

export async function fetchModelSetup(): Promise<ModelSetupEntry[]> {
  const invoke = await getInvoke();
  const res = await invoke<SidecarCapture>("ingest_model_setup");
  return res?.payload?.external_models ?? [];
}

export async function downloadModelWeights(token: string): Promise<WeightsDownloadResult> {
  const invoke = await getInvoke();
  const res = await invoke<{ ok?: boolean; payload?: WeightsDownloadResult; stderr?: string }>(
    "ingest_muscriptor_download",
    { hfToken: token || null },
  );
  return res?.payload ?? { ok: Boolean(res?.ok), error: res?.stderr };
}

export async function openExternalUrl(url: string): Promise<void> {
  const invoke = await getInvoke();
  await invoke("open_external_url", { url });
}

function readCache(store: Storage | null): ModelSetupEntry[] | null {
  try {
    const raw = store?.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ModelSetupEntry[];
    return Array.isArray(parsed) && parsed.length ? parsed : null;
  } catch {
    return null;
  }
}

function writeCache(store: Storage | null, entries: ModelSetupEntry[]): void {
  try {
    store?.setItem(CACHE_KEY, JSON.stringify(entries));
  } catch {
    /* quota / private mode — the panel just loses its warm start */
  }
}

export type ModelSetupPanelDeps = {
  fetchEntries?: () => Promise<ModelSetupEntry[]>;
  openUrl?: (url: string) => Promise<void>;
  copyText?: (text: string) => Promise<void>;
  downloadWeights?: (token: string) => Promise<WeightsDownloadResult>;
  storage?: Storage | null;
};

/** Fetch, render, and wire the panel's buttons. Deps are injectable for tests. */
export async function initModelSetupPanel(
  container: HTMLElement,
  deps: ModelSetupPanelDeps = {},
): Promise<void> {
  const fetchEntries = deps.fetchEntries ?? fetchModelSetup;
  const openUrl = deps.openUrl ?? openExternalUrl;
  const copyText = deps.copyText ?? ((t: string) => navigator.clipboard.writeText(t));
  const downloadWeights = deps.downloadWeights ?? downloadModelWeights;
  const storage =
    deps.storage !== undefined
      ? deps.storage
      : typeof localStorage === "undefined"
        ? null
        : localStorage;

  let entries: ModelSetupEntry[] = [];

  function paint(stale: boolean): void {
    container.innerHTML = modelSetupHtml(entries, stale);
  }

  async function refresh(): Promise<void> {
    // Paint whatever we already know first; the sidecar's cold start is slow
    // enough that a bare spinner reads as a hang.
    const cached = entries.length ? entries : readCache(storage);
    if (cached?.length) {
      entries = cached;
      paint(true);
    } else {
      container.innerHTML = `<div class="meta">Checking model setup…</div>`;
    }
    try {
      entries = await fetchEntries();
      writeCache(storage, entries);
      paint(false);
    } catch (e) {
      if (!entries.length) container.innerHTML = `<pre class="error">${esc(String(e))}</pre>`;
    }
  }

  function openSetupDialog(entry: ModelSetupEntry): void {
    const dialog = document.createElement("dialog");
    dialog.className = "msDialog";
    dialog.innerHTML = setupDialogHtml(entry);
    document.body.appendChild(dialog);

    const statusEl = dialog.querySelector<HTMLElement>("[data-ms-status]");
    const tokenEl = dialog.querySelector<HTMLInputElement>("[data-ms-token]");

    dialog.addEventListener("click", (ev) => {
      const btn = (ev.target as HTMLElement)?.closest("button");
      if (!btn) return;
      const open = btn.getAttribute("data-ms-open");
      if (open) {
        void openUrl(open);
        return;
      }
      if (!btn.getAttribute("data-ms-download")) return;
      const token = tokenEl?.value.trim() ?? "";
      btn.setAttribute("disabled", "true");
      if (statusEl) {
        statusEl.className = "msDialogStatus";
        statusEl.textContent = "Downloading weights… this can take a few minutes.";
      }
      void downloadWeights(token)
        .then((result) => {
          const message = downloadResultMessage(result);
          if (statusEl) {
            statusEl.className = message.ok ? "msDialogStatus ok" : "msDialogStatus error";
            statusEl.textContent = message.text;
          }
          if (result.ok && tokenEl) tokenEl.value = "";
          return refresh();
        })
        .catch((e) => {
          if (statusEl) {
            statusEl.className = "msDialogStatus error";
            statusEl.textContent = String(e);
          }
        })
        .finally(() => btn.removeAttribute("disabled"));
    });

    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  container.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement)?.closest("button");
    if (!btn) return;
    const open = btn.getAttribute("data-ms-open");
    const copy = btn.getAttribute("data-ms-copy");
    const setup = btn.getAttribute("data-ms-setup");
    if (open) void openUrl(open);
    else if (copy) void copyText(copy);
    else if (setup) {
      const entry = entries.find((e) => e.id === setup);
      if (entry) openSetupDialog(entry);
    } else if (btn.getAttribute("data-ms-recheck")) void refresh();
  });

  await refresh();
}
