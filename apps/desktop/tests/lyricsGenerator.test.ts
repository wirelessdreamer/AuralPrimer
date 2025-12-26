import { generateLyricsJsonFromPlainText } from "../src/lyricsGenerator";

describe("lyricsGenerator", () => {
  it("splits lines and distributes them across duration", () => {
    const out = generateLyricsJsonFromPlainText({
      lyricsText: "Line A\n\nLine B\nLine C",
      durationSec: 9,
      jobId: "t",
    });

    expect(out.format).toBe("psalms_karaoke_json_v1");
    expect(out.lines).toHaveLength(3);
    expect(out.lines[0]).toMatchObject({ start: 0, end: 3, text: "Line A" });
    expect(out.lines[1]).toMatchObject({ start: 3, end: 6, text: "Line B" });
    expect(out.lines[2]).toMatchObject({ start: 6, end: 9, text: "Line C" });
  });
});

