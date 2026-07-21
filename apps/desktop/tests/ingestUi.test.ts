import { buildIngestRequestFromForm, inferIngestTitleArtistFromSourcePath } from "../src/ingestUi";

describe("ingestUi", () => {
  it("builds normalized request with defaults", () => {
    const req = buildIngestRequestFromForm({
      sourcePath: "  C:/music/in.wav  ",
      mode: "import",
      outAuralSongPath: "  ",
      profile: "",
      config: "  ",
      title: "  Song Title ",
      artist: "  Artist ",
      drumFilter: " combined_filter ",
      melodicMethod: " basic_pitch ",
      shiftsText: "",
      multiFilter: false
    });

    expect(req).toEqual({
      source_path: "C:/music/in.wav",
      subcommand: "import",
      out_auralsong_path: undefined,
      profile: "full",
      config: undefined,
      title: "Song Title",
      artist: "Artist",
      drum_filter: "combined_filter",
      melodic_method: "basic_pitch",
      wholemix_transcriber: undefined,
      shifts: 1,
      multi_filter: false
    });
  });

  it("passes the whole-mix transcriber through to the sidecar request", () => {
    // Regression: the sidecar accepted --wholemix-transcriber from the start,
    // but nothing in the UI ever sent it, so MuScriptor was unreachable from
    // the app even once its weights were installed.
    const req = buildIngestRequestFromForm({
      sourcePath: "C:/music/in.wav",
      mode: "import",
      wholemixTranscriber: "muscriptor",
      multiFilter: false
    });
    expect(req.wholemix_transcriber).toBe("muscriptor");
  });

  it("omits the whole-mix transcriber when it is off", () => {
    for (const value of [undefined, "", "   "]) {
      const req = buildIngestRequestFromForm({
        sourcePath: "C:/music/in.wav",
        mode: "import",
        wholemixTranscriber: value,
        multiFilter: false
      });
      expect(req.wholemix_transcriber).toBeUndefined();
    }
  });

  it("passes through piano melodic methods unchanged", () => {
    for (const melodicMethod of [
      " piano_auto ",
      " piano_basic_pitch_playable ",
      " piano_basic_pitch ",
      " piano_basic_pitch_clean "
    ]) {
      const req = buildIngestRequestFromForm({
        sourcePath: "C:/music/piano.wav",
        mode: "import",
        melodicMethod,
        multiFilter: false
      });

      expect(req.melodic_method).toBe(melodicMethod.trim());
    }
  });

  it("builds the piano Psalm configured-stem import request shape", () => {
    const req = buildIngestRequestFromForm({
      sourcePath: "D:/AuralPrimer/benchmarks/piano/.cache/excerpts/psalm_5_keyboard_polyphony_stress.wav",
      mode: "import",
      outAuralSongPath: "D:/AuralPrimer/tmp/psalm5-gui-smoke.auralsong",
      profile: "full",
      config: "D:/AuralPrimer/tmp/psalm5-excerpt.config.json",
      title: "Psalm 5 GUI Smoke",
      artist: "Psalms",
      drumFilter: "auto",
      melodicMethod: "piano_auto",
      shiftsText: "1",
      multiFilter: false
    });

    expect(req).toEqual({
      source_path: "D:/AuralPrimer/benchmarks/piano/.cache/excerpts/psalm_5_keyboard_polyphony_stress.wav",
      subcommand: "import",
      out_auralsong_path: "D:/AuralPrimer/tmp/psalm5-gui-smoke.auralsong",
      profile: "full",
      config: "D:/AuralPrimer/tmp/psalm5-excerpt.config.json",
      title: "Psalm 5 GUI Smoke",
      artist: "Psalms",
      drum_filter: "auto",
      melodic_method: "piano_auto",
      shifts: 1,
      multi_filter: false
    });
  });

  it("preserves default auto melodic routing for backend role-aware selection", () => {
    const req = buildIngestRequestFromForm({
      sourcePath: "C:/music/stems-folder",
      mode: "import-dir",
      melodicMethod: "auto",
      multiFilter: false
    });

    expect(req.melodic_method).toBe("auto");
  });

  it("validates required source and shifts", () => {
    expect(() =>
      buildIngestRequestFromForm({
        sourcePath: "   ",
        mode: "import",
        multiFilter: false
      })
    ).toThrow("source path is required");

    expect(() =>
      buildIngestRequestFromForm({
        sourcePath: "x.wav",
        mode: "import",
        shiftsText: "0",
        multiFilter: false
      })
    ).toThrow("shifts must be an integer >= 1");

    expect(() =>
      buildIngestRequestFromForm({
        sourcePath: "x.wav",
        mode: "import",
        shiftsText: "abc",
        multiFilter: false
      })
    ).toThrow("shifts must be an integer >= 1");
  });

  it("infers artist/title from artist-title filenames", () => {
    expect(inferIngestTitleArtistFromSourcePath("C:\\music\\My Artist - My Song.wav")).toEqual({
      title: "My Song",
      artist: "My Artist"
    });
    expect(
      inferIngestTitleArtistFromSourcePath("D:\\Psalms\\Book of Psalms - Psalm 1 - The Road.mp3")
    ).toEqual({
      title: "Psalm 1 - The Road",
      artist: "Book of Psalms"
    });
  });

  it("handles track-prefixed and single-title filenames", () => {
    expect(inferIngestTitleArtistFromSourcePath("C:/music/01 - Artist Name - Song Name.flac")).toEqual({
      title: "Song Name",
      artist: "Artist Name"
    });
    expect(inferIngestTitleArtistFromSourcePath("C:/music/lonely_song.ogg")).toEqual({
      title: "lonely song"
    });
  });
});
