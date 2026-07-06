import { isManifestPack, featureDir, packDisplayName } from "../src/packKind";

describe("packKind.isManifestPack", () => {
  it("is true for .feedpak and .sloppak", () => {
    expect(isManifestPack("C:/songs/Foo.feedpak")).toBe(true);
    expect(isManifestPack("C:/songs/Foo.sloppak")).toBe(true);
    expect(isManifestPack("/data/songs/minimal.sloppak")).toBe(true);
  });

  it("is false for legacy .auralsong and unknown extensions", () => {
    expect(isManifestPack("C:/songs/Foo.auralsong")).toBe(false);
    expect(isManifestPack("C:/songs/Foo.zip")).toBe(false);
    expect(isManifestPack("C:/songs/Foo")).toBe(false);
  });

  it("is case-insensitive and tolerates a trailing separator", () => {
    expect(isManifestPack("C:/songs/Foo.SLOPPAK")).toBe(true);
    expect(isManifestPack("C:/songs/Foo.FeedPak")).toBe(true);
    expect(isManifestPack("C:/songs/Foo.sloppak/")).toBe(true);
    expect(isManifestPack("C:\\songs\\Foo.sloppak\\")).toBe(true);
  });
});

describe("packKind.featureDir", () => {
  it("returns 'aural' for feedpak and sloppak (CONTRACT C2)", () => {
    expect(featureDir("C:/songs/Foo.feedpak")).toBe("aural");
    expect(featureDir("C:/songs/Foo.sloppak")).toBe("aural");
    expect(featureDir("/data/songs/minimal.sloppak")).toBe("aural");
  });

  it("returns 'features' for legacy .auralsong and everything else", () => {
    expect(featureDir("C:/songs/Foo.auralsong")).toBe("features");
    expect(featureDir("C:/songs/Foo")).toBe("features");
  });
});

describe("packKind.packDisplayName", () => {
  it("strips directory and pack extension", () => {
    expect(packDisplayName("C:/songs/Minimal_Sloppak.sloppak")).toBe("Minimal Sloppak");
    expect(packDisplayName("/data/songs/Cool_Song.feedpak")).toBe("Cool Song");
    expect(packDisplayName("C:/songs/Legacy_Tune.auralsong")).toBe("Legacy Tune");
  });

  it("strips the ingest_ prefix and normalizes underscores", () => {
    expect(packDisplayName("D:/x/ingest_my_track.feedpak")).toBe("my track");
  });

  it("tolerates a trailing separator (directory-form pack)", () => {
    expect(packDisplayName("C:/songs/Minimal_Sloppak.sloppak/")).toBe("Minimal Sloppak");
  });

  it("falls back to the basename when stripping leaves nothing", () => {
    expect(packDisplayName("C:/songs/.feedpak")).toBe(".feedpak");
  });
});
