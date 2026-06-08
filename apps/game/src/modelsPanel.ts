/**
 * Models panel — preferred-modelpack list, installed-modelpack status,
 * and the import-from-path control.
 *
 * Owns:
 *   - #modelsRefresh, #preferredModels, #modelsStatus,
 *     #modelpackPath, #modelpackImport (5 DOM elements)
 *   - the Install/refresh button wiring
 *   - the per-pack Install buttons rendered into the preferred list
 *   - the install-from-path button
 *
 * Pure presentation + Tauri invoke; no transport / song / midi coupling.
 * Extracted from main.ts as Phase 2.K.
 */

import { PREFERRED_MODEL_PACKS } from "./models/preferredModelPacks";
import {
  installModelPackFromPath,
  installModelPackFromUrl,
  listInstalledModelPacks,
  type InstalledModelPack,
} from "./models/modelManager";

export type ModelsPanelDeps = {
  /** Same HTML-escape used elsewhere in the host; passed in to avoid re-implementing it here. */
  escapeHtml: (s: string) => string;
};

export type ModelsPanelHandle = {
  /** Re-render the preferred-packs list (called on boot). */
  renderPreferred: () => void;
  /** Refresh the installed-pack status (called on boot, after installs). */
  refresh: () => Promise<void>;
};

function formatModelPackLicense(pack: InstalledModelPack): string {
  if (typeof pack.license === "string") return pack.license;
  if (pack.license && typeof pack.license === "object") {
    const record = pack.license as Record<string, unknown>;
    for (const key of ["name", "id", "spdx", "text"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return "not declared";
}

function formatInstalledModelPacks(installed: InstalledModelPack[]): string {
  if (!installed.length) return "(no model packs installed)";
  return installed
    .map((pack) => {
      const lines = [
        `${pack.id}@${pack.version}${pack.ok ? "" : " [invalid]"}`,
        `  root: ${pack.root_dir}`,
        `  license: ${formatModelPackLicense(pack)}`,
      ];
      if (pack.license_path) lines.push(`  license_path: ${pack.license_path}`);
      if (pack.error) lines.push(`  error: ${pack.error}`);
      return lines.join("\n");
    })
    .join("\n\n");
}

export function initModelsPanel(deps: ModelsPanelDeps): ModelsPanelHandle {
  const refreshBtn = document.getElementById("modelsRefresh") as HTMLButtonElement | null;
  const preferredEl = document.getElementById("preferredModels") as HTMLDivElement | null;
  const statusEl = document.getElementById("modelsStatus") as HTMLPreElement | null;
  const pathInput = document.getElementById("modelpackPath") as HTMLInputElement | null;
  const importBtn = document.getElementById("modelpackImport") as HTMLButtonElement | null;
  if (!refreshBtn || !preferredEl || !statusEl || !pathInput || !importBtn) {
    throw new Error("initModelsPanel: required DOM (#modelsRefresh/#preferredModels/#modelsStatus/#modelpackPath/#modelpackImport) missing");
  }

  async function refresh(): Promise<void> {
    statusEl!.textContent = "Loading…";
    try {
      const installed = await listInstalledModelPacks();
      statusEl!.textContent = formatInstalledModelPacks(installed);
    } catch (e) {
      statusEl!.textContent = String(e);
    }
  }

  function renderPreferred(): void {
    preferredEl!.innerHTML = `
      <ul>
        ${PREFERRED_MODEL_PACKS.map((p) => {
          const disabled = p.url ? "" : "disabled";
          const hint = p.url ? "" : "(no download url configured yet)";
          return `
            <li>
              <div class="row">
                <div class="grow">
                  <strong>${deps.escapeHtml(p.id)}</strong> <span class="meta">v${deps.escapeHtml(p.version)}</span>
                  <div class="meta">${deps.escapeHtml(p.description ?? "")} ${deps.escapeHtml(hint)}</div>
                </div>
                <button class="installPreferred" data-id="${deps.escapeHtml(p.id)}" ${disabled}>Install</button>
              </div>
            </li>
          `;
        }).join("\n")}
      </ul>
    `;

    for (const btn of Array.from(preferredEl!.querySelectorAll("button.installPreferred"))) {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-id");
        if (!id) return;
        const pack = PREFERRED_MODEL_PACKS.find((p) => p.id === id);
        if (!pack) return;
        void installModelPackFromUrl(pack)
          .then(() => refresh())
          .catch((e) => {
            statusEl!.textContent = String(e);
          });
      });
    }
  }

  refreshBtn.addEventListener("click", () => {
    void refresh();
  });

  importBtn.addEventListener("click", () => {
    const p = pathInput!.value;
    void installModelPackFromPath(p)
      .then(() => refresh())
      .catch((e) => {
        statusEl!.textContent = String(e);
      });
  });

  return { renderPreferred, refresh };
}
