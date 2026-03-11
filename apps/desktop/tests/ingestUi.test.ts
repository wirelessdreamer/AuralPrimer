import { buildIngestRequestFromForm, inferIngestTitleArtistFromSourcePath } from "../src/ingestUi";

describe("ingestUi", () => {
  it("builds normalized request with defaults", () => {
    const req = buildIngestRequestFromForm({
      sourcePath: "  C:/music/in.wav  ",
      mode: "import",
      outSongpackPath: "  ",
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
      out_songpack_path: undefined,
      profile: "full",
      config: undefined,
      title: "Song Title",
      artist: "Artist",
      drum_filter: "combined_filter",
      melodic_method: "basic_pitch",
      shifts: 1,
      multi_filter: false
    });
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
