//! feedpak `manifest.yaml` parsing — the second native container format.
//!
//! Stage 2 of migrating the native format from `.auralsong` (manifest.json) to
//! feedpak. This reader lives alongside the existing [`crate::manifest`] reader;
//! a later cutover stage removes the old path.
//!
//! A feedpak is a directory (`<song>.feedpak/`) or a zip with a `manifest.yaml`
//! index (spec v1.11.0, schemas vendored in `packages/feedpak/schemas/`). The
//! parser follows feedpak's forward-compatibility rule: every documented field
//! is typed, every *un*documented top-level key is preserved verbatim in
//! [`FeedpakManifest::extra`] via `#[serde(flatten)]`. The AuralPrimer
//! extension keys (`aural_*`, snake_case) are strongly typed where we consume
//! them but, being captured by named fields rather than `extra`, are likewise
//! preserved.

use serde::Deserialize;
use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::Path;

/// One arrangement entry (`arrangements[]`) from the manifest.
///
/// Only the fields AuralPrimer reads are typed; `additionalProperties: true`
/// in the schema means anything else is preserved in [`Self::extra`].
#[derive(Debug, Deserialize, Clone, Default)]
pub struct FeedpakArrangement {
    pub id: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    #[serde(default)]
    pub file: Option<String>,
    #[serde(default)]
    pub notation: Option<String>,
    #[serde(default)]
    pub tuning: Option<Vec<i64>>,
    #[serde(default)]
    pub capo: Option<i64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_yaml::Value>,
}

/// One stem entry (`stems[]`) from the manifest.
#[derive(Debug, Deserialize, Clone, Default)]
pub struct FeedpakStem {
    pub id: String,
    pub file: String,
    #[serde(default)]
    pub codec: Option<String>,
    /// `default` is `bool | string` per schema; keep it untyped so a string
    /// form ("yes"/"on") does not fail deserialization.
    #[serde(default)]
    pub default: Option<serde_yaml::Value>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_yaml::Value>,
}

/// A parsed feedpak `manifest.yaml`.
///
/// Documented fields are typed; the `#[serde(flatten)] extra` map captures any
/// other top-level key (feedpak's forward-compat rule) so unknown spec
/// additions and unrecognised AuralPrimer extensions round-trip without loss.
#[derive(Debug, Deserialize, Clone, Default)]
pub struct FeedpakManifest {
    /// Format version (semver). Absent is treated as 1.0.0 per spec §5.
    #[serde(default)]
    pub feedpak_version: Option<String>,
    pub title: String,
    pub artist: String,
    pub duration: f64,
    #[serde(default)]
    pub arrangements: Vec<FeedpakArrangement>,
    #[serde(default)]
    pub stems: Vec<FeedpakStem>,
    #[serde(default)]
    pub song_timeline: Option<String>,
    #[serde(default)]
    pub lyrics: Option<String>,
    #[serde(default)]
    pub drum_tab: Option<String>,
    #[serde(default)]
    pub keys: Option<String>,
    #[serde(default)]
    pub harmony: Option<String>,
    #[serde(default)]
    pub vocal_pitch: Option<String>,

    // --- AuralPrimer extension keys (snake_case). Typed so we can consume
    //     them directly; still preserved because they are named fields. ---
    /// Path to the AuralPrimer-transcribed notes MIDI.
    #[serde(default)]
    pub aural_notes_mid: Option<String>,
    /// Directory holding the precomputed spectrogram PNG + geometry.
    #[serde(default)]
    pub aural_spectrogram: Option<String>,
    /// Map of refine role -> candidate path.
    #[serde(default)]
    pub aural_refine_candidates: Option<BTreeMap<String, String>>,
    /// Directory holding benchmark artifacts.
    #[serde(default)]
    pub aural_benchmark: Option<String>,
    /// Embedded pipeline-provenance object (kept opaque here).
    #[serde(default)]
    pub aural_pipeline: Option<serde_yaml::Value>,

    /// Every other top-level key, preserved verbatim (forward-compat rule).
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_yaml::Value>,
}

/// Strip a leading UTF-8 BOM if present.
///
/// serde_yaml, like serde_json, rejects a leading BOM; a manifest written by a
/// BOM-defaulting tool (e.g. PowerShell `Set-Content -Encoding UTF8`) would
/// otherwise silently vanish from the scan.
fn strip_bom(raw: &str) -> &str {
    raw.strip_prefix('\u{feff}').unwrap_or(raw)
}

