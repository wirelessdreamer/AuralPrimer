/**
 * Launch-time "pending model installs" notice for AuralPrimer.
 *
 * On boot we compare the installed model packs against the curated preferred
 * set; if any are missing we surface an attention-grabbing banner that leads
 * the user straight to the Models install UI. The banner is dismissible for
 * the session but re-appears on the next launch while packs are still missing,
 * so it nudges without permanently nagging.
 *
 * The detection (`computeMissingModelPacks`) is pure and unit-tested; the
 * banner controller takes injectable deps (list fn, storage, open callback) so
 * it too is testable without a DOM/Tauri.
 */
import { listInstalledModelPacks, type InstalledModelPack } from "./models/modelManager";
import { PREFERRED_MODEL_PACKS, type PreferredModelPack } from "./models/preferredModelPacks";

/** Preferred packs that are not present (ok) among the installed packs. */
export function computeMissingModelPacks(
  installed: InstalledModelPack[],
  preferred: PreferredModelPack[] = PREFERRED_MODEL_PACKS,
): PreferredModelPack[] {
  const have = new Set(installed.filter((p) => p.ok !== false).map((p) => p.id));
  return preferred.filter((p) => !have.has(p.id));
}

const DISMISS_KEY = "auralprimer.pendingModelInstalls.dismissed";

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

export type PendingModelInstallDeps = {
  /** Banner mount point (e.g. #modelInstallBanner). */
  container: HTMLElement;
  /** Reveal/scroll the Models install UI. */
  onOpenModels: () => void;
  listInstalled?: () => Promise<InstalledModelPack[]>;
  preferred?: PreferredModelPack[];
  /** Session-scoped by default so the nudge returns next launch. */
  storage?: Pick<Storage, "getItem" | "setItem">;
  escapeHtml?: (s: string) => string;
};

function hide(container: HTMLElement): void {
  container.hidden = true;
  container.innerHTML = "";
}

/**
 * Check for missing packs and, if any (and not dismissed for that exact set),
 * render the banner. Returns the missing packs it found (for callers/tests).
 */
export async function initPendingModelInstallBanner(
  deps: PendingModelInstallDeps,
): Promise<PreferredModelPack[]> {
  const list = deps.listInstalled ?? listInstalledModelPacks;
  const preferred = deps.preferred ?? PREFERRED_MODEL_PACKS;
  const escapeHtml = deps.escapeHtml ?? esc;
  const storage =
    deps.storage ?? (typeof sessionStorage !== "undefined" ? sessionStorage : undefined);

  let installed: InstalledModelPack[];
  try {
    installed = await list();
  } catch {
    hide(deps.container); // status unknown -> don't cry wolf
    return [];
  }

  const missing = computeMissingModelPacks(installed, preferred);
  if (!missing.length) {
    hide(deps.container);
    return [];
  }

  const key = missing
    .map((p) => p.id)
    .sort()
    .join(",");
  if (storage?.getItem(DISMISS_KEY) === key) {
    hide(deps.container);
    return missing;
  }

  const names = missing.map((p) => escapeHtml(p.id)).join(", ");
  const plural = missing.length === 1 ? "" : "s";
  deps.container.hidden = false;
  deps.container.innerHTML = `
    <div class="modelInstallBannerInner">
      <span class="modelInstallBannerIcon" aria-hidden="true">⚠</span>
      <span class="grow"><strong>${missing.length} model pack${plural} not installed</strong>
        — ${names}. Install ${missing.length === 1 ? "it" : "them"} for full transcription / import support.</span>
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
