/**
 * Brand asset generator.
 *
 * The mark is not drawn, it is derived. Every vertex of the A lands on a node
 * of a triangular pitch lattice -- a Tonnetz -- and every stroke runs along a
 * lattice edge. The triangle enclosed by the apex and the crossbar is three
 * mutually adjacent nodes, which in a Tonnetz is a triad: the letter's counter
 * and a chord are the same three points. Primer lights the upward triangle
 * (major); Studio lights the downward one directly below it (minor). Same
 * glyph, same nodes, opposite chord.
 *
 * Why the legs are straight
 * -------------------------
 * A triangular lattice offers three stroke directions and no vertical, so a
 * narrow A is only possible by zigzagging its legs between the two diagonals.
 * That was tried and rejected: a zigzagged leg cannot also splay, so the
 * letter ends up as a triangle standing on two near-vertical wiggles and reads
 * as a figure rather than an A. Splay and serration are mutually exclusive
 * here, and splay is what makes an A an A.
 *
 * The payoff is that ONE letterform covers every size. Only two things change
 * with scale, and both are ordinary optical sizing:
 *
 *   >= 256px   the lattice field is drawn, stroke light, the lone triad lit
 *   48..128px  field dropped (it goes to grey mush), stroke heavier, and the
 *              whole counter lit because a single triad is a few pixels
 *   <= 32px    the glyph grows into the tile and the stroke heavier still,
 *              which is the difference between a readable 16px icon and a
 *              smudge -- Windows asks for 16px more often than anything else
 *
 * No font is involved, so these render identically everywhere.
 * Run: node scripts/brand/generate-brand.mjs
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..", "..");
const OUT = join(ROOT, "assets", "brand");
const HH = Math.sqrt(3) / 2;
const S = 1024;

/**
 * Lattice coordinates: x runs across, r runs down one row at a time, and odd
 * rows are offset half a unit -- so (1.5, 1) is a node and (1.5, 0) is not.
 */
const FORM = {
  // Five rows, continuous 60 degree splay. The shipping form.
  splay: {
    rows: 4,
    legs: [[0, 4], [0.5, 3], [1, 2], [1.5, 1], [2, 0], [2.5, 1], [3, 2], [3.5, 3], [4, 4]],
    bar: [[1, 2], [2, 2], [3, 2]],
    triad: { major: [[1.5, 1], [2, 0], [2.5, 1]], minor: [[1.5, 1], [2.5, 1], [2, 2]] },
    // The whole counter: a triangle of side 2, which on this lattice is four
    // unit triangles -- the lit triad plus its three immediate neighbours.
    counter: [[1, 2], [3, 2], [2, 0]],
  },
  // Kept because it is the only form that keeps a kink in the leg, should the
  // woven look ever be wanted back. Legible, but the bow reads as a flare.
  bowed: {
    rows: 4,
    legs: [[0, 4], [-0.5, 3], [0, 2], [0.5, 1], [1, 0], [1.5, 1], [2, 2], [2.5, 3], [2, 4]],
    bar: [[0, 2], [1, 2], [2, 2]],
    triad: { major: [[0.5, 1], [1, 0], [1.5, 1]], minor: [[0.5, 1], [1.5, 1], [1, 2]] },
    counter: [[0, 2], [2, 2], [1, 0]],
  },
};

const THEME = {
  primer: { bg: "#0f1117", ink: "#f4f4f6", accent: "#56e885", edge: "#2b3349", dot: "#39425f" },
  studio: { bg: "#0f1117", ink: "#f4f4f6", accent: "#7cc6ff", edge: "#2b3349", dot: "#39425f" },
};

const n = (v) => Number(v.toFixed(2));