/// Parse `manifest.yaml` text into a [`FeedpakManifest`].
pub fn parse_feedpak_manifest_yaml(raw: &str) -> Result<FeedpakManifest, String> {
    let raw = strip_bom(raw);
    serde_yaml::from_str(raw).map_err(|e| format!("invalid YAML: {e}"))
}

/// Read and parse `manifest.yaml` from a directory-style `.feedpak`.
pub fn read_dir_feedpak_manifest(dir: &Path) -> Result<FeedpakManifest, String> {
    let manifest_path = dir.join("manifest.yaml");
    let raw = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("read {}: {e}", manifest_path.display()))?;
    parse_feedpak_manifest_yaml(&raw)
}

/// Read and parse `manifest.yaml` from a zip-style `.feedpak`.
pub fn read_zip_feedpak_manifest(zip: &Path) -> Result<FeedpakManifest, String> {
    let raw = read_zip_feedpak_manifest_text(zip)?;
    parse_feedpak_manifest_yaml(&raw)
}

fn read_zip_feedpak_manifest_text(zip: &Path) -> Result<String, String> {
    let f = fs::File::open(zip).map_err(|e| format!("open {}: {e}", zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name("manifest.yaml")
        .map_err(|e| format!("zip missing manifest.yaml: {e}"))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw)
        .map_err(|e| format!("zip read manifest.yaml: {e}"))?;
    Ok(raw)
}

/// Summary of a feedpak the library panel can render, analogous to the
/// AuralSong scan summary.
#[derive(Debug, Clone, Default)]
pub struct FeedpakSummary {
    pub title: String,
    pub artist: String,
    pub duration: f64,
    pub feedpak_version: Option<String>,
    pub arrangement_ids: Vec<String>,
    pub stem_ids: Vec<String>,
    pub has_song_timeline: bool,
    pub has_drum_tab: bool,
    pub has_notes_mid: bool,
    pub has_spectrogram: bool,
    pub has_refine_candidates: bool,
}

impl FeedpakSummary {
    fn from_manifest(m: &FeedpakManifest) -> Self {
        FeedpakSummary {
            title: m.title.clone(),
            artist: m.artist.clone(),
            duration: m.duration,
            feedpak_version: m.feedpak_version.clone(),
            arrangement_ids: m.arrangements.iter().map(|a| a.id.clone()).collect(),
            stem_ids: m.stems.iter().map(|s| s.id.clone()).collect(),
            has_song_timeline: m.song_timeline.is_some(),
            has_drum_tab: m.drum_tab.is_some(),
            has_notes_mid: m.aural_notes_mid.is_some(),
            has_spectrogram: m.aural_spectrogram.is_some(),
            has_refine_candidates: m
                .aural_refine_candidates
                .as_ref()
                .is_some_and(|c| !c.is_empty()),
        }
    }
}

/// Scan a feedpak container (directory or zip) and return its [`FeedpakSummary`].
///
/// A path is treated as a directory feedpak when it is a directory, and as a
/// zip feedpak otherwise (mirrors how the AuralSong scan distinguishes the two
/// container shapes by filesystem kind).
pub fn scan_feedpak(path: &Path) -> Result<FeedpakSummary, String> {
    let manifest = if path.is_dir() {
        read_dir_feedpak_manifest(path)?
    } else {
        read_zip_feedpak_manifest(path)?
    };
    Ok(FeedpakSummary::from_manifest(&manifest))
}

#[cfg(test)]
mod feedpak_tests {
    //! Tests pin the reader against the committed `minimal.feedpak` fixture and
    //! the forward-compatibility rule (unknown top-level keys preserved).

    use super::{parse_feedpak_manifest_yaml, scan_feedpak};
    use std::path::PathBuf;

    /// Path to the repo-root `minimal.feedpak` fixture, relative to this crate
    /// (`crates/auralsong-core`).
    fn minimal_feedpak_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../packages/feedpak/fixtures/minimal.feedpak")
    }

    fn minimal_manifest_yaml() -> String {
        std::fs::read_to_string(minimal_feedpak_dir().join("manifest.yaml"))
            .expect("read minimal.feedpak/manifest.yaml")
    }

    #[test]
    fn parse_minimal_fixture_extracts_documented_fields() {
        let m = parse_feedpak_manifest_yaml(&minimal_manifest_yaml())
            .expect("minimal fixture must parse");

        assert_eq!(m.feedpak_version.as_deref(), Some("1.11.0"));
        assert_eq!(m.title, "Minimal Fixture");
        assert_eq!(m.artist, "AuralPrimer");
        assert_eq!(m.duration, 2.0);

        assert_eq!(m.arrangements.len(), 1);
        let keys = &m.arrangements[0];
        assert_eq!(keys.id, "keys");
        assert_eq!(keys.name.as_deref(), Some("Keys"));
        assert_eq!(keys.kind.as_deref(), Some("piano"));
        assert_eq!(
            keys.notation.as_deref(),
            Some("arrangements/notation_keys.json")
        );

        assert_eq!(m.stems.len(), 1);
        assert_eq!(m.stems[0].id, "keys");
        assert_eq!(m.stems[0].file, "audio/stems/keys.wav");

        assert_eq!(m.song_timeline.as_deref(), Some("song_timeline.json"));
        assert_eq!(m.aural_notes_mid.as_deref(), Some("aural/notes.mid"));
    }

    #[test]
    fn unknown_top_level_keys_are_preserved() {
        // Forward-compat: a future spec key and an unrecognised aural_* key
        // must survive in `extra` rather than being dropped or erroring.
        let raw = r#"
feedpak_version: 1.11.0
title: Extra Keys
artist: AuralPrimer
duration: 1.0
arrangements:
- id: keys
  notation: arrangements/notation_keys.json
stems:
- id: keys
  file: audio/stems/keys.wav
future_spec_key: hello
aural_unknown_future: aural/whatever
"#;
        let m = parse_feedpak_manifest_yaml(raw).expect("manifest with extra keys must parse");
        assert!(
            m.extra.contains_key("future_spec_key"),
            "unknown spec key must be preserved in extra"
        );
        assert!(
            m.extra.contains_key("aural_unknown_future"),
            "unrecognised aural_* key must be preserved in extra"
        );
        assert_eq!(
            m.extra.get("future_spec_key").and_then(|v| v.as_str()),
            Some("hello")
        );
    }

    #[test]
    fn typed_aural_keys_do_not_leak_into_extra() {
        // The strongly-typed aural_* fields are captured by named fields, so
        // they must NOT also appear in `extra`.
        let m = parse_feedpak_manifest_yaml(&minimal_manifest_yaml())
            .expect("minimal fixture must parse");
        assert!(!m.extra.contains_key("aural_notes_mid"));
        assert!(!m.extra.contains_key("title"));
    }

    #[test]
    fn parse_feedpak_strips_utf8_bom() {
        let bom = format!("\u{feff}{}", minimal_manifest_yaml());
        let m = parse_feedpak_manifest_yaml(&bom).expect("BOM-prefixed manifest must parse");
        assert_eq!(m.title, "Minimal Fixture");
    }

    #[test]
    fn parse_feedpak_rejects_invalid_yaml() {
        // A mapping that is missing the required `title`/`artist`/`duration`
        // must surface as an error rather than silently defaulting.
        let result = parse_feedpak_manifest_yaml("title: Only Title\n");
        assert!(result.is_err(), "missing required fields must error");
        assert!(
            result.unwrap_err().contains("invalid YAML"),
            "error must explain why"
        );
    }

    #[test]
    fn scan_feedpak_on_minimal_fixture_dir_returns_summary() {
        let summary = scan_feedpak(&minimal_feedpak_dir()).expect("scan must succeed");

        assert_eq!(summary.title, "Minimal Fixture");
        assert_eq!(summary.artist, "AuralPrimer");
        assert_eq!(summary.duration, 2.0);
        assert_eq!(summary.feedpak_version.as_deref(), Some("1.11.0"));
        assert_eq!(summary.arrangement_ids, vec!["keys".to_string()]);
        assert_eq!(summary.stem_ids, vec!["keys".to_string()]);
        assert!(summary.has_song_timeline);
        assert!(summary.has_notes_mid);
        assert!(!summary.has_drum_tab);
        assert!(!summary.has_spectrogram);
        assert!(!summary.has_refine_candidates);
    }
}
