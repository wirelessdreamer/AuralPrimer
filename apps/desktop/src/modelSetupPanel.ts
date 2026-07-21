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

/** Result of the sidecar's `muscriptor-download` (with or without --check-only). */
export type WeightsDownloadResult = {
  ok: boolean;
  error?: string;
  needs_license_acceptance?: boolean;
  /** Only present on a --check-only run. */
  check_only?: boolean;
  authenticated?: boolean;
  access_granted?: boolean;
  repo?: string;
  size?: string;
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

/** `staleness`: "live" = just fetched, "checking" = cached while a refresh runs,
 * "stale" = cached and the refresh failed. The last two must be distinguishable:
 * a permanent "re-checking…" on a panel that has given up is a lie. */
export function modelSetupHtml(
  entries: ModelSetupEntry[],
  staleness: "live" | "checking" | "stale" = "live",
): string {
  if (!entries.length) {
    return `<div class="meta">No optional external models to set up.</div>`;
  }
  const note =
    staleness === "checking"
      ? `<div class="meta">Last known status — re-checking…</div>`
      : staleness === "stale"
        ? `<div class="meta">Last known status — could not reach the sidecar. Try Re-check.</div>`
        : "";
  return note + entries.map(modelSetupRowHtml).join("\n");
}

/**
 * The setup dialog's body: the ordered steps to get gated weights onto disk.
 * Rendered into a `<dialog>` so it reads as a guided flow rather than a row of
 * buttons the user has to sequence themselves.
 */
export function setupDialogHtml(entry: ModelSetupEntry, downloadSize = "5.5 GB"): string {
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
          <strong>Check access</strong>
          <div class="meta">Confirms the license is granted and you're signed in.
          If you've logged in with the Hugging Face CLI, that's all you need.</div>
          <button type="button" class="msBtn" data-ms-check="${esc(entry.id)}">Check access</button>
        </li>
        <li>
          <strong>Download the weights</strong>
          <div class="meta">About ${esc(downloadSize)} — runs in the background and
          can take a while.</div>
          <button type="button" class="msBtn msBtn--primary" data-ms-download="${esc(entry.id)}">Download weights</button>
        </li>
      </ol>

      <details class="msTokenFallback" data-ms-token-row>
        <summary class="meta">Not signed in? Use an access token instead</summary>
        <div class="meta">A read token from your account's access-token settings.
        It is passed to the downloader for this run only and is never stored.</div>
        <input type="password" class="msToken" data-ms-token placeholder="hf_…" autocomplete="off" />
      </details>

      <div class="msDialogStatus" data-ms-status role="status"></div>
      <div class="msDialogActions">
        <button type="submit" class="msBtn">Close</button>
      </div>
    </form>`;
}

/** A streamed line from the sidecar's download (`model_download_progress`). */
export type DownloadProgressEvent = {
  event?: string;
  downloaded_bytes?: number;
  total_bytes?: number | null;
  pct?: number;
  file?: string;
};

function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`;
  return `${Math.round(bytes / 1e3)} kB`;
}

/**
 * Progress line for the dialog. Returns null for events that carry nothing
 * worth showing, so the caller can leave the previous message in place rather
 * than blanking it.
 */
export function downloadProgressMessage(ev: DownloadProgressEvent): string | null {
  if (ev.event === "start") {
    return ev.total_bytes
      ? `Starting download — ${formatBytes(ev.total_bytes)} to fetch.`
      : "Starting download…";
  }
  if (typeof ev.downloaded_bytes !== "number") return null;
  const done = formatBytes(ev.downloaded_bytes);
  if (ev.total_bytes && typeof ev.pct === "number") {
    return `Downloading — ${done} of ${formatBytes(ev.total_bytes)} (${ev.pct.toFixed(1)}%)`;
  }
  return `Downloading — ${done}`;
}

/** Message to show for a download or access-check outcome. */
export function downloadResultMessage(result: WeightsDownloadResult): {
  text: string;
  ok: boolean;
  /** Reveal the token fallback: the user is not authenticated. */
  needsToken?: boolean;
} {
  if (result.ok) {
    return result.check_only
      ? {
          text: "Access granted and you're signed in — no token needed. Go ahead and download.",
          ok: true,
        }
      : { text: "Weights downloaded. This engine is ready to use.", ok: true };
  }
  if (result.needs_license_acceptance) {
    // Distinguish "no credential at all" from "signed in but not granted":
    // telling a signed-in user to paste a token sends them the wrong way.
    return result.authenticated === false
      ? {
          text:
            "You're not signed in to Hugging Face. Either run `huggingface-cli login`, " +
            "or paste an access token below and try again.",
          ok: false,
          needsToken: true,
        }
      : {
          text:
            "Access hasn't been granted yet. Open the model page (step 1), accept the " +
            "license with the account you're signed in as, then check again.",
          ok: false,
        };
  }
  return { text: result.error?.trim() || "Request failed.", ok: false };
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

export async function downloadModelWeights(
  token: string,
  checkOnly = false,
): Promise<WeightsDownloadResult> {
  const invoke = await getInvoke();
  const res = await invoke<{ ok?: boolean; payload?: WeightsDownloadResult; stderr?: string }>(
    "ingest_muscriptor_download",
    { hfToken: token || null, checkOnly },
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

/** Subscribe to streamed download progress; returns an unsubscribe fn. */
export async function listenDownloadProgress(
  onEvent: (ev: DownloadProgressEvent) => void,
): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  const unlisten = await listen<DownloadProgressEvent>("model_download_progress", (e) =>
    onEvent(e.payload),
  );
  return unlisten;
}

export type ModelSetupPanelDeps = {
  fetchEntries?: () => Promise<ModelSetupEntry[]>;
  openUrl?: (url: string) => Promise<void>;
  copyText?: (text: string) => Promise<void>;
  downloadWeights?: (token: string, checkOnly?: boolean) => Promise<WeightsDownloadResult>;
  onProgress?: (cb: (ev: DownloadProgressEvent) => void) => Promise<() => void>;
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
  const onProgress = deps.onProgress ?? listenDownloadProgress;
  const storage =
    deps.storage !== undefined
      ? deps.storage
      : typeof localStorage === "undefined"
        ? null
        : localStorage;

  let entries: ModelSetupEntry[] = [];
  let inFlight: Promise<void> | null = null;

  function paint(staleness: "live" | "checking" | "stale"): void {
    container.innerHTML = modelSetupHtml(entries, staleness);
  }

  function refresh(): Promise<void> {
    // One sidecar run at a time: each costs a ~40s cold start, and two in
    // flight can land out of order and paint the older answer last.
    if (inFlight) return inFlight;
    inFlight = (async () => {
      // Paint whatever we already know first; the cold start is slow enough
      // that a bare spinner reads as a hang.
      const cached = entries.length ? entries : readCache(storage);
      if (cached?.length) {
        entries = cached;
        paint("checking");
      } else {
        container.innerHTML = `<div class="meta">Checking model setup…</div>`;
      }
      try {
        entries = await fetchEntries();
        writeCache(storage, entries);
        paint("live");
      } catch (e) {
        if (entries.length) paint("stale");
        else container.innerHTML = `<pre class="error">${esc(String(e))}</pre>`;
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  }

  function openSetupDialog(entry: ModelSetupEntry): void {
    const dialog = document.createElement("dialog");
    dialog.className = "msDialog";
    dialog.innerHTML = setupDialogHtml(entry);
    document.body.appendChild(dialog);

    const statusEl = dialog.querySelector<HTMLElement>("[data-ms-status]");
    const tokenEl = dialog.querySelector<HTMLInputElement>("[data-ms-token]");

    const tokenRow = dialog.querySelector<HTMLDetailsElement>("[data-ms-token-row]");

    dialog.addEventListener("click", (ev) => {
      const btn = (ev.target as HTMLElement)?.closest("button");
      if (!btn) return;
      const open = btn.getAttribute("data-ms-open");
      if (open) {
        void openUrl(open);
        return;
      }
      const isCheck = Boolean(btn.getAttribute("data-ms-check"));
      if (!isCheck && !btn.getAttribute("data-ms-download")) return;

      const token = tokenEl?.value.trim() ?? "";
      btn.setAttribute("disabled", "true");
      if (statusEl) {
        statusEl.className = "msDialogStatus";
        statusEl.textContent = isCheck ? "Checking access…" : "Contacting Hugging Face…";
      }

      // Live byte counts for the duration of the transfer; a static "this can
      // take a while" on a multi-GB download reads as a hang.
      let stopProgress: (() => void) | null = null;
      if (!isCheck) {
        void onProgress((ev) => {
          const text = downloadProgressMessage(ev);
          if (text && statusEl) {
            statusEl.className = "msDialogStatus";
            statusEl.textContent = text;
          }
        })
          .then((un) => {
            stopProgress = un;
          })
          .catch(() => {
            /* progress is a nicety; the download still completes without it */
          });
      }

      void downloadWeights(token, isCheck)
        .then((result) => {
          const message = downloadResultMessage(result);
          if (statusEl) {
            statusEl.className = message.ok ? "msDialogStatus ok" : "msDialogStatus error";
            statusEl.textContent = message.text;
          }
          // Only surface the token field when it's actually the way forward.
          if (message.needsToken && tokenRow) tokenRow.open = true;
          if (result.ok && !result.check_only && tokenEl) tokenEl.value = "";
          return isCheck ? undefined : refresh();
        })
        .catch((e) => {
          if (statusEl) {
            statusEl.className = "msDialogStatus error";
            statusEl.textContent = String(e);
          }
        })
        .finally(() => {
          stopProgress?.();
          btn.removeAttribute("disabled");
        });
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
