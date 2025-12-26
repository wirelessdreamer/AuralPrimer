import type { PreferredModelPack } from "./preferredModelPacks";

// Keep types minimal and JSON-serializable for Tauri invoke.

export type InstalledModelPack = {
  id: string;
  version: string;
  root_dir: string;
  manifest_path: string;
  ok: boolean;
  error?: string;
};

export type InstallZipRequest = {
  zip_bytes: number[];
  expected_zip_sha256?: string;
};

/**
 * Avoid importing Tauri APIs in Node test environment.
 */
async function getInvoke() {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke;
}

export async function listInstalledModelPacks(): Promise<InstalledModelPack[]> {
  const invoke = await getInvoke();
  return invoke<InstalledModelPack[]>("list_installed_modelpacks");
}

export async function installModelPackFromUrl(pack: PreferredModelPack): Promise<void> {
  if (!pack.url) throw new Error(`preferred model pack ${pack.id} has no url configured`);

  // Download via fetch in the renderer, then pass bytes to Rust for extraction.
  const res = await fetch(pack.url);
  if (!res.ok) throw new Error(`download failed: ${res.status} ${res.statusText}`);
  const ab = await res.arrayBuffer();
  const bytes = Array.from(new Uint8Array(ab));

  const invoke = await getInvoke();
  const req: InstallZipRequest = {
    zip_bytes: bytes,
    expected_zip_sha256: pack.sha256,
  };

  await invoke("install_modelpack_zip_bytes", req);
}

export async function installModelPackFromPath(path: string): Promise<void> {
  const p = path.trim();
  if (!p) throw new Error("missing path");

  const invoke = await getInvoke();
  await invoke("install_modelpack_from_path", { path: p });
}
