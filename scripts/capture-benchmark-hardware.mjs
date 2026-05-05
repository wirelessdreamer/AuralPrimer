import { mkdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { arch, cpus, freemem, platform, release, totalmem, type } from "node:os";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve(root, "benchmarks", "hardware");
const outPath = resolve(outDir, "local-profile.latest.json");
mkdirSync(outDir, { recursive: true });

function bytesToGb(value) {
  return Math.round((Number(value || 0) / 1024 / 1024 / 1024) * 1000) / 1000;
}

function classifyProfile({ logicalCpuCount, memoryGb }) {
  if (logicalCpuCount >= 12 && memoryGb >= 32) {
    return "recommended_model_workstation";
  }
  if (logicalCpuCount >= 8 && memoryGb >= 16) {
    return "minimum_modern";
  }
  return "below_minimum_modern";
}

function getWindowsGpus() {
  const result = spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-Command",
      "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Depth 3",
    ],
    { encoding: "utf-8", shell: false },
  );
  if (result.status !== 0 || !result.stdout.trim()) {
    return [];
  }
  try {
    const parsed = JSON.parse(result.stdout);
    const rows = Array.isArray(parsed) ? parsed : [parsed];
    return rows.map((row) => ({
      name: row.Name ?? null,
      reported_adapter_ram_gb: row.AdapterRAM ? bytesToGb(row.AdapterRAM) : null,
      driver_version: row.DriverVersion ?? null,
    }));
  } catch {
    return [];
  }
}

function getUnixGpus() {
  const result = spawnSync("sh", ["-lc", "command -v lspci >/dev/null && lspci || true"], {
    encoding: "utf-8",
    shell: false,
  });
  if (result.status !== 0 || !result.stdout.trim()) {
    return [];
  }
  return result.stdout
    .split(/\r?\n/)
    .filter((line) => /vga|3d controller|display controller/i.test(line))
    .map((line) => ({ name: line.trim(), reported_adapter_ram_gb: null, driver_version: null }));
}

const cpuList = cpus();
const logicalCpuCount = cpuList.length;
const cpuModel = cpuList[0]?.model ?? "unknown";
const memoryGb = bytesToGb(totalmem());
const freeMemoryGb = bytesToGb(freemem());
const gpus = platform() === "win32" ? getWindowsGpus() : getUnixGpus();

const payload = {
  generated_at_utc: new Date().toISOString(),
  schema: "auralprimer_benchmark_hardware_profile.v1",
  target_profile_id: classifyProfile({ logicalCpuCount, memoryGb }),
  platform: {
    os: platform(),
    type: type(),
    release: release(),
    arch: arch(),
  },
  cpu: {
    model: cpuModel,
    logical_count: logicalCpuCount,
  },
  memory: {
    total_gb: memoryGb,
    free_gb_at_capture: freeMemoryGb,
  },
  gpu: {
    acceleration_required_for_default_import: false,
    detected: gpus,
  },
  runtime: {
    node: process.version,
    npm_user_agent: process.env.npm_config_user_agent ?? null,
  },
};

writeFileSync(outPath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
console.log(outPath);
