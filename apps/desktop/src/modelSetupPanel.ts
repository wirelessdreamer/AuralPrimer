/**
 * "Model setup" panel — renders the sidecar's external-model setup descriptors
 * (`ingest_model_setup` → `model-setup`) and directs the user to where each
 * optional, license-gated engine must be set up: install the package, then
 * accept its license at an external page (a gated HuggingFace model page, or
 * any other host). License acceptance can't be automated, so the panel opens
 * the accept URL in the user's browser and offers a re-check.
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
  next_step: "install_package" | "accept_license" | "ready";
};

type ModelSetupSnapshot = { external_models: ModelSetupEntry[] };
type SidecarCapture = { ok?: boolean; payload?: ModelSetupSnapshot };

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

/** Human status for an entry, plus the badge tone (found = neutral/ok). */
export function modelSetupStatus(entry: ModelSetupEntry): { label: string; ok: boolean } {
  switch (entry.next_step) {
    case "install_package":
      return { label: "Not installed", ok: false };
    case "accept_license":
      return { label: "Installed — license acceptance needed", ok: false };
    case "ready":
      return { label: "Ready", ok: true };
    default:
      return { label: entry.next_step, ok: false };
  }
}

/** Render one model row. Buttons carry data-* the init handler dispatches on. */
export function modelSetupRowHtml(entry: ModelSetupEntry): string {
  const status = modelSetupStatus(entry);
  const badgeCls = status.ok
    ? "importAuditStatus importAuditStatus--found"
    : "importAuditStatus importAuditStatus--missing";
  const acceptBtn =
    entry.requires_license_acceptance && entry.license_accept_url
      ? `<button type="button" class="msBtn" data-ms-open="${esc(entry.license_accept_url)}">Accept license</button>`
      : "";
  const docsBtn = entry.docs_url
    ? `<button type="button" class="msBtn" data-ms-open="${esc(entry.docs_url)}">Docs</button>`
    : "";
  const installBtn = !entry.package_installed
    ? `<button type="button" class="msBtn" data-ms-copy="${esc(entry.install_hint)}" title="Copy install command">Copy: ${esc(entry.install_hint)}</button>`
    : "";
  return `
    <div class="msRow" data-ms-id="${esc(entry.id)}">
      <div class="msHead">
        <span class="msName">${esc(entry.name)}</span>
        <span class="${badgeCls}">${esc(status.label)}</span>
      </div>
      <div class="meta">${esc(entry.summary)}</div>
      <div class="meta">by ${esc(entry.maker)} · ${esc(entry.license)}</div>
      <div class="msActions">${installBtn}${acceptBtn}${docsBtn}
        <button type="button" class="msBtn" data-ms-recheck="1">Re-check</button>
      </div>
    </div>`;
}

export function modelSetupHtml(entries: ModelSetupEntry[]): string {
  if (!entries.length) {
    return `<div class="meta">No optional external models to set up.</div>`;
  }
  return entries.map(modelSetupRowHtml).join("\n");
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

export async function openExternalUrl(url: string): Promise<void> {
  const invoke = await getInvoke();
  await invoke("open_external_url", { url });
}

export type ModelSetupPanelDeps = {
  fetchEntries?: () => Promise<ModelSetupEntry[]>;
  openUrl?: (url: string) => Promise<void>;
  copyText?: (text: string) => Promise<void>;
};

/** Fetch, render, and wire the panel's buttons. Deps are injectable for tests. */
export async function initModelSetupPanel(
  container: HTMLElement,
  deps: ModelSetupPanelDeps = {},
): Promise<void> {
  const fetchEntries = deps.fetchEntries ?? fetchModelSetup;
  const openUrl = deps.openUrl ?? openExternalUrl;
  const copyText = deps.copyText ?? ((t: string) => navigator.clipboard.writeText(t));

  async function refresh(): Promise<void> {
    container.innerHTML = `<div class="meta">Checking model setup…</div>`;
    try {
      container.innerHTML = modelSetupHtml(await fetchEntries());
    } catch (e) {
      container.innerHTML = `<pre class="error">${esc(String(e))}</pre>`;
    }
  }

  container.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement)?.closest("button");
    if (!btn) return;
    const open = btn.getAttribute("data-ms-open");
    const copy = btn.getAttribute("data-ms-copy");
    if (open) void openUrl(open);
    else if (copy) void copyText(copy);
    else if (btn.getAttribute("data-ms-recheck")) void refresh();
  });

  await refresh();
}
