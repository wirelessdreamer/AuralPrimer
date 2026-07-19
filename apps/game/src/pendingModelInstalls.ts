/**
 * Launch-time "pending model installs" notice for AuralPrimer.
 *
 * Source of truth is the packaged sidecar's `runtime-check`, NOT a static
 * preferred-pack list: the sidecar knows every real resolution path (a demucs
 * modelpack shipped as `modelpacks/*.zip`, Basic Pitch bundled inside the
 * sidecar, per-engine MT3 checkpoints under assets/models, ...) and marks each
 * asset/dependency `required` or optional. We raise the banner only for things
 * that are BOTH required and not ok — so we never nag about an optional
 * backend (e.g. TensorFlow) nor about something that IS present, just resolved
 * from a different location than a naive pack-list check would look in.
 *
 * Detection (`computeMissingRequirements`) is pure and unit-tested; the banner
 * controller takes injectable deps so it is testable without a DOM or Tauri.
 */

type RuntimeAsset = {
  ok?: boolean;
  required?: boolean;
  error?: string;
  kind?: string;
  path?: string;
};

type RuntimeDependency = {
  ok?: boolean;
  required?: boolean;
  role?: string;
  missing_behavior?: string;
  error?: string;
};

export type RuntimeCheckPayload = {
  ok?: boolean;
  assets?: Record<string, unknown>;
  dependencies?: Record<string, RuntimeDependency>;
};

export type RuntimeCheckResult = { ok?: boolean; payload?: RuntimeCheckPayload | null };

export type MissingRequirement = { id: string; label: string; detail: string };

function isAssetLike(v: unknown): v is RuntimeAsset {
  return !!v && typeof v === "object" && ("ok" in (v as object) || "required" in (v as object));
}

/** "basic_pitch_model" -> "Basic pitch model" */
function humanize(name: string): string {
  const s = name.replace(/[._]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Required-but-unavailable assets and dependencies from a runtime-check
 * payload. Asset groups nest (e.g. `mt3_checkpoints.<engine>`), so walk them.
 * Anything optional (`required !== true`) is deliberately ignored.
 */
export function computeMissingRequirements(
  payload: RuntimeCheckPayload | null | undefined,
): MissingRequirement[] {
  const missing: MissingRequirement[] = [];

  const walk = (obj: Record<string, unknown> | undefined, prefix: string): void => {
    for (const [name, val] of Object.entries(obj ?? {})) {
      if (!val || typeof val !== "object") continue;
      const id = prefix ? `${prefix}.${name}` : name;
      if (isAssetLike(val)) {
        const asset = val as RuntimeAsset;
        if (asset.required === true && asset.ok !== true) {
          missing.push({ id, label: humanize(name), detail: asset.error ?? "not found" });
        }
      } else {
        walk(val as Record<string, unknown>, id);
      }
    }
  };
  walk(payload?.assets, "");

  for (const [name, dep] of Object.entries(payload?.dependencies ?? {})) {
    if (dep?.required === true && dep.ok !== true) {
      missing.push({
        id: name,
        label: humanize(name),
        detail: dep.missing_behavior ?? dep.error ?? "not installed",
      });
    }
  }

  return missing;
}

const DISMISS_KEY = "auralprimer.pendingModelInstalls.dismissed";

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

export type PendingModelInstallDeps = {
  /** Banner mount point (#modelInstallBanner). */
  container: HTMLElement;
  /** Reveal the Models install UI. */
  onOpenModels: () => void;
  fetchRuntimeCheck?: () => Promise<RuntimeCheckResult>;
  /** Session-scoped by default so the nudge returns next launch. */
  storage?: Pick<Storage, "getItem" | "setItem">;
  escapeHtml?: (s: string) => string;
};

async function defaultFetchRuntimeCheck(): Promise<RuntimeCheckResult> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeCheckResult>("ingest_runtime_check");
}

function hide(container: HTMLElement): void {
  container.hidden = true;
  container.innerHTML = "";
}

/**
 * Check the sidecar runtime for missing REQUIRED models/dependencies and, if
 * any (and not dismissed for that exact set), render the banner. Returns what
 * it found, for callers/tests.
 */
export async function initPendingModelInstallBanner(
  deps: PendingModelInstallDeps,
): Promise<MissingRequirement[]> {
  const fetchRuntimeCheck = deps.fetchRuntimeCheck ?? defaultFetchRuntimeCheck;
  const escapeHtml = deps.escapeHtml ?? esc;
  const storage =
    deps.storage ?? (typeof sessionStorage !== "undefined" ? sessionStorage : undefined);

  let result: RuntimeCheckResult;
  try {
    result = await fetchRuntimeCheck();
  } catch {
    hide(deps.container); // runtime unknown -> stay quiet rather than cry wolf
    return [];
  }

  const missing = computeMissingRequirements(result?.payload);
  if (!missing.length) {
    hide(deps.container);
    return [];
  }

  const key = missing
    .map((m) => m.id)
    .sort()
    .join(",");
  if (storage?.getItem(DISMISS_KEY) === key) {
    hide(deps.container);
    return missing;
  }

  const names = missing.map((m) => escapeHtml(m.label)).join(", ");
  const plural = missing.length === 1 ? "" : "s";
  deps.container.hidden = false;
  deps.container.innerHTML = `
    <div class="modelInstallBannerInner">
      <span class="modelInstallBannerIcon" aria-hidden="true">⚠</span>
      <span class="grow"><strong>${missing.length} required model${plural} missing</strong>
        — ${names}. Install ${missing.length === 1 ? "it" : "them"} to enable full transcription / import.</span>
      <button type="button" class="modelInstallSetup">Set up models</button>
      <button type="button" class="modelInstallDismiss" aria-label="Dismiss">✕</button>
    </div>`;

  deps.container
    .querySelector(".modelInstallSetup")
    ?.addEventListener("click", () => deps.onOpenModels());
  deps.container.querySelector(".modelInstallDismiss")?.addEventListener("click", () => {
    storage?.setItem(DISMISS_KEY, key);
    hide(deps.container);
  });

  return missing;
}
