/**
 * Puts the generated brand assets where the apps actually read them.
 *
 * Also replaces the shipped icon.ico, which held a single 32x32 image -- so
 * Windows upscaled it for every larger slot (taskbar at 125% scaling, alt-tab,
 * the Programs list, file properties) and downsampled it for 16px. A real
 * multi-resolution .ico lets each slot pick the drawing meant for it, which is
 * the entire point of there being two drawings.
 *
 * Run after generate-brand.mjs and rasterize-brand.mjs.
 */
import { readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..", "..");
const PNG = join(ROOT, "assets", "brand", "png");
const BRAND = join(ROOT, "assets", "brand");

/** Every size Windows asks for. 256 is stored as 0 in the directory entry. */
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];

/**
 * ICO is a directory of images; since Vista the payloads may be PNG rather
 * than BMP, which is what lets a 256px entry exist at a sane file size.
 */
function buildIco(pngs) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);            // reserved
  header.writeUInt16LE(1, 2);            // 1 = icon
  header.writeUInt16LE(pngs.length, 4);
  const entries = Buffer.alloc(16 * pngs.length);
  let offset = header.length + entries.length;
  pngs.forEach(({ size, data }, i) => {
    const e = i * 16;
    entries.writeUInt8(size >= 256 ? 0 : size, e);
    entries.writeUInt8(size >= 256 ? 0 : size, e + 1);
    entries.writeUInt8(0, e + 2);        // palette size
    entries.writeUInt8(0, e + 3);        // reserved
    entries.writeUInt16LE(1, e + 4);     // colour planes
    entries.writeUInt16LE(32, e + 6);    // bits per pixel
    entries.writeUInt32LE(data.length, e + 8);
    entries.writeUInt32LE(offset, e + 12);
    offset += data.length;
  });
  return Buffer.concat([header, entries, ...pngs.map((p) => p.data)]);
}

const TARGETS = [
  { slug: "auralprimer", app: "game",    label: "AuralPrimer" },
  { slug: "auralstudio", app: "desktop", label: "AuralStudio" },
];

for (const t of TARGETS) {
  const ico = buildIco(
    ICO_SIZES.map((size) => ({ size, data: readFileSync(join(PNG, `${t.slug}-${size}.png`)) }))
  );
  const icoPath = join(ROOT, "apps", t.app, "src-tauri", "icons", "icon.ico");
  writeFileSync(icoPath, ico);
  console.log(`${t.label}: icon.ico  ${ICO_SIZES.join("/")}  ${ico.length} bytes`);

  copyFileSync(join(PNG, `${t.slug}-512.png`),
    join(ROOT, "apps", t.app, "src-tauri", "icons", "icon.png"));
  console.log(`${t.label}: icon.png  512`);

  // The tiny tier, not the mid one: a favicon is rendered at 16-32px
  // wherever it is actually used, which is the range that tier exists for.
  copyFileSync(join(BRAND, `${t.slug}-mark-tiny.svg`),
    join(ROOT, "apps", t.app, "public", "favicon.svg"));
  console.log(`${t.label}: favicon.svg (tiny tier)`);
}
