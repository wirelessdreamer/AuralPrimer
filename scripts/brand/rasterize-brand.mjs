/**
 * Rasterises the brand SVGs with the Chromium that Playwright already ships,
 * so no new dependency is needed to produce the PNG set.
 *
 * Which drawing is used at which size is the whole point of there being two:
 * the grid version's lattice edges are 3.6% of a lattice unit and go to mush
 * below about 256px, where the lone triad is also down to a few pixels. So the
 * crossover is baked in here rather than left to whoever exports next time.
 */
import { chromium } from "playwright";
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const ROOT = join(import.meta.dirname, "..", "..");
const BRAND = join(ROOT, "assets", "brand");
const PNG = join(BRAND, "png");

/** Three optical tiers; see the header of generate-brand.mjs for why. */
const GRID_FLOOR = 256;
const TINY_CEIL = 32;
const SIZES = [1024, 512, 256, 128, 64, 48, 32, 24, 16];

const APPS = [
  { slug: "auralprimer", grid: "auralprimer-icon.svg", small: "auralprimer-mark-small.svg", tiny: "auralprimer-mark-tiny.svg", square: "auralprimer-icon-square.svg" },
  { slug: "auralstudio", grid: "auralstudio-icon.svg", small: "auralstudio-mark-small.svg", tiny: "auralstudio-mark-tiny.svg", square: "auralstudio-icon-square.svg" },
];

mkdirSync(PNG, { recursive: true });
/**
 * Playwright pins a browser revision and the installed set does not always
 * contain that exact one -- an npm bump leaves the old builds on disk and the
 * new one undownloaded. Any recent Chromium rasterises SVG identically, so
 * take the newest that is actually present rather than failing or pulling a
 * 150MB download for a handful of PNGs.
 */
function findChromium() {
  const root = join(homedir(), "AppData", "Local", "ms-playwright");
  if (existsSync(root)) {
    const builds = readdirSync(root)
      .map((d) => /^chromium(_headless_shell)?-(\d+)$/.exec(d))
      .filter(Boolean)
      .map((m) => ({ dir: m[0], rev: Number(m[2]) }))
      .sort((a, b) => b.rev - a.rev);
    for (const b of builds) {
      for (const rel of [
        ["chrome-headless-shell-win64", "chrome-headless-shell.exe"],
        ["chrome-win64", "chrome.exe"],
      ]) {
        const exe = join(root, b.dir, ...rel);
        if (existsSync(exe)) return exe;
      }
    }
  }
  return undefined; // let Playwright try its own default
}

const exe = findChromium();
if (exe) console.log(`using ${exe}`);
const browser = await chromium.launch(exe ? { executablePath: exe } : {});
const page = await browser.newPage();

for (const app of APPS) {
  for (const size of SIZES) {
    const src = size >= GRID_FLOOR ? app.grid : size <= TINY_CEIL ? app.tiny : app.small;
    const svg = readFileSync(join(BRAND, src), "utf8")
      .replace(/width="\d+" height="\d+"/, `width="${size}" height="${size}"`);
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(
      `<!doctype html><style>html,body{margin:0;padding:0;background:transparent}` +
      `svg{display:block}</style>${svg}`,
      { waitUntil: "load" }
    );
    const buf = await page.screenshot({ omitBackground: true });
    const name = `${app.slug}-${size}.png`;
    writeFileSync(join(PNG, name), buf);
    console.log(`${name.padEnd(28)} from ${src}`);
  }
}

// Store listings round their own corners and reject an alpha channel, so they
// get the square drawing flattened onto its own background rather than the
// desktop one with a radius already baked in.
const STORE_SIZES = [1024, 512];
for (const app of APPS) {
  for (const size of STORE_SIZES) {
    const svg = readFileSync(join(BRAND, app.square), "utf8")
      .replace(/width="\d+" height="\d+"/, `width="${size}" height="${size}"`);
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(
      `<!doctype html><style>html,body{margin:0;padding:0}svg{display:block}</style>${svg}`,
      { waitUntil: "load" }
    );
    const name = `${app.slug}-store-${size}.png`;
    writeFileSync(join(PNG, name), await page.screenshot({ omitBackground: false }));
    console.log(`${name.padEnd(28)} from ${app.square}`);
  }
}

// A contact sheet, so a change to the geometry can be eyeballed in one look
// instead of opening nine files.
const sheet = (slug) => APPS.find((a) => a.slug === slug);
for (const app of APPS) {
  const tiles = SIZES.map((s) => {
    const src = s >= GRID_FLOOR ? app.grid : s <= TINY_CEIL ? app.tiny : app.small;
    const svg = readFileSync(join(BRAND, src), "utf8")
      .replace(/width="\d+" height="\d+"/, `width="${Math.min(s, 256)}" height="${Math.min(s, 256)}"`);
    return `<figure><div>${svg}</div><figcaption>${s}</figcaption></figure>`;
  }).join("");
  await page.setViewportSize({ width: 1180, height: 420 });
  await page.setContent(
    `<!doctype html><style>
      body{margin:0;background:#1b1e26;font:12px system-ui;color:#8d93ad;
           display:flex;align-items:flex-end;gap:18px;padding:24px;flex-wrap:wrap}
      figure{margin:0;display:flex;flex-direction:column;gap:7px;align-items:center}
      figcaption{font-weight:600}
    </style>${tiles}`,
    { waitUntil: "load" }
  );
  const buf = await page.screenshot({ fullPage: true });
  writeFileSync(join(PNG, `_contact-${app.slug}.png`), buf);
  console.log(`_contact-${app.slug}.png`);
}

await browser.close();
