//! Pack-kind classification shared across both Tauri shells.
//!
//! Both apps historically dispatched on the literal string `".feedpak"`
//! scattered across their `lib.rs`. AuralPrimer now also opens `.sloppak`
//! packs — [Slopsmith](https://github.com/mikey0000/slopsmith)'s song format,
//! feedpak's sibling: same container model (dir or zip, `manifest.yaml` index,
//! unknown keys preserved), identical required manifest fields. The Rust
//! [`crate::feedpak::FeedpakManifest`] parses a sloppak manifest as-is.
//!
//! This module is the ONE place the `.sloppak` / `.feedpak` / `.auralsong`
//! extension strings live in Rust. Everything else calls [`pack_kind`],
//! [`is_manifest_pack`], or [`is_auralsong`].

use std::path::Path;

/// The three native container formats AuralPrimer understands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PackKind {
    /// `.feedpak` — the primary native format (`manifest.yaml`, `aural/`).
    Feedpak,
    /// `.sloppak` — Slopsmith's format; feedpak's sibling (`manifest.yaml`,
    /// artifacts under `aural/`, drum tab shared at pack root).
    Sloppak,
    /// `.auralsong` — the legacy format (`manifest.json`, `features/`).
    AuralSong,
}

/// Strip trailing path separators so a path like `song.sloppak/` (or a
/// backslash-terminated Windows form) still classifies. We only look at the
/// extension, case-insensitively.
fn trimmed(path: &str) -> &str {
    path.trim_end_matches(['/', '\\'])
}

/// Classify a container path by extension (case-insensitive). Returns `None`
/// for anything that is not one of the three known formats.
pub fn pack_kind(path: &str) -> Option<PackKind> {
    let trimmed = trimmed(path);
    // Case-insensitive extension match without allocating for the common case:
    // fold only the extension via `Path::extension`.
    let ext = Path::new(trimmed).extension()?.to_str()?;
    if ext.eq_ignore_ascii_case("feedpak") {
        Some(PackKind::Feedpak)
    } else if ext.eq_ignore_ascii_case("sloppak") {
        Some(PackKind::Sloppak)
    } else if ext.eq_ignore_ascii_case("auralsong") {
        Some(PackKind::AuralSong)
    } else {
        None
    }
}

/// True for a manifest-driven pack (feedpak OR sloppak) — the packs that carry
/// a `manifest.yaml` index and stash AuralPrimer artifacts under `aural/`.
/// This is the check that used to be `ends_with(".feedpak")`.
pub fn is_manifest_pack(path: &str) -> bool {
    matches!(pack_kind(path), Some(PackKind::Feedpak | PackKind::Sloppak))
}

/// True for the legacy `.auralsong` (manifest.json / `features/`) format.
pub fn is_auralsong(path: &str) -> bool {
    matches!(pack_kind(path), Some(PackKind::AuralSong))
}

/// True for any supported container (manifest pack or legacy auralsong).
pub fn is_supported_pack(path: &str) -> bool {
    pack_kind(path).is_some()
}

#[cfg(test)]
mod container_tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn pack_kind_by_extension() {
        assert_eq!(pack_kind("x.feedpak"), Some(PackKind::Feedpak));
        assert_eq!(pack_kind("x.sloppak"), Some(PackKind::Sloppak));
        assert_eq!(pack_kind("x.auralsong"), Some(PackKind::AuralSong));
        assert_eq!(pack_kind("x.zip"), None);
        assert_eq!(pack_kind("x"), None);
        assert_eq!(pack_kind(""), None);
    }

    #[test]
    fn pack_kind_is_case_insensitive() {
        assert_eq!(pack_kind("Song.FEEDPAK"), Some(PackKind::Feedpak));
        assert_eq!(pack_kind("Song.SlopPak"), Some(PackKind::Sloppak));
        assert_eq!(pack_kind("SONG.AuralSong"), Some(PackKind::AuralSong));
    }

    #[test]
    fn pack_kind_handles_trailing_separators() {
        assert_eq!(pack_kind("dir/song.sloppak/"), Some(PackKind::Sloppak));
        assert_eq!(pack_kind("dir\\song.feedpak\\"), Some(PackKind::Feedpak));
        assert_eq!(
            pack_kind("C:/songs/My Song.sloppak/"),
            Some(PackKind::Sloppak)
        );
    }

    #[test]
    fn is_manifest_pack_covers_feedpak_and_sloppak_only() {
        assert!(is_manifest_pack("x.feedpak"));
        assert!(is_manifest_pack("x.sloppak"));
        assert!(!is_manifest_pack("x.auralsong"));
        assert!(!is_manifest_pack("x.zip"));
    }

    #[test]
    fn is_auralsong_and_supported() {
        assert!(is_auralsong("x.auralsong"));
        assert!(!is_auralsong("x.feedpak"));
        assert!(!is_auralsong("x.sloppak"));

        assert!(is_supported_pack("x.feedpak"));
        assert!(is_supported_pack("x.sloppak"));
        assert!(is_supported_pack("x.auralsong"));
        assert!(!is_supported_pack("x.zip"));
    }

    /// Path to the repo-root `minimal.sloppak` fixture, relative to this crate.
    fn minimal_sloppak_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../packages/sloppak/fixtures/minimal.sloppak")
    }

    /// The existing feedpak reader must parse a real sloppak manifest as-is:
    /// arrangements use `file` (not `notation`), unknown `slopsmith_version`
    /// survives in `extra`, and the summary path yields the right ids.
    #[test]
    fn scan_minimal_sloppak_via_feedpak_reader() {
        let dir = minimal_sloppak_dir();
        assert!(
            dir.join("manifest.yaml").is_file(),
            "fixture missing: {}",
            dir.display()
        );

        let summary = crate::feedpak::scan_feedpak(&dir).expect("scan sloppak fixture");
        assert_eq!(summary.title, "Minimal Sloppak");
        assert_eq!(summary.artist, "Slopsmith");
        assert_eq!(summary.duration, 4.0);
        assert_eq!(
            summary.arrangement_ids,
            vec!["lead".to_string(), "bass".to_string()]
        );
        assert_eq!(
            summary.stem_ids,
            vec![
                "guitar".to_string(),
                "bass".to_string(),
                "drums".to_string(),
                "full".to_string(),
            ]
        );
        assert!(summary.has_drum_tab, "drum_tab manifest key present");

        // Confirm the raw manifest parses and the sloppak arrangement uses
        // `file` (not `notation`), plus the unknown key round-trips.
        let manifest =
            crate::feedpak::read_dir_feedpak_manifest(&dir).expect("parse sloppak manifest");
        assert_eq!(manifest.arrangements.len(), 2);
        assert_eq!(
            manifest.arrangements[0].file.as_deref(),
            Some("arrangements/lead.json")
        );
        assert!(manifest.arrangements[0].notation.is_none());
        assert_eq!(manifest.arrangements[0].capo, Some(2));
        assert!(
            manifest.extra.contains_key("slopsmith_version"),
            "unknown sloppak key must survive in extra"
        );
        assert_eq!(
            manifest
                .extra
                .get("slopsmith_version")
                .and_then(|v| v.as_str()),
            Some("0.9.0")
        );
    }
}