function mark({ form = "splay", triad, theme, grid, rounded, fill, weight, lit }) {
  const f = FORM[form];
  const U = (fill * S) / (f.rows * HH);
  const cR = f.rows / 2;
  const xs = f.legs.concat(f.bar).map((p) => p[0]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const at = (x, r) => [n((x - cx) * U + S / 2), n((r - cR) * HH * U + S / 2)];
  const px = (x, r) => at(x, r).join(",");
  const d = (poly) => poly.map((p, i) => (i ? "L" : "M") + px(p[0], p[1])).join(" ");

  const rx = rounded ? n(0.22 * S) : 0;
  const parts = [`<rect width="${S}" height="${S}"${rx ? ` rx="${rx}"` : ""} fill="${theme.bg}"/>`];

  if (grid) {
    // Bleeds to the tile edge rather than sitting inside a border: a Tonnetz
    // does not end, and a framed patch would read as a badge.
    let rows = "", diag = "", dots = "";
    const span = 5;
    for (let r = -4; r <= f.rows + 4; r++) {
      const off = (((r % 2) + 2) % 2) ? 0.5 : 0;
      const lo = Math.ceil(cx - span - off), hi = Math.floor(cx + span - off);
      rows += `M${px(lo + off, r)}L${px(hi + off, r)}`;
      for (let i = lo; i <= hi; i++) {
        const x = i + off;
        const [dx, dy] = at(x, r);
        dots += `<circle cx="${dx}" cy="${dy}" r="${n(0.058 * U)}"/>`;
        if (r < f.rows + 4) diag += `M${px(x, r)}L${px(x - 0.5, r + 1)}M${px(x, r)}L${px(x + 0.5, r + 1)}`;
      }
    }
    parts.push(
      `<g style="clip-path:inset(0 round ${rx}px)">` +
      `<g stroke="${theme.edge}" stroke-width="${n(0.036 * U)}" fill="none">` +
      `<path d="${rows}"/><path d="${diag}"/></g>` +
      `<g fill="${theme.dot}">${dots}</g></g>`
    );
  }

  // Which triangle gets the accent is an optical-sizing call. Large: the
  // single triad, so Primer's major points up and Studio's minor points down
  // and the pair is told apart by shape. Small: the whole counter, because a
  // lone triad is four pixels at 32 and the colour has to carry the identity
  // on its own -- at which size the up/down distinction was invisible anyway.
  const shape = lit === "counter" ? f.counter : f.triad[triad];
  parts.push(`<path d="${d(shape)}Z" fill="${theme.accent}"/>`);
  parts.push(
    `<g stroke="${theme.ink}" stroke-width="${n(weight * U)}" fill="none" ` +
    `stroke-linecap="round" stroke-linejoin="round">` +
    `<path d="${d(f.legs)}"/><path d="${d(f.bar)}"/></g>`
  );

  const label = `Letter A traced along a triangular pitch lattice, ` +
    `its counter filled as a ${triad} triad`;
  void label;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" ` +
    `width="${S}" height="${S}" role="img" aria-label="${label}">\n  ` +
    parts.join("\n  ") + `\n</svg>\n`;
}

const BIG = { grid: true, fill: 0.60, weight: 0.195, lit: "triad" };
const SMALL = { grid: false, fill: 0.60, weight: 0.32, lit: "counter" };
const TINY = { grid: false, fill: 0.66, weight: 0.42, lit: "counter" };

const FILES = {
  // Desktop / Tauri: corner radius baked in, lattice field, light stroke.
  "auralprimer-icon.svg":        { ...BIG, triad: "major", theme: THEME.primer, rounded: true },
  "auralstudio-icon.svg":        { ...BIG, triad: "minor", theme: THEME.studio, rounded: true },
  // Store listings mask their own corners and reject transparency.
  "auralprimer-icon-square.svg": { ...BIG, triad: "major", theme: THEME.primer, rounded: false },
  "auralstudio-icon-square.svg": { ...BIG, triad: "minor", theme: THEME.studio, rounded: false },
  // Same letterform, no field, heavier stroke. For 48..128px.
  "auralprimer-mark-small.svg":  { ...SMALL, triad: "major", theme: THEME.primer, rounded: true },
  "auralstudio-mark-small.svg":  { ...SMALL, triad: "minor", theme: THEME.studio, rounded: true },
  // Heaviest, and nearly filling the tile. For 32px and below.
  "auralprimer-mark-tiny.svg":   { ...TINY, triad: "major", theme: THEME.primer, rounded: true },
  "auralstudio-mark-tiny.svg":   { ...TINY, triad: "minor", theme: THEME.studio, rounded: true },
};

mkdirSync(OUT, { recursive: true });
for (const [name, cfg] of Object.entries(FILES)) {
  const svg = mark(cfg);
  writeFileSync(join(OUT, name), svg, "utf8");
  console.log(`assets/brand/${name.padEnd(30)} ${String(svg.length).padStart(6)} bytes`);
}
