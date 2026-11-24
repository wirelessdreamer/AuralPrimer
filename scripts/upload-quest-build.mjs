/**
 * Uploads the Quest APK to a Meta Horizon Store release channel.
 *
 *   node scripts/upload-quest-build.mjs [channel] [--notes "..."] [--yes]
 *
 * Channel defaults to ALPHA, because the cost of a mistaken upload rises
 * sharply with the channel and nothing about an automated run should make
 * reaching real users the easy path. Uploading to the production channel is
 * refused unless --yes is passed as well.
 *
 * The App ID is public and lives here. The App Secret is not: it comes from
 * META_APP_SECRET, or failing that from ~/.auralprimer/meta.env, which sits
 * beside the signing material and outside the repository.
 *
 * Get it from the Developer Dashboard -> your app -> API, with an admin
 * account. Unlike the signing key it is rotatable -- if it leaks, regenerate it
 * there and nothing is permanently lost.
 */
import { spawnSync } from "node:child_process";
import { existsSync, openSync, readSync, closeSync, statSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const ROOT = join(import.meta.dirname, "..");
const APP_ID = "1418730557979975";

/**
 * Set once and effectively permanent: Meta treats the age group as fixed after
 * publishing. MIXED_AGES makes the app installable by under-13s, which is only
 * defensible because nothing here reports anywhere -- the client's only sockets
 * are LAN discovery and a TCP link to the desktop, and there is no analytics in
 * the tree. If that ever stops being true, this line is the thing that made it
 * a compliance problem rather than a feature.
 */
const AGE_GROUP = "MIXED_AGES";

const APK = join(ROOT, "UnityClient", "Builds", "AuralPrimer.apk");
const SYMBOLS_DIR = join(ROOT, "UnityClient", "Builds");

function fail(message, hint) {
  console.error(`upload: ${message}`);
  if (hint) console.error(`        ${hint}`);
  process.exit(1);
}

/** Where ovr-platform-util might be, in order of how explicit the choice was. */
function findPlatformUtil() {
  const fromEnv = process.env.OVR_PLATFORM_UTIL;
  if (fromEnv) {
    if (!existsSync(fromEnv)) fail(`OVR_PLATFORM_UTIL points at nothing: ${fromEnv}`);
    return fromEnv;
  }
  const onPath = spawnSync(process.platform === "win32" ? "where" : "which",
    ["ovr-platform-util"], { encoding: "utf8" });
  if (onPath.status === 0) return onPath.stdout.split(/\r?\n/)[0].trim();
  fail("ovr-platform-util not found",
    "Download it from https://developers.meta.com/horizon/downloads/package/platform-utils-cli-pc/ " +
    "and either put it on PATH or set OVR_PLATFORM_UTIL.");
}

/**
 * Refuse to spend ten minutes uploading something the store will reject.
 *
 * With no keystore configured Unity signs with the Android debug key, whose
 * certificate subject is the literal "Android Debug" -- and that string
 * survives DER encoding as plain ASCII inside the signature block. Meta rejects
 * debug-signed builds, but only after the transfer completes, so catching it
 * here saves the whole round trip.
 */
function isDebugSigned(apkPath) {
  const needle = Buffer.from("Android Debug", "ascii");
  const size = statSync(apkPath).size;
  const chunk = 1 << 20;
  const fd = openSync(apkPath, "r");
  try {
    // Overlap each read by the needle length so a match spanning a chunk
    // boundary is not missed.
    const buf = Buffer.alloc(chunk + needle.length);
    let pos = 0;
    while (pos < size) {
      const read = readSync(fd, buf, 0, buf.length, pos);
      if (read <= 0) break;
      if (buf.subarray(0, read).includes(needle)) return true;
      pos += chunk;
    }
    return false;
  } finally {
    closeSync(fd);
  }
}

/**
 * The App Secret, from the environment or the credentials file.
 *
 * A file is offered as well as an environment variable because the alternative
 * in practice is pasting the secret onto a command line, where it lands in
 * shell history. The placeholder written by the setup is rejected rather than
 * sent, so a half-finished setup fails here instead of at the API.
 */
function readAppSecret() {
  if (process.env.META_APP_SECRET) return process.env.META_APP_SECRET;

  const file = process.env.AURAL_CREDENTIALS
    ?? join(homedir(), ".auralprimer", "meta.env");
  if (!existsSync(file)) return null;

  // Split and trim rather than matching a newline pattern: trimming also
  // disposes of the carriage return on a CRLF file, which a naive split
  // would otherwise leave glued to the end of the value.
  // Split and trim rather than matching a newline pattern: trimming also
  // disposes of the carriage return on a CRLF file, which a naive split
  // would otherwise leave glued to the end of the value.
  const line = readFileSync(file, "utf8")
    .split("\n")
    .map((l) => l.trim())
    .find((l) => !l.startsWith("#") && l.startsWith("META_APP_SECRET"));
  if (!line) return null;

  const value = line.slice(line.indexOf("=") + 1).trim().replace(/^["']|["']$/g, "");
  if (!value || value.startsWith("<")) return null; // still the placeholder
  return value;
}

const argv = process.argv.slice(2);
const yes = argv.includes("--yes");
const notesAt = argv.indexOf("--notes");
const notes = notesAt >= 0 ? argv[notesAt + 1] : null;
const channel = argv.find((a) => !a.startsWith("--") && a !== notes) ?? "ALPHA";

const secret = readAppSecret();
if (!secret) {
  fail("no Meta App Secret available",
    `Set META_APP_SECRET, or put it in ${join(homedir(), ".auralprimer", "meta.env")}. ` +
    "Get it from the Developer Dashboard -> your app -> API (admin account).");
}
if (!existsSync(APK)) fail(`no APK at ${APK}`, "Build it first with BuildCommand.BuildAndroid.");

if (/^(store|live|production)$/i.test(channel) && !yes) {
  fail(`refusing to upload to "${channel}" without --yes`,
    "That channel is visible to real users.");
}

if (isDebugSigned(APK)) {
  fail("this APK is signed with the Android debug key",
    "Meta rejects debug-signed builds. Rebuild with AURAL_KEYSTORE and friends " +
    "set (see BUILDING.md section 8) -- not through the Unity Editor, which " +
    "would write the password into tracked ProjectSettings.asset.");
}

const args = [
  "upload-quest-build",
  "--app-id", APP_ID,
  "--app-secret", secret,
  "--apk", APK,
  "--channel", channel,
  "--age-group", AGE_GROUP,
];
if (existsSync(SYMBOLS_DIR)) {
  args.push("--debug-symbols-dir", SYMBOLS_DIR, "--debug-symbols-pattern", "*.symbols.zip");
}
if (notes) args.push("--notes", notes);

// Logged with the secret masked, so a pasted terminal does not leak it.
console.log(`upload: ${channel} / ${AGE_GROUP} / ${(statSync(APK).size / 1e6).toFixed(0)}MB`);
console.log(`        ${args.map((a) => (a === secret ? "***" : a)).join(" ")}`);

const run = spawnSync(findPlatformUtil(), args, { stdio: "inherit" });
process.exit(run.status ?? 1);
