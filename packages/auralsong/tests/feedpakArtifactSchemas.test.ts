import path from "node:path";
import { promises as fs } from "node:fs";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020";

import auralFingeringSchema from "../../feedpak/schemas/aural-fingering.schema.json";
import drumTabSchema from "../../feedpak/schemas/drum-tab.schema.json";
import harmonySchema from "../../feedpak/schemas/harmony.schema.json";
import keysSchema from "../../feedpak/schemas/keys.schema.json";
import songTimelineSchema from "../../feedpak/schemas/song-timeline.schema.json";
import vocalPitchSchema from "../../feedpak/schemas/vocal-pitch.schema.json";
import vocalPitchContourSchema from "../../feedpak/schemas/vocal-pitch-contour.schema.json";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(HERE, "../../feedpak/fixtures/minimal.feedpak");

const ajv = new Ajv2020({ allErrors: true, strict: false });

async function readFixtureJson(relPath: string): Promise<unknown> {
  return JSON.parse(await fs.readFile(path.join(FIXTURE_DIR, relPath), "utf-8"));
}

describe("feedpak model-upgrade artifact schemas", () => {
  it.each([
    ["song_timeline.json", songTimelineSchema],
    ["drum_tab.json", drumTabSchema],
    ["keys.json", keysSchema],
    ["harmony.json", harmonySchema],
    ["vocal_pitch.json", vocalPitchSchema],
    ["vocal_pitch_contour.json", vocalPitchContourSchema],
    ["aural/fingering.lead_guitar.json", auralFingeringSchema],
  ])("validates %s in the minimal feedpak fixture", async (relPath, schema) => {
    const validate = ajv.compile(schema as object);
    const doc = await readFixtureJson(relPath);

    expect(validate(doc), JSON.stringify(validate.errors ?? [], null, 2)).toBe(true);
  });

  it("accepts compact string/fret aliases in fingering sidecars", () => {
    const validate = ajv.compile(auralFingeringSchema as object);
    const doc = {
      version: "1.0.0",
      instrument: "lead_guitar",
      notes: [{ t_on: 0.1, pitch: 60, s: 2, f: 8 }],
    };

    expect(validate(doc), JSON.stringify(validate.errors ?? [], null, 2)).toBe(true);
  });

  it("accepts generic guitar fingering sidecars", () => {
    const validate = ajv.compile(auralFingeringSchema as object);
    const doc = {
      version: "1.0.0",
      instrument: "guitar",
      notes: [{ t_on: 0.1, pitch: 60, string: 2, fret: 8 }],
    };

    expect(validate(doc), JSON.stringify(validate.errors ?? [], null, 2)).toBe(true);
  });

  it("rejects unusable fingering sidecars", () => {
    const validate = ajv.compile(auralFingeringSchema as object);
    const doc = {
      version: "1.0.0",
      instrument: "lead_guitar",
      notes: [{ t_on: 0.1, pitch: 60, string: 99, fret: 5 }],
    };

    expect(validate(doc)).toBe(false);
    expect(validate.errors?.map((err) => err.instancePath)).toContain("/notes/0/string");
  });

  it("rejects vocal pitch notes the lyrics visualizer would drop", () => {
    const validate = ajv.compile(vocalPitchSchema as object);
    const doc = { version: 1, notes: [{ t: 0.25, d: 0, midi: 69 }] };

    expect(validate(doc)).toBe(false);
    expect(validate.errors?.map((err) => err.instancePath)).toContain("/notes/0/d");
  });

  it("rejects vocal pitch contour samples the lyrics visualizer would drop", () => {
    const validate = ajv.compile(vocalPitchContourSchema as object);
    const doc = { version: 1, samples: [{ t: 0.25, hz: 0 }] };

    expect(validate(doc)).toBe(false);
    expect(validate.errors?.map((err) => err.instancePath)).toContain("/samples/0/hz");
  });

  it("accepts harmony key metadata and positive event durations", () => {
    const validate = ajv.compile(harmonySchema as object);
    const doc = {
      version: 1,
      key: "Bb",
      mode: "minor",
      confidence: 0.73,
      score: 0.91,
      events: [{ t: 0, duration: 2, root: "Bb", quality: "min", rn: "i" }],
    };

    expect(validate(doc), JSON.stringify(validate.errors ?? [], null, 2)).toBe(true);
  });

  it("rejects harmony event durations the Nashville visualizer would ignore", () => {
    const validate = ajv.compile(harmonySchema as object);
    const doc = { version: 1, events: [{ t: 0, duration: 0, root: "C" }] };

    expect(validate(doc)).toBe(false);
    expect(validate.errors?.map((err) => err.instancePath)).toContain("/events/0/duration");
  });
});
